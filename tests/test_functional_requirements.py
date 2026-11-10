"""Functional requirements of A2 Acquire Dated Evidence, one test per requirement ID.

The test name starts with the requirement ID as Innoslate numbers it (SR.1.2.1 -> test_SR_1_2_1_...):

    SR.1.2 and SR.1.2.1 to SR.1.2.5   system functional requirements of A2 and of A2.1 to A2.5 (SRD)
    CR.2.x.y                          component requirements of C.2 Evidence Acquisition Subsystem (CRS.C2)

docs/traceability.md lists each test beside its function, module and requirement. All data here is SYNTHETIC
(hand-written snapshots in examples/sec_synthetic_snapshots); no test touches the network.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tickercase import acquisition, http_client
from tickercase.acquisition import AcquiredEvidence, acquire_dated_evidence
from tickercase.api import create_app
from tickercase.http_client import FetchError, LiveHttpClient, TransportResponse
from tickercase.models import CaseStatus, DataMode
from tickercase.providers.sec import TICKERS_URL
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, ROOT, make_settings, offline_factory, ps_draft

CONTEXT_HOSTS = ("gamma-api.polymarket.com", "production.dataviz.cnn.io", "news.google.com", "/v1/finance/search")
PUBLIC_HOSTS = ("sec.gov", "finance.yahoo.com", "polymarket.com", "cnn.io", "news.google.com")


class FailingSource:
    """A fetcher whose requests fail when the URL contains one of the given parts (an injected source fault)."""

    def __init__(self, inner, parts, code="timeout"):
        self.inner, self.parts, self.code = inner, parts, code
        self.data_mode = inner.data_mode

    def get_json(self, url):
        if any(part in url for part in self.parts):
            raise FetchError(self.code, f"injected {self.code}", url=url, data_mode=self.data_mode)
        return self.inner.get_json(url)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def service(tmp_path, failing=(), code="timeout"):
    settings = make_settings(tmp_path)
    base = offline_factory(settings)
    factory = (lambda mode, source: FailingSource(base(mode, source), failing, code)) if failing else base
    return CaseService(settings, fetcher_factory=factory, store=CaseStore(tmp_path / "cases"), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)


def confirmed(draft):
    return confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW)


def acquire(tmp_path, failing=(), code="timeout") -> AcquiredEvidence:
    draft = ps_draft()
    status, acquired = service(tmp_path, failing, code).acquire(draft, confirmed(draft), sec_mode="synthetic")
    assert acquired is not None, status
    return acquired


def stamped_records(evidence_set):
    """Every record of the evidence set that came from a source response."""
    f, m, c = evidence_set.filing, evidence_set.market, evidence_set.context
    singles = [f.reported_facts, f.insiders, m.price_history, m.options, m.price_base_rate, m.earnings, c.sentiment, c.news]
    return list(f.records) + [r for r in singles if r is not None]


# ---------------------------------------------------------------- SR.1.2 (A2) and SR.1.2.1 to SR.1.2.5 (A2.1 to A2.5)


def test_SR_1_2_acquires_the_evidence_set_for_the_confirmed_claim_from_the_public_sources(tmp_path):
    """SR.1.2 TickerCase shall acquire the evidence set for the confirmed claim from the public sources. (A2)"""
    acquired = acquire(tmp_path)
    ev = acquired.evidence_set
    assert ev.filing.found and ev.filing.records and ev.filing.reported_facts is not None       # A2.1 Filing Records
    assert ev.market.price_history is not None and ev.market.options is not None                # A2.2 Market Records
    assert ev.context.sentiment is not None and ev.context.news is not None                     # A2.3 Context Records
    assert acquired.source_error_records == []
    assert acquired.evidence_query and all(url.startswith("https://") and any(h in url for h in PUBLIC_HOSTS) for url in acquired.evidence_query)
    # the claim's ticker decides what is asked: the evidence is of this claim
    assert {r.ticker for r in ev.filing.records} == {"SYNT"} and ev.market.price_history.symbol == "SYNT"

    # without a confirmation no source is asked at all
    draft = ps_draft()
    status, nothing = service(tmp_path).acquire(draft, None, sec_mode="synthetic")
    assert status is CaseStatus.BLOCKED_UNCONFIRMED and nothing is None
    status, nothing = service(tmp_path).acquire(ps_draft(target_price="120"), confirmed(draft), sec_mode="synthetic")
    assert status is CaseStatus.BLOCKED_CONFIRMATION_STALE and nothing is None


def test_SR_1_2_1_attaches_the_provenance_record_to_the_evidence_record(tmp_path):
    """SR.1.2.1 TickerCase shall attach the provenance record to the evidence record. (A2.4, SS.2.4)"""
    ev = acquire(tmp_path).evidence_set
    records = stamped_records(ev)
    assert len(records) > 8
    for record in records:
        url = getattr(record, "source_url", None) or getattr(record, "source_response_url", None) or getattr(record, "source_urls", None)
        assert url, f"{type(record).__name__} has no source URL"
        assert record.retrieved_at is not None, f"{type(record).__name__} has no capture time"
        assert record.data_mode is DataMode.SYNTHETIC, f"{type(record).__name__} has no data mode"
    # the stamp of every group of records is stated with the evidence set
    assert set(ev.data_modes) == {"sec_filings", "sec_facts", "market_prices", "options", "insiders", "fear_greed", "news", "earnings_date"}


def test_SR_1_2_2_converts_the_failed_source_request_into_the_source_error_record(tmp_path):
    """SR.1.2.2 TickerCase shall convert the failed source request into the source error record. (A2.5, SS.2.5)"""
    for code in ("timeout", "forbidden", "rate_limited", "invalid_json"):
        acquired = acquire(tmp_path, failing=("companyfacts",), code=code)
        errors = acquired.source_error_records
        assert [(e.provider_id, e.code) for e in errors] == [("sec_xbrl_companyfacts", code)]
        assert "companyfacts" in errors[0].url and errors[0].data_mode is not None
        assert acquired.guard.steps["sec_facts"] == "failed" and acquired.evidence_set.filing.reported_facts is None
        assert acquired.evidence_set.data_modes["sec_facts"] == "unavailable"  # nothing is substituted for the failed source


def test_SR_1_2_3_sends_the_declared_contact_user_agent_with_the_filing_service_request():
    """SR.1.2.3 TickerCase shall send the declared contact User-Agent with the filing service request. (A2.1, SS.2.1)"""
    sent = []

    def transport(url, headers, timeout):
        sent.append((url, dict(headers)))
        return TransportResponse(200, {}, b"{}")

    contact = "TickerCase/0.13 Team 12 team12@example.org"
    LiveHttpClient(user_agent=contact, min_interval_seconds=0.0, transport=transport).get_json(TICKERS_URL)
    assert sent == [(TICKERS_URL, {**sent[0][1], "User-Agent": contact})]


def test_SR_1_2_4_requests_the_option_chain_of_the_claim_ticker(tmp_path):
    """SR.1.2.4 TickerCase shall request the option chain of the claim ticker. (A2.2, SS.2.2)"""
    acquired = acquire(tmp_path)
    assert any("/finance/options/SYNT" in url for url in acquired.evidence_query)
    chain = acquired.evidence_set.market.options
    assert chain is not None and chain.symbol == "SYNT" and acquired.guard.steps["options"] == "ok"


def test_SR_1_2_5_excludes_the_context_signal_from_the_verdict_rules(tmp_path):
    """SR.1.2.5 TickerCase shall exclude the context signal from the verdict rules. (A2.3, SS.2.3)"""
    draft = ps_draft()
    with_context = service(tmp_path / "a").evaluate(draft, confirmed(draft), sec_mode="synthetic")
    without = service(tmp_path / "b", failing=CONTEXT_HOSTS).evaluate(draft, confirmed(draft), sec_mode="synthetic")
    assert with_context.sentiment is not None and with_context.news is not None
    assert without.sentiment is None and without.news is None and without.prediction_markets is None
    assert without.verdict.label == with_context.verdict.label
    assert [(i.id, i.stance) for i in without.evidence_items] == [(i.id, i.stance) for i in with_context.evidence_items]
    assert (without.oracle.low, without.oracle.high) == (with_context.oracle.low, with_context.oracle.high)
    assert acquire(tmp_path / "c").evidence_set.context.label == "context_only"


# ---------------------------------------------------------------- CRS.C2 component requirements of C.2


def test_CR_2_0_2_excludes_the_paid_data_services_from_the_evidence_requests(tmp_path):
    """CR.2.0.2 The Evidence Acquisition Subsystem shall exclude the paid data services from the evidence requests."""
    hosts = {re.match(r"https://([^/]+)/", url).group(1) for url in acquire(tmp_path).evidence_query}
    assert hosts and all(any(host.endswith(free) for free in PUBLIC_HOSTS) for host in hosts), hosts
    assert not any("anthropic" in host for host in hosts)
    assert acquisition.SOURCES == ("sec", "market", "options", "web")


def test_CR_2_1_2_refuses_the_live_filing_request_without_the_contact_address():
    """CR.2.1.2 The SEC EDGAR Client shall refuse the live filing request without the contact address."""
    sent = []
    for user_agent in (None, "", "TickerCase/0.13 no contact"):
        client = LiveHttpClient(user_agent=user_agent, transport=lambda *a: sent.append(a))
        with pytest.raises(FetchError) as refused:
            client.get_json(TICKERS_URL)
        assert refused.value.code == "missing_user_agent"
    assert sent == []  # refused before anything is sent


def test_CR_2_4_1_serves_the_recorded_response_in_replay_mode_without_the_live_request(tmp_path, monkeypatch):
    """CR.2.4.1 The HTTP Fetcher shall serve the recorded response in replay mode without the live request."""
    def no_network(*args, **kwargs):
        raise AssertionError("a live request was sent in replay mode")

    monkeypatch.setattr(http_client, "urlopen", no_network)
    acquired = acquire(tmp_path)
    assert acquired.source_error_records == [] and len(acquired.evidence_query) > 10
    # the original capture time of the recording is kept beside the time of this replay
    assert all(r.source_captured_at is not None for r in acquired.evidence_set.filing.records)


def test_CR_2_4_2_uses_the_case_data_mode_for_the_case_requests(tmp_path):
    """CR.2.4.2 The HTTP Fetcher shall use the case data mode for the case requests."""
    ev = acquire(tmp_path).evidence_set
    assert set(ev.data_modes.values()) == {"synthetic"}
    assert {r.data_mode for r in stamped_records(ev)} == {DataMode.SYNTHETIC}


def test_CR_2_5_1_completes_the_remaining_source_requests_despite_the_failed_request(tmp_path):
    """CR.2.5.1 The Source Guard shall complete the remaining source requests despite the failed request."""
    acquired = acquire(tmp_path, failing=("sec.gov",))  # the whole filing service is down
    steps = acquired.guard.steps
    assert {steps[s] for s in ("sec_filings", "sec_facts", "insiders")} == {"failed"}
    assert {steps[s] for s in ("price_history", "options", "risk_free_rate", "price_base_rate", "benchmarks", "earnings_date", "fear_greed")} == {"ok"}
    assert acquired.evidence_set.market.price_history is not None and acquired.evidence_set.context.sentiment is not None
    assert {e.provider_id for e in acquired.source_error_records} >= {"sec_edgar_submissions", "sec_xbrl_companyfacts"}


# ---------------------------------------------------------------- the model, the module and the contract agree


def test_A2_signature_is_built_from_its_ICOM():
    """The parameters of acquire_dated_evidence are the inputs and controls of the A2 box; its result holds the outputs."""
    import inspect

    names = list(inspect.signature(acquire_dated_evidence).parameters)
    assert names[:4] == ["confirmed_claim", "source_evidence", "data_mode_setting", "provider_access_terms"]
    outputs = set(AcquiredEvidence.__dataclass_fields__)
    assert {"evidence_query", "evidence_set", "source_error_records"} <= outputs
    doc = acquisition.__doc__
    assert "Function ID: A2" in doc and all(f"A2.{n}" in doc for n in range(1, 6)) and acquisition.FUNCTION_ID == "A2"


def test_contract_operations_of_the_A2_internal_flows_are_served(tmp_path):
    """Every operation in contracts/api-v1.yaml is served by the API, and the three internal flows answer."""
    contract = (ROOT / "contracts" / "api-v1.yaml").read_text(encoding="utf-8")
    declared = set(re.findall(r"operationId:\s*(\w+)", contract))
    client = TestClient(create_app(service(tmp_path)))
    served = {op["operationId"] for path in client.get("/openapi.json").json()["paths"].values() for op in path.values()}
    assert declared and declared <= served, declared - served
    assert {"acquireDatedEvidence", "requestFilingsAndReportedFacts", "requestMarketAndOptionData", "requestContextSignals"} <= declared

    draft = ps_draft().model_dump()
    confirmation = client.post("/claims/confirm", json=draft).json()
    body = {"draft": draft, "confirmation": confirmation, "sec_mode": "synthetic"}
    for path, function_id, flow_id, field in (("filings", "A2.1", "IO.37", "filing_records"), ("market", "A2.2", "IO.38", "market_records"),
                                              ("context", "A2.3", "IO.39", "context_records")):
        r = client.post(f"/evidence/{path}", json=body)
        assert r.status_code == 200, r.text
        flow = r.json()
        assert (flow["function_id"], flow["flow_id"]) == (function_id, flow_id) and flow[field] and flow["evidence_query"]
    whole = client.post("/evidence", json=body)
    assert whole.status_code == 200 and whole.json()["function_id"] == "A2" and whole.json()["evidence_set"]["data_modes"]
    assert client.post("/evidence", json={"draft": draft, "sec_mode": "synthetic"}).status_code == 409  # not confirmed: nothing is asked
