"""Local web server: security guards and API over the golden portfolio."""

import pytest
from starlette.testclient import TestClient

from conftest import GOLDEN
from glassfolio.server.app import create_app

TOKEN = "t" * 43
HOST = "testserver"


@pytest.fixture
def client(golden, tmp_path):
    lake, _ = golden
    (tmp_path / "index.html").write_text("<html></html>")
    app = create_app(lake, TOKEN, (HOST,), static_dir=tmp_path)
    c = TestClient(app, base_url=f"http://{HOST}")
    c.get(f"/?token={TOKEN}", follow_redirects=False)
    return c


def test_api_requires_session_cookie(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    anon = TestClient(app, base_url=f"http://{HOST}")
    assert anon.get("/api/exposure").status_code == 401


def test_wrong_token_is_refused(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    anon = TestClient(app, base_url=f"http://{HOST}")
    assert anon.get("/?token=wrong", follow_redirects=False).status_code == 403


def test_foreign_host_header_is_refused(client):
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 421


def test_cross_origin_post_is_refused(client):
    r = client.post("/api/owners", json={"nickname": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_cookie_is_httponly_and_strict(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    r = TestClient(app, base_url=f"http://{HOST}").get(f"/?token={TOKEN}", follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_exposure_with_slice(client):
    body = client.get("/api/exposure?as_of=2026-09-18").json()
    assert body["summary"]["total_value"] == pytest.approx(18000)
    nvda = {c["ticker"]: c for c in body["companies"]}["NVDA"]
    assert nvda["total"] == pytest.approx(4500) and nvda["direct_value"] == pytest.approx(1000)
    roth = client.get("/api/exposure?as_of=2026-09-18&account_type=roth_ira").json()
    assert roth["summary"]["total_value"] == pytest.approx(10000)


def test_company_drilldown(client):
    body = client.get("/api/company/NVDA?as_of=2026-09-18").json()
    assert {r["group"]: r["total"] for r in body["fund"]}["QQQ"] == pytest.approx(2000)
    assert {r["group"]: r["total"] for r in body["account"]}["Alice Roth"] == pytest.approx(1200)


def test_meta_lists_accounts_and_default_date(client):
    meta = client.get("/api/meta").json()
    assert meta["default_as_of"] == "2026-09-18"
    assert {a["nickname"] for a in meta["accounts"]} == {"Alice Taxable", "Alice Roth"}


def test_run_checks_and_read_back(client):
    r = client.post("/api/checks", json={"as_of": "2026-09-18", "account": "Alice Taxable",
                                          "reported_total": "8010"})
    assert r.status_code == 200
    statuses = {x["check_type"]: x["status"] for x in r.json()["results"]}
    assert statuses["account_total"] == "pass"
    latest = client.get("/api/checks").json()
    assert any(x["account"] == "Alice Taxable" for x in latest)


def test_statement_upload_preview_then_commit(client):
    client.post("/api/accounts", json={"nickname": "New", "owner": "alice", "broker": "G",
                                       "account_type": "taxable"})
    profile = client.get("/api/meta").json()["profiles"][0]["profile_id"]
    raw = (GOLDEN / "broker_alice_taxable.csv").read_bytes().replace(b"8,010", b"8,011")
    r = client.post("/api/import/statement", files={"file": ("s.csv", raw)},
                    data={"account": "New", "profile_id": profile, "as_of": "2026-09-18"})
    preview = r.json()
    assert r.status_code == 200 and len(preview["rows"]) == 4
    before = client.get("/api/ops").json()
    assert client.post("/api/import/commit", json={"token": preview["token"]}).status_code == 200
    assert len(client.get("/api/ops").json()) == len(before) + 1
    again = client.post("/api/import/commit", json={"token": preview["token"]})
    assert again.status_code == 400 and "expired" in again.json()["error"]


def test_bad_input_is_a_400_not_a_crash(client):
    r = client.post("/api/import/etf", files={"file": ("x.csv", b"garbage")},
                    data={"etf": "ZZZ", "format": "ishares"})
    assert r.status_code == 400 and "iShares" in r.json()["error"]
