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


EVALS = GOLDEN.parent.parent / "evals" / "files"


def test_assisted_import_without_a_model_then_recognised(client):
    raw = (EVALS / "fidelity_style_positions.csv").read_bytes()
    read = client.post("/api/assist/read", files={"file": ("f.csv", raw)}).json()
    assert read["source"] == "heuristic" and read["kind"] == "positions" and read["errors"] == []
    assert read["as_of"] == "2026-10-01" and read["cash_symbols"] == ["SPAXX**"]
    preview = client.post("/api/assist/preview", json={
        "token": read["token"], "account": "Alice Taxable", "broker": "Fidelity", "reading": {}}).json()
    assert {r["symbol"] for r in preview["rows"]} == {"NVDA", "FXAIX", "SPAXX**"}
    assert client.post("/api/import/commit", json={"token": preview["token"]}).status_code == 200
    again = client.post("/api/assist/read", files={
        "file": ("g.csv", raw.replace(b"10/01/2026", b"10/31/2026"))}).json()
    assert again["source"] == "saved" and again["broker"] == "Fidelity" and again["as_of"] == "2026-10-31"


def test_assisted_fund_holdings_and_lots(client):
    raw = (EVALS / "vanguard_style_holdings.csv").read_bytes()
    read = client.post("/api/assist/read", files={"file": ("h.csv", raw)}).json()
    assert read["kind"] == "fund_holdings" and read["fund_ticker"] == "GTOT"
    preview = client.post("/api/assist/preview", json={"token": read["token"], "reading": {}}).json()
    assert preview["count"] == 4 and preview["errors"] == []
    assert client.post("/api/import/commit", json={"token": preview["token"]}).status_code == 200
    lots = (EVALS / "lots_style.csv").read_bytes()
    read = client.post("/api/assist/read", files={"file": ("l.csv", lots)}).json()
    preview = client.post("/api/assist/preview", json={"token": read["token"], "account": "Alice Taxable",
                                                      "reading": {}}).json()
    assert preview["count"] == 2
    assert client.post("/api/import/commit", json={"token": preview["token"]}).status_code == 200


def test_user_corrections_are_validated(client):
    raw = (EVALS / "plain_positions.csv").read_bytes()
    read = client.post("/api/assist/read", files={"file": ("p.csv", raw)}).json()
    bad = client.post("/api/assist/preview", json={"token": read["token"], "account": "Alice Taxable",
                                                  "reading": {"columns": {**read["columns"], "shares": "Close"},
                                                              "as_of": "2026-09-18", "edited": True}})
    assert bad.status_code == 422 and any("doesn't match" in e for e in bad.json()["errors"])


def test_model_settings_endpoints(client):
    body = client.get("/api/assist/models").json()
    assert body["name"] is None
    r = client.post("/api/assist/model", json={"url": "http://example.com/v1", "name": "x"})
    assert r.status_code == 400
    ok = client.post("/api/assist/model", json={"url": "http://127.0.0.1:9/v1", "name": "tiny"}).json()
    assert ok["name"] == "tiny"


def test_failed_lots_import_saves_no_layout(client):
    before = len(client.get("/api/meta").json()["profiles"])
    lots = b"Symbol,Open Date,Quantity,Cost Basis\nZZZZ,01/02/2024,6,300\n"
    read = client.post("/api/assist/read", files={"file": ("l.csv", lots)}).json()
    r = client.post("/api/assist/preview", json={"token": read["token"], "account": "Alice Taxable",
                                                 "reading": {"as_of": "2026-09-18"}})
    assert r.status_code == 400 and "unknown securities" in r.json()["error"]
    assert len(client.get("/api/meta").json()["profiles"]) == before


def test_preview_store_is_bounded(client):
    from glassfolio.server.api import MAX_HELD, keep_recent
    assert len(keep_recent({i: i for i in range(MAX_HELD + 5)})) == MAX_HELD


def test_chat_without_a_model_says_so(client):
    body = client.post("/api/chat", json={"message": "How much NVDA?"}).json()
    assert "Settings" in body["reply"]


def test_chat_proposal_needs_confirmation(client, monkeypatch):
    import glassfolio.server.chat_api as chat_api

    class Scripted:
        name = "scripted"

        def __init__(self):
            self.n = 0

        def chat_json(self, messages, schema):
            self.n += 1
            if self.n == 1:
                return {"action": "call_tool", "tool": "list_questions", "arguments": {}, "reason": "look", "reply": None}
            if self.n == 2:
                item = messages[-1]["content"].split('"item_id": "')[1].split('"')[0]
                return {"action": "call_tool", "tool": "answer_question", "reply": None,
                        "reason": "they said the $3000 was a deposit",
                        "arguments": {"item_id": item, "classification": "deposit"}}
            return {"action": "reply", "tool": None, "arguments": {}, "reason": None, "reply": "Confirm below."}

    model = Scripted()
    monkeypatch.setattr(chat_api, "configured_model", lambda: model)
    body = client.post("/api/chat", json={"message": "that was a deposit"}).json()
    (proposal,) = body["proposals"]
    assert len(client.get("/api/inbox").json()) == 1
    assert client.post("/api/chat/confirm", json={"action_id": proposal["action_id"]}).status_code == 200
    assert client.get("/api/inbox").json() == []
    (op,) = [o for o in client.get("/api/ops").json() if o["tool"] == "resolve_flow"]
    assert op["actor"] == "model" and "3000" not in op["description"] and "#" in op["description"]
    again = client.post("/api/chat/confirm", json={"action_id": proposal["action_id"]})
    assert again.status_code == 400


class ProposeDeposit:
    """Scripted model: list questions, propose a deposit for the first, reply."""
    name = "scripted"

    def __init__(self):
        self.n = 0

    def chat_json(self, messages, schema):
        self.n += 1
        if self.n == 1:
            return {"action": "call_tool", "tool": "list_questions", "arguments": {}, "reason": "look", "reply": None}
        if self.n == 2:
            item = messages[-1]["content"].split('"item_id": "')[1].split('"')[0]
            return {"action": "call_tool", "tool": "answer_question", "reply": None, "reason": "said so",
                    "arguments": {"item_id": item, "classification": "deposit"}}
        return {"action": "reply", "tool": None, "arguments": {}, "reason": None, "reply": "Confirm below."}


def test_chat_confirm_refuses_questions_already_answered(client, monkeypatch):
    import glassfolio.server.chat_api as chat_api
    monkeypatch.setattr(chat_api, "configured_model", ProposeDeposit)
    (proposal,) = client.post("/api/chat", json={"message": "deposit"}).json()["proposals"]
    (item,) = client.get("/api/inbox").json()
    client.post("/api/inbox/answer", json={"item_id": item["item_id"], "classification": "dividend"})
    r = client.post("/api/chat/confirm", json={"action_id": proposal["action_id"]})
    assert r.status_code == 400 and "already" in r.json()["error"]


def test_chat_rejects_malformed_body(client):
    assert client.post("/api/chat", json=["x"]).status_code == 400
