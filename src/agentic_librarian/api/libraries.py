"""Library-picker endpoints: search the public OverDrive directory, and read/replace the
user's saved libraries (ordered). No secrets — slugs are public (see UserLibrary)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError
from agentic_librarian.db.models import UserLibrary
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    global db_manager
    db_manager = new_manager


class LibraryIn(BaseModel):
    slug: str
    name: str


class LibrariesIn(BaseModel):
    libraries: list[LibraryIn]


@router.get("/libraries/search")
def search_libraries(q: str, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    if not q.strip():
        return []
    try:
        return overdrive.search_libraries(q)
    except ThunderError as exc:
        raise HTTPException(status_code=503, detail="library directory unavailable") from exc


@router.get("/me/libraries")
def get_my_libraries(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(UserLibrary)
            .filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        )
        return {"libraries": [{"slug": r.library_slug, "name": r.display_name} for r in rows]}


@router.put("/me/libraries")
def put_my_libraries(
    body: LibrariesIn = Body(...),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    with db_manager.get_session() as session:
        session.query(UserLibrary).filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby").delete()
        for i, lib in enumerate(body.libraries):
            session.add(
                UserLibrary(
                    user_id=user.id,
                    provider="libby",
                    library_slug=lib.slug,
                    display_name=lib.name,
                    sort_order=i,
                )
            )
        session.flush()
    return {"libraries": [{"slug": lib.slug, "name": lib.name} for lib in body.libraries]}
