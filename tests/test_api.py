from fastapi.testclient import TestClient

from tickercase.api import create_app
from tickercase.service import CaseService
from tickercase.storage import CaseStore

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft


def make_client(tmp_path):
    service = CaseService(make_settings(tmp_path), store=CaseStore(tmp_path / "cases"), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    return TestClient(create_app(service))


def test_full_api_flow(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/health").json()["status"] == "ok"
    draft = ps_draft().model_dump()

    v = client.post("/claims/validate", json=draft).json()
    assert v["ok"] is True and len(v["fingerprint"]) == 64

    r = client.post("/cases", json={"draft": draft, "sec_mode": "synthetic"})
    assert r.status_code == 409 and r.json()["status"] == "blocked_unconfirmed"

    conf = client.post("/claims/confirm", json=draft)
    assert conf.status_code == 200
    confirmation = conf.json()

    changed = dict(draft, target_price="200")
    r = client.post("/cases", json={"draft": changed, "confirmation": confirmation, "sec_mode": "synthetic"})
    assert r.status_code == 409 and r.json()["status"] == "blocked_confirmation_stale"

    r = client.post("/cases", json={"draft": draft, "confirmation": confirmation, "sec_mode": "synthetic"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"]["label"] == "partially_supported" and body["analysis_status"] == "deterministic_rules"
    assert {i["id"] for i in body["evidence_items"]} >= {"E1", "E2", "E3"}
    assert body["recheck_conditions"]
    calc = {c["name"]: c for c in body["calculations"]}
    assert calc["required_annual_revenue"]["value"] == "400000000"  # Decimal serialised as string
    assert calc["target_market_cap"]["value"] == "10000000000"
    assert body["evidence_records"][0]["data_mode"] == "synthetic"

    stored = client.get(f"/cases/{body['case_id']}")
    assert stored.status_code == 200 and stored.json()["case_id"] == body["case_id"]
    assert client.get("/cases/" + "0" * 32).status_code == 404
    assert client.get("/cases/../../etc").status_code in (400, 404)


def test_confirm_rejects_invalid(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/claims/confirm", json=ps_draft(target_price="NaN").model_dump())
    assert r.status_code == 422
    assert r.json()["issues"][0]["code"] == "nan_not_allowed"


def test_invalid_case_returns_422(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/cases", json={"draft": ps_draft(horizon_years="-1").model_dump(), "sec_mode": "synthetic"})
    assert r.status_code == 422 and r.json()["status"] == "blocked_invalid_input"


def test_live_failure_via_api_keeps_calculations(tmp_path):
    client = make_client(tmp_path)  # no SEC_USER_AGENT configured
    draft = ps_draft().model_dump()
    confirmation = client.post("/claims/confirm", json=draft).json()
    body = client.post("/cases", json={"draft": draft, "confirmation": confirmation, "sec_mode": "live"}).json()
    assert body["status"] == "evaluated_with_provider_errors"
    assert body["provider_errors"][0]["code"] == "missing_user_agent"
    assert len(body["calculations"]) >= 5 and body["evidence_records"] == []


def test_reference_endpoint(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/reference/SYNT", params={"mode": "synthetic"})
    assert r.status_code == 200
    body = r.json()
    assert body["suggestions"]["reference_price"]["value"] == "50"
    assert body["suggestions"]["current_shares"]["value"] == "95000000"
    assert body["revenue_suggestion"]["value"] == "200000000"
    defaults = body["assumption_suggestions"]
    assert defaults["target_assumed_shares"]["value"] == "95000000"
    assert defaults["valuation_multiple_ps"]["value"] == "23.75"  # 50 x 95M / 200M
    assert defaults["valuation_multiple_ps"]["source"].startswith("default_assumption:")
    assert client.get("/reference/bad ticker!", params={"mode": "synthetic"}).status_code == 422
