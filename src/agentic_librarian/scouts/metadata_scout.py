"""Grab additional metadata to flesh out reading list"""

import json
import os
import re
import time
from contextlib import suppress

import mlflow
import requests
from bs4 import BeautifulSoup
from google import genai
from googleapiclient.discovery import build

# Load environment variables from .env if present (for local dev)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def fetch_google_books_metadata(title: str, author: str, api_key: str = None) -> dict:
    """
    Fetches book metadata from Google Books API using Title and Author.

    Args:
        title (str): The book title.
        author (str): The book author.
        api_key (str): Optional Google API Key (recommended for higher rate limits).

    Returns:
        dict: A dictionary containing clean metadata or None if not found.
    """
    base_url = "https://www.googleapis.com/books/v1/volumes"

    query = f"intitle:{title} inauthor:{author}"

    params = {
        "q": query,
        "maxResults": 1,  # Best match
        "langRestrict": "en",  # Restrict to English
        "printType": "books",
    }

    if api_key:
        params["key"] = api_key

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()  # Raise error for 4xx/5xx responses
        data = response.json()

        # Check if items exist
        if "items" not in data:
            print(f"Warning: No results found for '{title}' by {author}")
            return None

        # Extract the first result
        book = data["items"][0]["volumeInfo"]

        isbn_13 = ""
        if "ISBN_13" in book.get("industryIdentifiers", [{}])[0].get("type", ""):
            isbn_13 = book["industryIdentifiers"][0]["identifier"]

        return {
            "google_id": data["items"][0]["id"],
            "ISBN_13": isbn_13,
            "title": book.get("title"),
            "authors": book.get("authors", []),
            "published_date": book.get("publishedDate"),
            "description": book.get("description", ""),
            "page_count": book.get("pageCount", 0),
            "genres": book.get("categories", []),  # genres
            "average_rating": book.get("averageRating"),
            "thumbnail": book.get("imageLinks", {}).get("thumbnail"),
        }

    except requests.exceptions.RequestException as e:
        print(f"API Request failed: {e}")
        return None


