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
    c = TestClient(app, base_url=f"http://{HOST}", headers={"x-glassfolio": "1"})
    c.get(f"/?token={TOKEN}", follow_redirects=False)
    return c


def test_api_requires_session_cookie(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    anon = TestClient(app, base_url=f"http://{HOST}", headers={"x-glassfolio": "1"})
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
    assert TOKEN.lower() not in cookie  # a separate session secret, not the launch token
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
    assert meta["default_as_of"] == "2026-09-30"  # latest statement
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


def test_changes_inbox_answer_and_returns(client):
    q = "start=2026-09-18&end=2026-09-30&account=Alice%20Taxable"
    body = client.get(f"/api/changes?{q}").json()
    nvda = {c["ticker"]: c for c in body["companies"]}["NVDA"]
    assert nvda["rebalance_effect"] == pytest.approx(1210) and nvda["change"] == pytest.approx(1754)
    assert body["returns"]["open_questions"] == 1
    (item,) = client.get("/api/inbox").json()
    r = client.post("/api/inbox/answer", json={"item_id": item["item_id"], "classification": "deposit"})
    assert r.status_code == 200
    after = client.get(f"/api/changes?{q}").json()["returns"]
    assert after["open_questions"] == 0 and after["twr"] == pytest.approx(8350 / 8000 - 1)


def test_changes_rejects_reversed_dates(client):
    r = client.get("/api/changes?start=2026-09-30&end=2026-09-18")
    assert r.status_code == 400


def test_launch_token_works_once(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    c = TestClient(app, base_url=f"http://{HOST}")
    first = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert first.status_code == 303 and TOKEN not in first.headers["set-cookie"]
    assert TestClient(app, base_url=f"http://{HOST}").get(
        f"/?token={TOKEN}", follow_redirects=False).status_code == 403


def test_non_ascii_token_is_a_403_not_a_crash(golden, tmp_path):
    app = create_app(golden[0], TOKEN, (HOST,), static_dir=tmp_path)
    c = TestClient(app, base_url=f"http://{HOST}", raise_server_exceptions=False)
    assert c.get("/?token=%C3%A9", follow_redirects=False).status_code == 403


def test_api_needs_client_header(client):
    assert client.get("/api/meta", headers={"x-glassfolio": ""}).status_code == 403


def test_cross_site_fetch_metadata_is_refused(client):
    assert client.get("/api/meta", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/meta", headers={"sec-fetch-site": "same-site"}).status_code == 403
    assert client.get("/api/meta", headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_pages_cannot_be_framed(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_oversized_upload_rejected_before_parsing(client):
    r = client.post("/api/import/prices", content=b"x", headers={
        "content-type": "multipart/form-data; boundary=b", "content-length": str(30 * 1024 * 1024)})
    assert r.status_code == 400 and "20 MB" in r.json()["error"]


def test_exposure_includes_after_tax(client):
    body = client.get("/api/exposure?as_of=2026-09-18&account=Alice%20Taxable").json()
    assert body["summary"]["after_tax"] == pytest.approx(8000 - 1500 * 0.333)
    nvda = {c["ticker"]: c for c in body["companies"]}["NVDA"]
    assert nvda["after_tax"] < nvda["total"]


def test_onboard_person_with_state_and_what_if(client):
    r = client.post("/api/people", json={"nickname": "sam", "state": "TX",
                                        "federal_ltcg_rate": 0.15, "federal_ordinary_rate": 0.22})
    assert r.status_code == 200
    body = client.get("/api/taxes").json()
    sam = next(p for p in body["profiles"] if p["name"] == "sam (TX)")
    assert sam["state_rate"] == 0 and sam["rates"]["stcg"] == pytest.approx(0.22)
    what_if = client.get("/api/taxes?as_of=2026-09-18&account=Alice%20Taxable&state_rate=0").json()
    assert what_if["what_if"]["tax"] == pytest.approx(1500 * 0.24)
    assert what_if["totals"]["tax"] == pytest.approx(1500 * 0.333)


def test_progressive_state_requires_a_rate(client):
    r = client.post("/api/people", json={"nickname": "ny", "state": "NY"})
    assert r.status_code == 400 and "bracket" in r.json()["error"]


def test_mark_account_tax_exempt(client):
    assert client.post("/api/tax/treatment", json={"account": "Alice Taxable", "treatment": "exempt"}).status_code == 200
    body = client.get("/api/exposure?as_of=2026-09-18&account=Alice%20Taxable").json()
    assert body["summary"]["tax"] == 0


def test_computed_properties_are_serialized(client):
    body = client.get("/api/taxes?as_of=2026-09-18&account=Alice%20Taxable").json()
    assert body["totals"]["after_tax"] == pytest.approx(8000 - 1500 * 0.333)


def test_bad_person_rates_create_nothing(client):
    r = client.post("/api/people", json={"nickname": "bob", "state": "CA", "state_rate": 9.3})
    assert r.status_code == 400
    assert "bob" not in client.get("/api/meta").json()["owners"]


def test_strict_inputs(client):
    r = client.post("/api/people", json={"nickname": "x", "state": "CA", "niit": "false",
                                        "federal_ltcg_rate": None})
    assert r.status_code == 400
    assert client.get("/api/taxes?ltcg=5").status_code == 400
    assert client.get("/api/taxes?assumption=sideways").status_code == 400
