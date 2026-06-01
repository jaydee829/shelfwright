"""Grab additional metadata to flesh out reading list"""

import json
import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress

import requests
from agentic_librarian.llm_retry import genai_http_options
from bs4 import BeautifulSoup
from google import genai
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Load environment variables from .env if present (for local dev)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# --- CORE ABSTRACTIONS ---

# Retry transient HTTP failures (429 + 5xx) with bounded exponential backoff. Google Books
# rate-limits the per-discovery enrichment burst with 429s (REC-016/REC-020); without this, a burst
# of discoveries silently loses all REST metadata. Bounded (total=3, ~1+2+4s) and Retry-After is NOT
# honored on purpose: a single rate-limited lookup must not stall an interactive recommendation by
# the server-dictated delay — transient blips recover, sustained limits degrade gracefully (skip).
_API_RETRY = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "POST"}),
    respect_retry_after_header=False,
)


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
        # A Session with a retrying adapter so transient 429/5xx (e.g. the Google Books enrichment
        # burst) back off and retry instead of silently dropping a book's metadata.
        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=_API_RETRY)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        """Shared request logic with error handling."""
        try:
            response = self._session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Warning: {self.source_name} request failed: {e}")
            return {}


class LLMScout(BaseScout):
    """Abstract base class for scouts using LLMs for unstructured data."""

    def __init__(self, api_key: str = None, model_name: str = None):
        # Fallback to GOOGLE_SEARCH_API_KEY if no specific key provided
        key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not key:
            raise ValueError(f"{self.__class__.__name__} requires a Google API key.")
        super().__init__(key)
        # http_options carries the shared transient-error retry so grounded calls ride through
        # 429/5xx demand spikes instead of crashing the run (REC-020).
        self._client = genai.Client(api_key=self.api_key, http_options=genai_http_options())
        # These scouts use Gemini-native Search grounding ({"google_search": {}}), so they need a
        # grounding-capable model. Defaults to gemini-2.5-flash (reliable free-tier grounding);
        # GROUNDING_MODEL/EXPLORER_MODEL override it. (Non-grounding mesh agents use GEMINI_MODEL.)
        self.model_name = (
            model_name or os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"
        )

    def _extract_text(self, response) -> str | None:
        """Return the response text, falling back to concatenated candidate parts.

        Grounded / multi-part responses can leave ``response.text`` empty even though
        the answer is present in the candidate parts.
        """
        if getattr(response, "text", None):
            return response.text
        try:
            parts = response.candidates[0].content.parts or []
        except (AttributeError, IndexError, TypeError):
            return None
        texts = [p.text for p in parts if getattr(p, "text", None)]
        return "".join(texts) if texts else None

    def _safe_extract_json(self, response_text: str, title: str, author: str, retry_count: int = 0) -> dict | None:
        """Cleans and parses LLM JSON output with descriptive error logging."""
        if not response_text:
            # The model can return no text part (e.g. a blocked response). Surface it as
            # a retry rather than crashing on .strip().
            print(f"Warning: empty LLM response for '{title}' by '{author}'.")
            return None

        text = response_text.strip()

        # Pull the JSON payload out of the response, tolerating code fences and any
        # prose a grounded model may add before or after it.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        else:
            block = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if block:
                text = block.group(1).strip()

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
        - narrators (list of strings)
        - length_minutes (int): convert "X hrs Y mins" to total minutes
        Input: {text_content[:30000]}

        CRITICAL: If the information is not present in the text, return an empty object {{}}.
        DO NOT hallucinate or guess.
        """
        response = self._client.models.generate_content(model=self.model_name, contents=prompt)
        data = self._safe_extract_json(self._extract_text(response), title, author)
        if data is None:
            response = self._client.models.generate_content(model=self.model_name, contents=prompt + "\nJSON ONLY.")
            data = self._safe_extract_json(self._extract_text(response), title, author, retry_count=1)

        # Normalize keys
        if data and "narrators" in data:
            data["narrator_names"] = data.pop("narrators")
        if data and "length_minutes" in data:
            data["audio_minutes"] = data.pop("length_minutes")

        return data or {}


class DirectKnowledgeScout(LLMScout):
    """Scouts audiobook metadata using direct LLM knowledge and search grounding."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        return self.scout_audiobook(title, author)

    def scout_audiobook(self, title: str, author: str) -> dict:
        """Uses Gemini with search grounding to find audiobook details."""
        prompt = f"""
        Find the official audiobook duration and narrators for:
        Title: {title}
        Author: {author}

        Return ONLY a raw JSON object with:
        - title (string)
        - narrators (list of strings)
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

        data = self._safe_extract_json(self._extract_text(response), title, author)
        if data is None:
            prompt += "\n\nSTRICT: Return valid JSON ONLY."
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"tools": [{"google_search": {}}] if use_grounding else []},
            )
            data = self._safe_extract_json(self._extract_text(response), title, author, retry_count=1)

        if data and "narrators" in data:
            data["narrator_names"] = data.pop("narrators")

        return data or {}


def _flatten_style_map(data: dict | None) -> dict[str, str]:
    """Coerce a scouted style dict to {attribute: non-empty-string}. The work-style prompt asks the
    model to also list attributes that DIFFER from the author baseline, so a value can come back as a
    nested dict (e.g. {"differences": {"pacing": "..."}}). Hoist one level of nested string values to
    the top level and drop anything that is not a non-empty string (REC-021)."""
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.strip():
                    out.setdefault(sub_key, sub_val.strip())  # top-level value wins on collision
    return out


class StyleScout(LLMScout):
    """Scouts deep style attributes for authors and narrators using LLM knowledge."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        """
        In the context of a work, we might want to scout the style of its primary author,
        the specific style of the work itself, and its narrators.
        """
        style_data = {"author_style": {}, "narrator_styles": {}, "work_style": {}}
        author_baseline = kwargs.get("author_styles", {})

        # 1. Scout Author Style
        style_data["author_style"] = self.scout_author_style(author)

        # 2. Scout Work Specific Style (Informed Scouting, ADR-023). For a new author the
        # DB baseline (author_styles kwarg) is empty, so fall back to the author style we
        # just scouted above rather than scouting the work with no baseline.
        baseline = author_baseline or style_data["author_style"]
        style_data["work_style"] = self.scout_work_style(title, author, author_baseline=baseline)

        # 3. Scout Narrator Styles (if provided in kwargs)
        narrators = kwargs.get("narrators", [])
        for n in narrators:
            style_data["narrator_styles"][n] = self.scout_narrator_style(n)

        return style_data

    def scout_work_style(self, title: str, author: str, author_baseline: dict = None) -> dict:
        """Extracts style attributes specific to a single work, informed by author's usual style."""
        baseline_str = ", ".join([f"{k}: {v}" for k, v in author_baseline.items()]) if author_baseline else "Unknown"

        prompt = f"""
        Analyze the literary style of the book: '{title}' by {author}

        The author's typical style baseline is: [{baseline_str}]

        Focus on work-specific attributes:
        - perspective: (e.g., 1st person, 3rd person limited, omniscient)
        - interiority: (e.g., deep character thoughts, external/plot-focused)
        - thematic_depth: (e.g., light/entertainment, moderate, heavy/philosophical)

        Also identify any attributes where this specific book DIFFERS from the author's usual baseline:
        - pacing, tone, style, prose_density, humor, etc.

        Return ONLY a raw JSON object with these keys.
        If an attribute is identical to the author's general baseline, omit it from the JSON.
        """
        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return _flatten_style_map(self._safe_extract_json(self._extract_text(response), "Work Style", title))

    def scout_author_style(self, name: str) -> dict:
        """Extracts style attributes for an author."""
        prompt = f"""
        Analyze the literary style of the author: {name}
        Focus on:
        - pacing: (e.g., fast-paced, slow-burn, brisk)
        - tone: (e.g., cynical, optimistic, atmospheric, clinical)
        - style: (e.g., lyrical, minimalist, technical, humorous)
        - prose_density: (e.g., minimalist, flowery, dense, accessible)
        - dialogue_style: (e.g., naturalistic, formal, witty, sparse)
        - lexicon: (e.g., archaic, academic, simple, specialized)
        - humor: (e.g., dry, satirical, slapstick, none)
        - world_building: (e.g., immersive, heavy, seamless, minimalist)
        - emotional_distance: (e.g., intimate, clinical, detached, warm)

        Return ONLY a raw JSON object with these nine keys.
        If unknown, return empty strings for those keys.
        """
        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return _flatten_style_map(self._safe_extract_json(self._extract_text(response), "Author Style", name))

    def scout_narrator_style(self, name: str) -> dict:
        """Extracts performance attributes for an audiobook narrator."""
        prompt = f"""
        Analyze the performance style of the audiobook narrator: {name}
        Provide values for:
        - pacing: (speed and rhythm)
        - voice_differentiation: (ability to create distinct characters)
        - accent_dialect: (accuracy/consistency of accents)
        - pitch_tone: (musicality/depth of voice)
        - consistency: (maintenance of tone/voices over time)
        - emotional_range: (conveying complex emotions)
        - gender_range: (believability of cross-gender performance)
        - production_quality: (clarity and technical quality)

        Return ONLY a raw JSON object with these keys. Use short descriptive phrases (3-5 words max).
        If unknown, return empty strings for those keys.
        """
        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return _flatten_style_map(self._safe_extract_json(self._extract_text(response), "Narrator Style", name))