def fetch_hardcover_metadata(title: str, author: str, format: str, api_key: str = None) -> dict:
    """Get metadata from Hardcover API

    Args:
        title (str): The book title.
        author (str): The book author.
        api_key (str, optional): API key for authentication. Defaults to None.

    Returns:
        dict: A dictionary containing Hardcover metadata.
    """
    url = "https://api.hardcover.app/v1/graphql"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise ValueError("Hardcover API key not set")

    query = """
        query GetEditionsFromTitleFormat($title: String!, $format: String!) {
            editions(
                where: {book: {title: {_eq: $title}}, country: {name: {_eq: "United States of America"}}, edition_format: {_eq: $format}}
            ) {
                isbn_13
                title
                book {
                contributions {
                    author {
                    name
                    }
                }
                moods: cached_tags(path: "Mood")
                genres: cached_tags(path: "Genre")
                description
                pages
                audio_seconds
                release_date
                }
                pages
                audio_seconds
                release_date
            }
            }
    """

    variables = {"title": title, "format": format}

    try:
        response = requests.post(
            url,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        # Return the first book found
        editions = data.get("data", {}).get("editions", [])
        moods = []
        genres = []
        audio_length = None
        pages = None
        if not editions:
            print(f"Warning: No Hardcover results found for '{title}' by {author}")
            return {}
        for edition in editions:
            raw_moods = edition.get("book", {}).get("moods", [])
            moods.extend([raw_mood.get("tagSlug") for raw_mood in raw_moods])
            raw_genres = edition.get("book", {}).get("genres", [])
            genres.extend([raw_genre.get("tagSlug") for raw_genre in raw_genres])
            audio_length = edition.get("audio_seconds") or audio_length
            pages = edition.get("pages") or pages
            if "audiobook" in format.lower() and edition.get("audio_seconds"):
                book = edition.get("book", {})
                break
            if "audiobook" not in format.lower() and edition.get("pages"):
                book = edition.get("book", {})
                break

        authors = []
        for contrib in edition.get("book", {}).get("contributions", []):
            author = contrib.get("author", {}).get("name")
            if author:
                authors.append(author)

        edition_release_date = edition.get("release_date")
        original_release_date = book.get("release_date") or edition_release_date

        return {
            "title": edition.get("title"),
            "authors": authors,
            "edition_format": edition.get("edition_format"),
            "page_count": pages,
            "publication_date": edition_release_date,
            "original_publication_date": original_release_date,
            "isbn_13": edition.get("isbn_13"),
            "moods": set(moods),
            "genres": set(genres),
            "description": book.get("description", ""),
            "audio_minutes": audio_length // 60 if audio_length else None,
        }
    except requests.RequestException as e:
        print(f"Hardcover API request failed: {e}")
        return {}


# Audible package requires authentication, no open API available
# iTunes API is limited and often returns incomplete data
# scraping HTML tags is fragile, so I will use an LLM-powered approach


class AudiobookScout:
    """Scouts audiobook metadata from Audible using LLM extraction."""

    def __init__(self):
        self._API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._API_KEY:
            raise ValueError(
                "Google Search API key not set. Please set the GOOGLE_SEARCH_API_KEY environment variable."
            )
        self._client = genai.Client(api_key=self._API_KEY)

    def search_audible_link(self, title: str) -> str | None:
        """Finds the Audible URL using Google Custom Search."""
        search_engine_id = os.environ.get("SEARCH_ENGINE_ID")
        if not search_engine_id:
            raise ValueError("Search Engine ID not set. Please set the SEARCH_ENGINE_ID environment variable.")

        service = build("customsearch", "v1", developerKey=self._API_KEY)
        search_results = (
            service.cse().list(q=f"site:audible.com {title} audiobook", cx=search_engine_id, num=1).execute()
        )

        if "items" in search_results and len(search_results["items"]) > 0:
            return search_results["items"][0]["link"]
        return None

    def fetch_page_content(self, title: str) -> str:
        """Fetches raw HTML (mimicking a browser)."""

        url = self.search_audible_link(title)
        if not url:
            raise ValueError(f"No Audible link found for title: {title}")

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers)

        # strip scripts/styles to save tokens
        soup = BeautifulSoup(response.content, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()

        return soup.get_text()

    def extract_metadata_with_gemini(self, title: str) -> dict:
        """Uses Gemini 2.5 Flash to extract data."""
        text_content = self.fetch_page_content(title)

        # Define the JSON schema in the prompt
        prompt = f"""
        You are a data extraction agent. Extract the following fields from the text below.
        Return ONLY a raw JSON object. Do not use Markdown formatting.

        Fields required:
        - title (string)
        - narrator (string): clean name only
        - length_minutes (int): convert "X hrs Y mins" to total minutes

        Input Text:
        {text_content[:30000]}
        """

        response = self._client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)

        text = response.text.strip()
        if text.startswith("```json"):
            text = text.replace("```json", "").replace("```", "")
        return json.loads(text)


class DirectKnowledgeScout:
    """Scouts audiobook metadata using direct LLM knowledge and search grounding."""

    def __init__(self):
        self._API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._API_KEY:
            raise ValueError(
                "Google Search API key not set. Please set the GOOGLE_SEARCH_API_KEY environment variable."
            )
        self._client = genai.Client(api_key=self._API_KEY)

    def scout_audiobook(self, title: str, author: str) -> dict:
        """Uses Gemini with search grounding to find audiobook details."""
        prompt = f"""
        Find the official audiobook duration and narrator for:
        Title: {title}
        Author: {author}

        Return ONLY a raw JSON object with:
        - title (string)
        - narrator (string)
        - audio_minutes (int)
        """

        # Using tools for search grounding (simulated by prompt since direct tool support varies by SDK version)
        # Note: If the SDK supports tools=[{ 'google_search': {} }], add it here.
        # For now, we rely on the model's knowledge/search capabilities.
        response = self._client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"tools": [{"google_search": {}}] if os.environ.get("USE_SEARCH_GROUNDING") == "1" else []},
        )

        text = response.text.strip()
        if text.startswith("```json"):
            text = text.replace("```json", "").replace("```", "")
        return json.loads(text)


