from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UTC = UTC


class Base(DeclarativeBase):
    pass


class WorkContributor(Base):
    __tablename__ = "work_contributors"

    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("authors.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String, default="Primary", primary_key=True)

    work: Mapped["Work"] = relationship(back_populates="contributors")
    author: Mapped["Author"] = relationship(back_populates="contributions")


# Junction table for Edition and Narrator
edition_narrators = Table(
    "edition_narrators",
    Base.metadata,
    Column("edition_id", ForeignKey("editions.id"), primary_key=True),
    Column("narrator_id", ForeignKey("narrators.id"), primary_key=True),
)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    contributions: Mapped[list["WorkContributor"]] = relationship(back_populates="author")


class Work(Base):
    __tablename__ = "works"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    moods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    contributors: Mapped[list["WorkContributor"]] = relationship(back_populates="work", cascade="all, delete-orphan")
    editions: Mapped[list["Edition"]] = relationship(back_populates="work")
    tropes: Mapped[list["WorkTrope"]] = relationship(back_populates="work")
    suggestions: Mapped[list["Suggestions"]] = relationship(back_populates="work")


class Narrator(Base):
    __tablename__ = "narrators"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    style_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Trope(Base):
    __tablename__ = "tropes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Vector | None] = mapped_column(Vector(1536), nullable=True)

    works: Mapped[list["WorkTrope"]] = relationship(back_populates="trope")


class WorkTrope(Base):
    __tablename__ = "work_tropes"

    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True, nullable=False)
    trope_id: Mapped[UUID] = mapped_column(ForeignKey("tropes.id"), primary_key=True, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    work: Mapped["Work"] = relationship(back_populates="tropes")
    trope: Mapped["Trope"] = relationship(back_populates="works")


class Edition(Base):
    __tablename__ = "editions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), nullable=False)
    isbn_13: Mapped[str | None] = mapped_column(String, nullable=True)
    format: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    work: Mapped["Work"] = relationship(back_populates="editions")
    narrators: Mapped[list["Narrator"]] = relationship(secondary=edition_narrators)
    reading_history: Mapped[list["ReadingHistory"]] = relationship(back_populates="edition")


class ReadingHistory(Base):
    __tablename__ = "reading_history"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    edition_id: Mapped[UUID] = mapped_column(ForeignKey("editions.id"), nullable=False)
    date_started: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_completed: Mapped[date] = mapped_column(Date, nullable=False)
    user_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    edition: Mapped["Edition"] = relationship(back_populates="reading_history")


class Suggestions(Base):
    __tablename__ = "suggestions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), nullable=False)
    suggested_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="Suggested", nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    work: Mapped["Work"] = relationship(back_populates="suggestions")