class LLMTropeScout(LLMScout):
    """Scouts deep literary tropes for a work using LLM knowledge."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        """Finds tropes for a specific work."""
        prompt = f"""
        Identify the top 5-10 literary tropes, narrative patterns, or character archetypes for the book:
        Title: {title}
        Author: {author}

        For each trope, provide:
        - trope_name: (standard name, e.g., 'The Chosen One', 'Found Family')
        - description: (general definition of the trope)
        - relevance_score: (float 0.0-1.0 indicating how central it is to this specific book)
        - justification: (how this trope manifests in this specific book)

        CRITICAL: Focus on narrative devices and archetypes. Avoid broad genres (Fantasy, Sci-Fi) or simple moods.
        Return ONLY a raw JSON object with a 'tropes' key containing a list of these trope objects.
        """
        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return self._safe_extract_json(self._extract_text(response), "Tropes", title) or {"tropes": []}


# --- THE MANAGER ---


class ScoutManager:
    """Orchestrates multiple scouts and merges their results."""

    def __init__(self):
        self.scouts: list[tuple[BaseScout, int]] = []

    def register_scout(self, scout: BaseScout, priority: int = 1):
        """Registers a scout with a given priority (lower = higher priority)."""
        self.scouts.append((scout, priority))
        self.scouts.sort(key=lambda x: x[1])

    def enrich(self, title: str, author: str, format: str = "Paperback", **kwargs) -> dict:
        """Aggregates metadata from all registered scouts."""
        merged_data = {
            "title": title,
            "contributors": [{"name": author, "role": "Author"}],
            "genres": set(),
            "moods": set(),
            "source_priority": [],
            "author_style": {},
            "narrator_styles": {},
            "work_style": {},
            "narrator_names": [],
            "enriched_tropes": [],
        }

        for scout, _ in self.scouts:
            # Skip audiobook-only scouts if format is not audiobook
            if isinstance(scout, AudiobookScout | DirectKnowledgeScout) and "audiobook" not in format.lower():
                continue

            res = scout.search(title, author, format=format, narrators=merged_data["narrator_names"], **kwargs)
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

            # Merge Tropes (LLMTropeScout specific)
            if new_tropes := res.get("tropes"):
                merged_data["enriched_tropes"].extend(new_tropes)

            # Merge Contributors (Maintain uniqueness by name + role)
            existing_contributors = {(c["name"], c["role"]) for c in merged_data["contributors"]}
            for new_c in res.get("contributors", []):
                if (new_c["name"], new_c["role"]) not in existing_contributors:
                    merged_data["contributors"].append(new_c)
                    existing_contributors.add((new_c["name"], new_c["role"]))

            # Merge Styles (StyleScout specific)
            if style_data := res.get("author_style"):
                merged_data["author_style"].update(style_data)
            if w_style := res.get("work_style"):
                merged_data["work_style"].update(w_style)
            if n_styles := res.get("narrator_styles"):
                merged_data["narrator_styles"].update(n_styles)

            # Update Narrators list for StyleScout
            if new_narrators := res.get("narrator_names"):
                for n in new_narrators:
                    if n not in merged_data["narrator_names"]:
                        merged_data["narrator_names"].append(n)

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
