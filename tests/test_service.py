from decimal import Decimal

from tickercase.http_client import FakeHttpClient, FetchError
from tickercase.models import CaseStatus, DataMode
from tickercase.providers.sec import SUBMISSIONS_URL, TICKERS_URL
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, TICKERS_PAYLOAD, make_settings, offline_factory, ps_draft, submissions_payload

SUB = SUBMISSIONS_URL.format(cik="0001234567")


def svc(tmp_path, factory=None, **settings_kw):
    settings = make_settings(tmp_path, **settings_kw)
    return CaseService(
        settings,
        fetcher_factory=factory or offline_factory(settings),
        store=CaseStore(tmp_path / "cases"),
        now=lambda: FIXED_NOW,
        today=lambda: FIXED_TODAY,
    )


def confirmed(draft):
    return confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW)


def test_unconfirmed_does_not_evaluate(tmp_path):
    r = svc(tmp_path).evaluate(ps_draft(), None, sec_mode="synthetic")
    assert r.status is CaseStatus.BLOCKED_UNCONFIRMED and r.calculations == [] and r.evidence_records == []


def test_stale_confirmation_does_not_evaluate(tmp_path):
    c = confirmed(ps_draft())
    r = svc(tmp_path).evaluate(ps_draft(target_price="120"), c, sec_mode="synthetic")
    assert r.status is CaseStatus.BLOCKED_CONFIRMATION_STALE and r.calculations == []


def test_invalid_input_blocked_with_issues(tmp_path):
    r = svc(tmp_path).evaluate(ps_draft(reference_price="0", currency=None), None, sec_mode="synthetic")
    assert r.status is CaseStatus.BLOCKED_INVALID_INPUT
    assert {i.code for i in r.validation_issues} == {"must_be_positive"}
    assert any(m.field == "currency" and m.blocking for m in r.missing_fields)


def test_synthetic_end_to_end(tmp_path):
    s = svc(tmp_path)
    draft = ps_draft(ticker="SYNT")
    r = s.evaluate(draft, confirmed(draft), sec_mode="synthetic")
    assert r.status is CaseStatus.EVALUATED
    assert r.verdict.label == "partially_supported" and r.analysis_status == "deterministic_rules"
    assert {k: v for k, v in r.data_modes.items() if k != "analysis"} == {
        "claim_inputs": "user_input", "sec_filings": "synthetic", "sec_facts": "synthetic", "market_prices": "synthetic",
        "options": "synthetic", "base_rate": "synthetic", "insiders": "synthetic", "fear_greed": "synthetic"}
    assert r.oracle.low is not None and {m.id for m in r.oracle.methods if m.status == "ok"} >= {"M1", "M2", "M3"}
    assert r.reported_facts.revenue[-1].value == Decimal("200000000")
    assert r.market.last_close == Decimal("50")
    assert r.mixed_sources is True
    assert {x.data_mode for x in r.evidence_records} == {DataMode.SYNTHETIC}
    assert any("synthetic" in w for w in r.warnings)
    assert r.confirmed_claim.value_provenance["horizon_years"] == "user_input:assumption"
    assert r.confirmed_claim.value_provenance["reference_price"] == "user_input:manual_reference_value"
    stored = s.store.load(r.case_id)
    assert stored.model_dump() == r.model_dump()


def test_network_failure_keeps_calculations_and_does_not_substitute(tmp_path):
    fake = FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, SUB: FetchError("timeout", "timed out", url=SUB, data_mode=DataMode.LIVE)}, data_mode=DataMode.LIVE)
    s = svc(tmp_path, factory=lambda mode, source: fake)
    draft = ps_draft(ticker="TEST")
    r = s.evaluate(draft, confirmed(draft), sec_mode="live")
    assert r.status is CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS
    calc = {c.name: c for c in r.calculations}
    assert calc["required_annual_revenue"].value == Decimal("400000000")
    assert r.evidence_records == []
    assert r.provider_errors[0].code == "timeout"
    assert r.data_modes["sec_filings"] == "unavailable"
    # missing data is reported as missing evidence, never as contrary evidence
    assert r.verdict.label == "insufficiently_specified"
    assert {i.id: i.stance for i in r.evidence_items}["E1"] == "missing"
    assert r.mixed_sources is False


def test_live_without_user_agent_reports_config_problem(tmp_path):
    s = svc(tmp_path, sec_user_agent=None)
    draft = ps_draft()
    r = s.evaluate(draft, confirmed(draft), sec_mode="live")
    assert r.provider_errors[0].code == "missing_user_agent"
    assert r.calculations and r.evidence_records == []


def test_replay_mode_with_missing_snapshot_does_not_fall_back(tmp_path):
    s = svc(tmp_path)  # snapshot_dir is an empty tmp dir
    draft = ps_draft()
    r = s.evaluate(draft, confirmed(draft), sec_mode="replay")
    assert r.provider_errors[0].code == "snapshot_missing"
    assert r.evidence_records == [] and r.data_modes["sec_filings"] == "unavailable"


def test_unknown_ticker_in_provider(tmp_path):
    s = svc(tmp_path)
    draft = ps_draft(ticker="ZZZZ")
    r = s.evaluate(draft, confirmed(draft), sec_mode="synthetic")
    assert r.provider_errors[0].code == "unknown_ticker"
    assert r.calculations


def test_rate_limit_and_forbidden_errors_are_structured(tmp_path):
    for err in (
        FetchError("rate_limited", "429", url=SUB, http_status=429, retry_after_seconds=60),
        FetchError("forbidden", "403", url=SUB, http_status=403),
        FetchError("server_error", "503", url=SUB, http_status=503),
    ):
        fake = FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, SUB: err}, data_mode=DataMode.LIVE)
        s = svc(tmp_path, factory=lambda mode, source, f=fake: f)
        draft = ps_draft(ticker="TEST")
        r = s.evaluate(draft, confirmed(draft), sec_mode="live")
        pe = r.provider_errors[0]
        assert (pe.code, pe.http_status) == (err.code, err.http_status)
        assert pe.retry_after_seconds == err.retry_after_seconds
        assert r.calculations


def test_unknown_mode_is_config_error(tmp_path):
    draft = ps_draft()
    r = svc(tmp_path).evaluate(draft, confirmed(draft), sec_mode="bogus")
    assert r.provider_errors[0].code == "config_error"
