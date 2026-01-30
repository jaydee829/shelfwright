from datetime import date

import pandas as pd


def split_formats(df: pd.DataFrame) -> pd.DataFrame:
    """Create tidy rows of books with multiple formats

    Args:
        df (pd.DataFrame): raw book data

    Returns:
        pd.DataFrame: book data with one format per row
    """
    if "format" not in df.columns:
        raise ValueError("DataFrame must contain a 'format' column")

    # Split the 'format' column by comma and explode into multiple rows
    df["format"] = df["format"].str.split(",")
    df = df.explode("format")

    # Strip whitespace from format entries
    df["format"] = df["format"].str.strip()
    return df


def parse_completion_date(date_str: str, fallback_year: int | None = None) -> date | None:
    """Parse common completion date formats in the CSV.

    Handles:
    - 1/7/2020
    - 4-Jan (uses fallback_year if provided, else current year)
    - 2020-01-28
    """
    if not date_str or pd.isna(date_str) or str(date_str).strip() == "":
        return None

    date_str = str(date_str).strip()

    # Try common explicit formats first
    # %d-%b handles "4-Jan" (though result has year 1900)
    for fmt in ["%d-%b", "%b-%d", "%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]:
        try:
            parsed = pd.to_datetime(date_str, format=fmt, errors="coerce")
            if not pd.isna(parsed):
                if parsed.year == 1900:
                    # Use fallback year or current year for "day-Month" formats
                    target_year = fallback_year if fallback_year else date.today().year
                    parsed = parsed.replace(year=target_year)
                return parsed.date()
        except (ValueError, TypeError):
            continue

    # Fallback to generic robust parsing
    try:
        parsed = pd.to_datetime(date_str, errors="coerce")
        if not pd.isna(parsed):
            return parsed.date()
    except Exception:
        pass

    return None


def split_authors(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for books with more than one author

    Handles: ';', 'and', and ' & ' as separators.

    Args:
        df (pd.DataFrame): raw book data

    Returns:
        pd.DataFrame: book data with 1 author per column
    """
    if "Author" not in df.columns:
        raise ValueError("DataFrame must contain an 'Author' column")

    # Split on ';', ' and ', or ' & '
    author_pattern = r";|\s+and\s+|\s+&\s+"

    # We use apply(lambda s: ...) to handle potential non-string values safely
    # but the CSV reader usually gives strings or NaN.
    author_splits = df["Author"].str.split(author_pattern, expand=True)

    # Strip whitespace from all resulting columns
    for col in author_splits.columns:
        author_splits[col] = author_splits[col].str.strip()

    # Rename the new columns with numbers
    author_splits.columns = [f"Author_{i + 1}" for i in range(author_splits.shape[1])]

    # Drop the original 'Author' column and add the new author columns
    df = pd.concat([df.drop(columns=["Author"]), author_splits], axis=1)
    return df


def split_narrators(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for audiobooks with more than 1 narrator

    Args:
        df (pd.DataFrame): enhanced audiobook data

    Returns:
        pd.DataFrame: audiobook data with 1 narrator per column
    """
    if "Narrator" not in df.columns:
        raise ValueError("DataFrame must contain a 'Narrator' column")

    narrator_splits = df["Narrator"].str.split(";", expand=True).map(lambda x: x.strip() if isinstance(x, str) else x)

    # Rename the new columns with numbers
    narrator_splits.columns = [f"Narrator_{i + 1}" for i in range(narrator_splits.shape[1])]

    # Drop the original 'Narrator' column and add the new narrator columns
    df = pd.concat([df.drop(columns=["Narrator"]), narrator_splits], axis=1)
    return df
