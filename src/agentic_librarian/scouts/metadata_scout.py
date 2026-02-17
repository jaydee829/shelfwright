"""Grab additional metadata to flesh out reading list"""

import json
import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress

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


# --- CORE ABSTRACTIONS ---


class BaseScout(ABC):
    """Abstract base class for all metadata scouts."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.source_name = self.__class__.__name__

    @abstractmethod
    def search(self, title: str, author: str, **kwargs) -> dict:
        """
        Standardized search interface.
        Returns a dictionary of metadata or an empty dict if not found.
        """
        pass

    def _extract_year(self, date_str: str | None) -> int | None:
        """Helper to extract a 4-digit year from a string robustly."""
        if not date_str:
            return None
        match = re.search(r"\b(\d{4})\b", str(date_str))
        if match:
            with suppress(ValueError):
                return int(match.group(1))
        return None


class APIScout(BaseScout):
    """Abstract base class for scouts interacting with structured REST/GraphQL APIs."""

    def __init__(self, api_key: str = None, timeout: int = 10):
        super().__init__(api_key)
        self.timeout = timeout

    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        """Shared request logic with error handling."""
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Warning: {self.source_name} request failed: {e}")
            return {}


class LLMScout(BaseScout):
    """Abstract base class for scouts using LLMs for unstructured data."""

    def __init__(self, api_key: str = None, model_name: str = "gemini-2.0-flash"):
        # Fallback to GOOGLE_SEARCH_API_KEY if no specific key provided
        key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not key:
            raise ValueError(f"{self.__class__.__name__} requires a Google API key.")
        super().__init__(key)
        self._client = genai.Client(api_key=self.api_key)
        self.model_name = model_name

    def _safe_extract_json(self, response_text: str, title: str, author: str, retry_count: int = 0) -> dict | None:
        """Cleans and parses LLM JSON output with descriptive error logging."""
        text = response_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?|\n?```$", "", text, flags=re.MULTILINE)

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("LLM returned non-dictionary JSON")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"Failed to parse LLM JSON for '{title}' by '{author}'."
            if retry_count == 0:
                print(f"Warning: {msg} Retrying... Error: {e}")
                return None
            raise ValueError(f"{msg} Output did not follow valid JSON after retry: {text[:100]}...") from e


# --- IMPLEMENTATIONS ---


class GoogleBooksScout(APIScout):
    """Scout for Google Books API."""

    def __init__(self, api_key: str = None):
        key = api_key or os.environ.get("GOOGLE_BOOKS_API_KEY")
        super().__init__(key)

    def search(self, title: str, author: str, **kwargs) -> dict:
        base_url = "https://www.googleapis.com/books/v1/volumes"
        query = f"intitle:{title} inauthor:{author}"
        params = {
            "q": query,
            "maxResults": 1,
            "langRestrict": "en",
            "printType": "books",
        }
        if self.api_key:
            params["key"] = self.api_key

        data = self._make_request("GET", base_url, params=params)
        if not data or "items" not in data:
            return {}

        book = data["items"][0]["volumeInfo"]
        isbn_13 = ""
        identifiers = book.get("industryIdentifiers", [])
        for id_obj in identifiers:
            if id_obj.get("type") == "ISBN_13":
                isbn_13 = id_obj.get("identifier")
                break

        contributors = [{"name": a, "role": "Author"} for a in book.get("authors", [])]

        return {
            "google_id": data["items"][0]["id"],
            "isbn_13": isbn_13,
            "title": book.get("title"),
            "contributors": contributors,
            "published_date": book.get("publishedDate"),
            "description": book.get("description", ""),
            "page_count": book.get("pageCount"),  # Default None
            "genres": book.get("categories", []),
            "average_rating": book.get("averageRating"),
            "thumbnail": book.get("imageLinks", {}).get("thumbnail"),
        }


class HardcoverScout(APIScout):
    """Scout for Hardcover.app GraphQL API."""

    def __init__(self, api_key: str = None):
        key = api_key or os.environ.get("HARDCOVER_API_KEY")
        super().__init__(key)

    def search(self, title: str, author: str, **kwargs) -> dict:
        format_val = kwargs.get("format", "Paperback")
        url = "https://api.hardcover.app/v1/graphql"
        if not self.api_key:
            return {}

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
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
                        author_role {
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
        variables = {"title": title, "format": format_val}
        data = self._make_request("POST", url, headers=headers, json={"query": query, "variables": variables})

        editions = data.get("data", {}).get("editions", [])
        if not editions:
            return {}

        moods, genres = [], []
        audio_length, pages = None, None
        selected_edition = None

        for edition in editions:
            raw_moods = edition.get("book", {}).get("moods", [])
            moods.extend([m.get("tagSlug") for m in raw_moods])
            raw_genres = edition.get("book", {}).get("genres", [])
            genres.extend([g.get("tagSlug") for g in raw_genres])
            audio_length = edition.get("audio_seconds") or audio_length
            pages = edition.get("pages") or pages
            if "audiobook" in format_val.lower() and edition.get("audio_seconds"):
                selected_edition = edition
                break
            if "audiobook" not in format_val.lower() and edition.get("pages"):
                selected_edition = edition
                break

        # Fallback if no perfect format match found
        if not selected_edition:
            selected_edition = editions[0]

        book = selected_edition.get("book", {})
        contributors = []
        for c in book.get("contributions", []):
            name = c.get("author", {}).get("name")
            role = c.get("author_role", {}).get("name") or "Author"
            if name:
                contributors.append({"name": name, "role": role})

        edition_release_date = selected_edition.get("release_date")
        original_release_date = book.get("release_date") or edition_release_date

        return {
            "title": selected_edition.get("title"),
            "contributors": contributors,
            "edition_format": selected_edition.get("edition_format"),
            "page_count": pages,
            "publication_date": edition_release_date,
            "original_publication_date": original_release_date,
            "isbn_13": selected_edition.get("isbn_13"),
            "moods": set(moods),
            "genres": set(genres),
            "description": book.get("description", ""),
            "audio_minutes": audio_length // 60 if audio_length else None,
        }


class AudiobookScout(LLMScout):
    """Scouts audiobook metadata from Audible using LLM extraction."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        return self.extract_metadata_with_gemini(title, author)

    def search_audible_link(self, title: str) -> str | None:
        search_engine_id = os.environ.get("SEARCH_ENGINE_ID")
        if not search_engine_id:
            raise ValueError("Search Engine ID not set.")

        service = build("customsearch", "v1", developerKey=self.api_key)
        search_results = (
            service.cse().list(q=f"site:audible.com {title} audiobook", cx=search_engine_id, num=1).execute()
        )

        if "items" in search_results and len(search_results["items"]) > 0:
            return search_results["items"][0]["link"]
        return None

    def fetch_page_content(self, title: str) -> str:
        url = self.search_audible_link(title)
        if not url:
            raise ValueError(f"No Audible link found for title: {title}")

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()
        return soup.get_text()

    def extract_metadata_with_gemini(self, title: str, author: str = "Unknown") -> dict:
        text_content = self.fetch_page_content(title)
        prompt = f"""
        Extract the following fields from the text. Return ONLY a raw JSON object.
        - title (string)
        - narrator (string)
        - length_minutes (int): convert "X hrs Y mins" to total minutes
        Input: {text_content[:30000]}

        CRITICAL: If the information is not present in the text, return an empty object {{}}.
        DO NOT hallucinate or guess.
        """
        response = self._client.models.generate_content(model=self.model_name, contents=prompt)
        data = self._safe_extract_json(response.text, title, author)
        if data is None:
            response = self._client.models.generate_content(model=self.model_name, contents=prompt + "\nJSON ONLY.")
            data = self._safe_extract_json(response.text, title, author, retry_count=1)
        return data or {}


