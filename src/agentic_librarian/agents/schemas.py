"""Pydantic output schemas for the recommendation pipeline's structured LLM steps."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Targets(BaseModel):
    """The Analyst's structured reading of the request."""

    tropes: list[str] = Field(default_factory=list, description="Target tropes the user wants.")
    styles: list[str] = Field(default_factory=list, description="Target literary/narrator styles.")
    session_constraints: list[str] = Field(
        default_factory=list, description="Things to avoid just for this session (e.g. 'no gore')."
    )


class Discovery(BaseModel):
    title: str = Field(description="Book title as it appears in search results.")
    author: str = Field(description="Primary author name.")
    why: str = Field(default="", description="One sentence on why it fits.")


class Discoveries(BaseModel):
    """The Explorer's structured web discoveries."""

    books: list[Discovery] = Field(default_factory=list)