class MultiSourceScout:
    """Consolidates book metadata enrichment from Google Books, Hardcover, and Audible."""

    def __init__(self, google_api_key: str = None, hardcover_api_key: str = None):
        """
        Initialize the scout with API keys.

        Args:
            google_api_key (str): Google Books API key.
            hardcover_api_key (str): Hardcover API key.
        """
        self.google_api_key = google_api_key or os.environ.get("GOOGLE_BOOKS_API_KEY")
        self.hardcover_api_key = hardcover_api_key or os.environ.get("HARDCOVER_API_KEY")
        self.audio_scout = AudiobookScout()
        self.direct_scout = DirectKnowledgeScout()

    def scout_metadata(self, title: str, author: str, format: str = "Paperback") -> dict:
        """
        Scouts metadata from multiple sources and merges them.

        Args:
            title (str): The book title.
            author (str): The book author.
            format (str): The book format (e.g., "Paperback", "Audiobook").

        Returns:
            dict: Unified metadata dictionary.
        """
        # Fetch from standard sources
        google_data = fetch_google_books_metadata(title, author, api_key=self.google_api_key) or {}
        hardcover_data = fetch_hardcover_metadata(title, author, format=format, api_key=self.hardcover_api_key) or {}

        # Handle Audiobooks with dual-pathway scouting
        audio_minutes = hardcover_data.get("audio_minutes")
        pathway_a_results = {}
        pathway_b_results = {}

        if "audiobook" in format.lower():
            # Setup MLFlow experiment
            mlflow.set_experiment("audiobook_scouting_comparison")

            with mlflow.start_run(run_name=f"scout_{title}_{int(time.time())}"):
                mlflow.log_params({"title": title, "author": author, "hardcover_minutes": audio_minutes})

                # Pathway A: Scraping
                start_a = time.time()
                try:
                    pathway_a_results = self.audio_scout.extract_metadata_with_gemini(title)
                    mlflow.log_metric("pathway_a_latency", time.time() - start_a)
                    mlflow.log_metric("pathway_a_minutes", pathway_a_results.get("length_minutes", 0))
                except Exception as e:
                    mlflow.log_param("pathway_a_error", str(e))

                # Pathway B: Direct Knowledge
                start_b = time.time()
                try:
                    pathway_b_results = self.direct_scout.scout_audiobook(title, author)
                    mlflow.log_metric("pathway_b_latency", time.time() - start_b)
                    mlflow.log_metric("pathway_b_minutes", pathway_b_results.get("audio_minutes", 0))
                except Exception as e:
                    mlflow.log_param("pathway_b_error", str(e))

                # Selection logic: Prefer Audible (Pathway A) if available, fallback to Direct (Pathway B)
                # Compare and flag disparity
                audible_min = pathway_a_results.get("length_minutes")
                direct_min = pathway_b_results.get("audio_minutes")

                if audible_min and direct_min and abs(audible_min - direct_min) > 10:
                    mlflow.log_param("disparity_warning", True)
                    print(f"Warning: Disparity detected between Audible ({audible_min}) and Direct ({direct_min})")

                audio_minutes = audible_min or direct_min or audio_minutes

        # Safely handle dates
        pub_date_str = hardcover_data.get("publication_date") or google_data.get("published_date")
        orig_date_str = hardcover_data.get("original_publication_date") or google_data.get("published_date")

        # Extract year for original_publication_year (Work model expects Int)
        original_year = self._extract_year(orig_date_str)

        merged = {
            "title": hardcover_data.get("title") or google_data.get("title") or title,
            "authors": hardcover_data.get("authors") or google_data.get("authors") or [author],
            "isbn_13": hardcover_data.get("isbn_13") or google_data.get("ISBN_13"),
            "page_count": hardcover_data.get("page_count") or google_data.get("page_count"),
            "description": hardcover_data.get("description") or google_data.get("description"),
            "genres": list(set(list(hardcover_data.get("genres", [])) + google_data.get("genres", []))),
            "moods": list(hardcover_data.get("moods", [])),  # Only Hardcover has moods
            "average_rating": google_data.get("average_rating"),
            "thumbnail": google_data.get("thumbnail"),
            "publication_date": pub_date_str,
            "original_publication_year": original_year,
            "audio_minutes": audio_minutes,
            "source_priority": ["hardcover", "google_books", "audible"],
        }

        return merged

    def _extract_year(self, date_str: str | None) -> int | None:
        """Helper to extract a 4-digit year from a string robustly.

        Handles ISO (YYYY-MM-DD), Year only (YYYY), and descriptive dates (Jan 2023).

        Args:
            date_str (str | None): The date string to parse.

        Returns:
            int | None: The 4-digit year as an integer, or None if not found.
        """
        if not date_str:
            return None

        # Look for first sequence of exactly 4 digits
        match = re.search(r"(\d{4})", str(date_str))
        if match:
            with suppress(ValueError):
                return int(match.group(1))

        return None