class DirectKnowledgeScout(LLMScout):
    """Scouts audiobook metadata using direct LLM knowledge and search grounding."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        return self.scout_audiobook(title, author)

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

        CRITICAL: Use the provided search results to verify these facts.
        If the information is not definitively found, return an empty object {{}}.
        DO NOT guess or provide info from your internal memory if it is not verified by search.
        """

        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )

        data = self._safe_extract_json(response.text, title, author)
        if data is None:
            prompt += "\n\nSTRICT: Return valid JSON ONLY."
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"tools": [{"google_search": {}}] if use_grounding else []},
            )
            data = self._safe_extract_json(response.text, title, author, retry_count=1)

        return data or {}


# --- THE MANAGER ---


class ScoutManager:
    """Orchestrates multiple scouts and merges their results."""

    def __init__(self):
        self.scouts: list[tuple[BaseScout, int]] = []

    def register_scout(self, scout: BaseScout, priority: int = 1):
        """Registers a scout with a given priority (lower = higher priority)."""
        self.scouts.append((scout, priority))
        self.scouts.sort(key=lambda x: x[1])

    def enrich(self, title: str, author: str, format: str = "Paperback") -> dict:
        """Aggregates metadata from all registered scouts."""
        merged_data = {
            "title": title,
            "contributors": [{"name": author, "role": "Author"}],
            "genres": set(),
            "moods": set(),
            "source_priority": [],
        }

        for scout, _ in self.scouts:
            # Skip audiobook-only scouts if format is not audiobook
            if isinstance(scout, AudiobookScout | DirectKnowledgeScout) and "audiobook" not in format.lower():
                continue

            res = scout.search(title, author, format=format)
            if not res:
                continue

            merged_data["source_priority"].append(scout.source_name)

            # Prioritized Merge Logic:
            # Only update fields if they are currently empty (None, "", 0, or default title)
            # Since we iterate in priority order, the first valid value found stays.

            if isbn := res.get("isbn_13"):
                merged_data["isbn_13"] = merged_data.get("isbn_13") or isbn

            if merged_data["title"] == title and (t := res.get("title")):
                merged_data["title"] = t

            # Additive fields (Sets)
            merged_data["genres"].update(res.get("genres", []))
            merged_data["moods"].update(res.get("moods", []))

            # Merge Contributors (Maintain uniqueness by name + role)
            existing_contributors = {(c["name"], c["role"]) for c in merged_data["contributors"]}
            for new_c in res.get("contributors", []):
                if (new_c["name"], new_c["role"]) not in existing_contributors:
                    merged_data["contributors"].append(new_c)
                    existing_contributors.add((new_c["name"], new_c["role"]))

            # Single-value fields loop
            for key in [
                "page_count",
                "description",
                "average_rating",
                "thumbnail",
                "audio_minutes",
                "publication_date",
            ]:
                if val := res.get(key):
                    merged_data[key] = merged_data.get(key) or val

            # Special handling for publication year
            if not merged_data.get("original_publication_year"):
                orig_date = res.get("original_publication_date") or res.get("published_date")
                if year := scout._extract_year(orig_date):
                    merged_data["original_publication_year"] = year

        # Final clean up
        merged_data["genres"] = list(merged_data["genres"])
        merged_data["moods"] = list(merged_data["moods"])
        return merged_data
