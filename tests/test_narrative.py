"""AI narrative: fact table, request shape, verification, failure paths. No network: a fake client stands in for Claude."""

import json
from types import SimpleNamespace

import pytest

from tickercase.narrative import MODEL, SECTIONS, build_facts, write_narrative
from tickercase.service import CaseService
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft


@pytest.fixture
def case(tmp_path):
    svc = CaseService(make_settings(tmp_path), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft(ticker="SYNT", current_shares="95000000")
    return svc, svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


class FakeClient:
    def __init__(self, payload=None, stop_reason="end_turn", raw_text=None):
        self.calls = []
        text = raw_text if raw_text is not None else json.dumps(payload, ensure_ascii=False)
        self._response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason, model=MODEL,
                                         usage=SimpleNamespace(input_tokens=5000, output_tokens=3000))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _fact(facts, label_start):
    return next(f for f in facts if f.label_en.startswith(label_start))


def test_fact_table_has_sources_and_probabilities(case):
    _, result = case
    facts = build_facts(result)
    ids = [f.id for f in facts]
    assert len(ids) == len(set(ids)) and ids[0] == "F01"
    assert _fact(facts, "probability range low").value and _fact(facts, "M1 ").source
    assert any(f.label_en.startswith("insider open-market sales") for f in facts)


def test_narrative_request_and_verification(case):
    _, result = case
    facts = build_facts(result)
    low = _fact(facts, "probability range low")
    target = _fact(facts, "target price")
    payload = {name: [] for name in SECTIONS}
    payload["logic_chain"] = [
        {"zh": f"目标价 {target.value}，概率下限约 {float(low.value) * 100:.1f}%。", "en": f"Target {target.value}; lower bound about {float(low.value) * 100:.1f}%.",
         "fact_ids": [target.id, low.id]},
        {"zh": "市场情绪的含义需要结合其他信号看。", "en": "Market mood needs the other signals for context.", "fact_ids": []},
    ]
    payload["conclusion"] = [{"zh": "概率是 37.5%。", "en": "The probability is 37.5%.", "fact_ids": [low.id]}]  # invented number
    client = FakeClient(payload)
    n = write_narrative(result, client=client, now=lambda: FIXED_NOW)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "Fact table" in call["messages"][0]["content"]
    assert n.status == "ok" and n.total == 3
    statuses = [s.status for s in n.sections["logic_chain"]] + [s.status for s in n.sections["conclusion"]]
    assert statuses == ["verified", "qualitative", "unsupported"]
    assert n.unsupported == 1 and n.verified == 2
    assert n.usage == {"input_tokens": 5000, "output_tokens": 3000}


def test_refusal_and_bad_json_are_reported(case):
    _, result = case
    refused = write_narrative(result, client=FakeClient({}, stop_reason="refusal"), now=lambda: FIXED_NOW)
    assert refused.status == "refused"
    broken = write_narrative(result, client=FakeClient(raw_text="not json"), now=lambda: FIXED_NOW)
    assert broken.status == "error" and "schema" in broken.error


def test_service_stores_narrative_and_handles_missing_credentials(case, monkeypatch):
    svc, result = case
    payload = {name: [] for name in SECTIONS}
    updated = svc.narrate(result, client=FakeClient(payload))
    assert updated.narrative.status == "ok"
    import anthropic

    def boom(*a, **k):
        raise RuntimeError("no credentials")
    monkeypatch.setattr(anthropic, "Anthropic", boom)
    again = svc.narrate(result)
    assert again.narrative.status == "not_configured" and "no credentials" in again.narrative.error
