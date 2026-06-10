"""Lift 2 Stage 4: FastAPI serves the built SPA same-origin, with an index.html fallback
for client-side routes, real built files served as-is, API routes taking precedence, and a
path-traversal guard on the catch-all."""

from fastapi.testclient import TestClient

from agentic_librarian.api.main import app


def _build_dist(root):
    (root / "assets").mkdir()
    (root / "index.html").write_text('<!doctype html><div id="root"></div>')
    (root / "assets" / "app.js").write_text('console.log("spa")')
    return root


def test_root_serves_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_unknown_path_falls_back_to_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/add")  # a client route, not a file
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_real_asset_is_served(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/assets/app.js")
    assert r.status_code == 200
    assert 'console.log("spa")' in r.text


def test_api_route_wins_over_spa_catch_all(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/health")  # unauthenticated API route
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_path_traversal_is_blocked(tmp_path, monkeypatch):
    # Secret lives OUTSIDE the dist dir. Call the handler directly — the HTTP client would
    # normalize the `..` away before routing, so a direct call is what exercises the guard.
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("../secret.txt")
    # The escaped path is refused → falls back to the SPA shell (index.html), not the secret.
    assert resp.path.endswith("index.html")


def test_sibling_prefix_collision_is_blocked(tmp_path, monkeypatch):
    # A sibling dir whose path shares the dist prefix (dist vs dist-secret) must NOT be
    # reachable — this is why the guard compares against root + os.sep, not a bare prefix.
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    sibling = tmp_path / "dist-secret"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("SIBLING-SECRET")
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("../dist-secret/leak.txt")
    assert resp.path.endswith("index.html")  # refused → SPA shell, not the sibling file


def test_absolute_path_input_is_blocked(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("/etc/hostname")
    assert resp.path.endswith("index.html")
