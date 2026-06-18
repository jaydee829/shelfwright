import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
client = TestClient(app)

GOODREADS_CSV = (
    "Book Id,Title,Author,My Rating,Binding,Date Read,Exclusive Shelf,My Review\n"
    "1,Dune,Frank Herbert,5,Kindle Edition,2024/03/05,read,loved it\n"
    "2,Hyperion,Dan Simmons,0,Audiobook,,to-read,\n"
)


def _upload(csv_text):
    return client.post("/import/preview", files={"file": ("export.csv", io.BytesIO(csv_text.encode()), "text/csv")})


def test_preview_detects_goodreads_and_suggests_mapping():
    r = _upload(GOODREADS_CSV)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "goodreads"
    assert body["suggested_mapping"]["title"] == "Title"
    assert body["counts"]["read_dated"] == 1
    assert body["counts"]["to_read"] == 1
    assert len(body["preview_rows"]) == 2


def test_preview_rejects_empty_file():
    r = _upload("")
    assert r.status_code == 422
