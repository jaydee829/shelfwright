import re
from datetime import date

import pandas as pd


def split_formats(df: pd.DataFrame) -> pd.DataFrame:
    """Create tidy rows of books with multiple formats"""
    if "format" not in df.columns:
        raise ValueError("DataFrame must contain a 'format' column")

    df["format"] = df["format"].str.split(",")
    df = df.explode("format")
    df["format"] = df["format"].str.strip()
    # explode() duplicates index labels for multi-format rows; reset so the downstream column-wise
    # concats in split_authors/split_narrators can align (a non-unique index raises InvalidIndexError).
    df = df.reset_index(drop=True)
    return df


def parse_completion_date(date_str: str, fallback_year: int | None = None) -> date | None:
    """Parse common completion date formats in the CSV."""
    if not date_str or pd.isna(date_str) or str(date_str).strip() == "":
        return None

    date_str = str(date_str).strip()

    for fmt in ["%d-%b", "%b-%d", "%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]:
        try:
            parsed = pd.to_datetime(date_str, format=fmt, errors="coerce")
            if not pd.isna(parsed):
                if parsed.year == 1900:
                    target_year = fallback_year if fallback_year else date.today().year
                    parsed = parsed.replace(year=target_year)
                return parsed.date()
        except (ValueError, TypeError):
            continue

    parsed = pd.to_datetime(date_str, errors="coerce")
    if not pd.isna(parsed):
        return parsed.date()

    return None


def _is_name_suffix(text: str) -> bool:
    """Check if a string component is a common name suffix."""
    suffixes = {"jr", "sr", "iii", "iv", "v", "ii", "esq", "md", "phd"}
    clean_text = text.lower().replace(".", "").strip()
    return clean_text in suffixes


def _should_split_on_comma(text: str) -> bool:
    """
    Heuristic to determine if a comma is a list separator or name part.

    Returns True if likely a list (e.g. "Robert Jordan, Brandon Sanderson")
    Returns False if likely a single name or suffix (e.g. "Jordan, Robert" or "Vonnegut, Jr.")
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) <= 1:
        return False

    # 1. Check for common suffixes in the second part
    if _is_name_suffix(parts[1]):
        return False

    # 2. Check word counts
    # "Jordan, Robert" -> 1 word, 1 word (Total 2)
    # "Robert Jordan, Brandon Sanderson" -> 2 words, 2 words (Total 4)
    total_words = len(text.split())
    if total_words > 2:
        # If it looks like "First Last, First Last", definitely split
        # If it looks like "Last, First Middle", it might have 3 words but 1 comma.
        # But "Last, First" is the dominant 2-word case.
        if len(parts) > 2:
            return True  # Multiple commas usually means a list

        # Two parts, more than 2 words.
        # Check if BOTH parts look like full names (contain space)
        # "Robert Jordan, Brandon Sanderson" -> Yes, Yes (Split)
        # "Martin, George R. R." -> No, Yes (Don't split)
        if " " in parts[0].strip() and " " in parts[1].strip():
            return True

    return False


def split_authors(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for books with more than one author.

    Deterministic logic:
    1. Split on strong separators (semicolon, 'and', '&').
    2. Apply heuristic to determine if remaining commas are separators or name parts.
    """
    if "Author" not in df.columns:
        raise ValueError("DataFrame must contain an 'Author' column")

    strong_sep = r";|\s+and\s+|\s+&\s+"

    def process_row(author_str):
        if not author_str or pd.isna(author_str):
            return []

        # 1. First split by strong separators
        first_pass = re.split(strong_sep, str(author_str))

        final_authors = []
        for part in first_pass:
            part = part.strip()
            if not part:
                continue

            # 2. Heuristic check for commas
            if "," in part and _should_split_on_comma(part):
                final_authors.extend([p.strip() for p in part.split(",") if p.strip()])
            else:
                final_authors.append(part)

        return final_authors

    # Apply the logic to each row
    author_lists = df["Author"].apply(process_row)

    # Convert lists to a DataFrame of numbered columns. Build it on df's own index —
    # .tolist() discards it, and the axis=1 concat below aligns by label, so a default
    # RangeIndex would silently misalign on any non-default input index (PR #32 review).
    author_splits = pd.DataFrame(author_lists.tolist(), index=df.index)
    author_splits.columns = [f"Author_{i + 1}" for i in range(author_splits.shape[1])]

    # Merge back and drop original
    df = pd.concat([df.drop(columns=["Author"]), author_splits], axis=1)
    return df


def split_narrators(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for audiobooks with more than 1 narrator"""
    if "Narrator" not in df.columns:
        raise ValueError("DataFrame must contain a 'Narrator' column")

    narrator_splits = df["Narrator"].str.split(";", expand=True).map(lambda x: x.strip() if isinstance(x, str) else x)
    narrator_splits.columns = [f"Narrator_{i + 1}" for i in range(narrator_splits.shape[1])]
    df = pd.concat([df.drop(columns=["Narrator"]), narrator_splits], axis=1)
    return df
