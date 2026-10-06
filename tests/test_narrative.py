"""AI narrative: fact table, request shape, verification, failure paths. No network: a fake client stands in for Claude."""

import json
from types import SimpleNamespace

import pytest

from tickercase.narrative import MODEL, SECTIONS, build_facts, cache_key, estimate_cost, write_narrative
from tickercase.service import CaseService
from tickercase.storage import CaseStore
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
        {"text": f"目标价 {target.value}，概率下限约 {float(low.value) * 100:.1f}%。", "fact_ids": [target.id, low.id]},
        {"text": "市场情绪的含义需要结合其他信号看。", "fact_ids": []},
    ]
    payload["conclusion"] = [{"text": "概率是 37.5%。", "fact_ids": [low.id]}]  # invented number
    client = FakeClient(payload)
    n = write_narrative(result, client=client, now=lambda: FIXED_NOW)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema" and call["output_config"]["effort"] == "medium"
    assert "Simplified Chinese" in call["system"] and "{language_rule}" not in call["system"]
    content = call["messages"][0]["content"]
    assert "Fact table" in content and low.label_zh in content and low.label_en not in content  # one language's labels only
    assert n.status == "ok" and n.total == 3 and n.language == "zh" and n.effort == "medium" and n.cache_key
    assert n.sections["logic_chain"][0].en == ""
    statuses = [s.status for s in n.sections["logic_chain"]] + [s.status for s in n.sections["conclusion"]]
    assert statuses == ["verified", "qualitative", "unsupported"]
    assert n.unsupported == 1 and n.verified == 2
    assert n.usage == {"input_tokens": 5000, "output_tokens": 3000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert estimate_cost("claude-opus-5-5", n.usage) == pytest.approx(0.08)


def test_request_follows_model_and_language(case):
    _, result = case
    payload = {name: [] for name in SECTIONS}
    sonnet = FakeClient(payload)
    n = write_narrative(result, client=sonnet, now=lambda: FIXED_NOW, model="claude-sonnet-5-5", effort="low", language="en")
    call = sonnet.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["output_config"]["effort"] == "low" and call["fallbacks"] == "default"
    assert "natural English" in call["system"]
    haiku = FakeClient(payload)
    n = write_narrative(result, client=haiku, now=lambda: FIXED_NOW, model="claude-haiku-4-5", effort="low", language="en")
    call = haiku.calls[0]
    assert "effort" not in call["output_config"] and "fallbacks" not in call and "betas" not in call
    assert n.effort is None
    facts = build_facts(result)
    keys = {cache_key(facts, model=m, effort=e, language=lang) for m, e, lang in
            (("claude-opus-5-5", "medium", "zh"), ("claude-opus-5-5", "low", "zh"), ("claude-opus-5-5", "medium", "en"), ("claude-sonnet-5-5", "medium", "zh"))}
    assert len(keys) == 4
    assert cache_key(facts, model="claude-haiku-4-5", effort="low", language="zh") == cache_key(facts, model="claude-haiku-4-5", effort="high", language="zh")


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
    assert updated.narrative.status == "ok" and updated.narratives["zh"] is updated.narrative
    import anthropic

    def boom(*a, **k):
        raise RuntimeError("no credentials")
    monkeypatch.setattr(anthropic, "Anthropic", boom)
    again = svc.narrate(result, language="en")
    assert again.narratives["en"].status == "not_configured" and "no credentials" in again.narratives["en"].error
    assert again.narratives["zh"].status == "ok"  # the other language is kept


def test_service_reuses_narrative_for_same_facts(case, tmp_path):
    svc, result = case
    svc.store = CaseStore(tmp_path / "cases")
    payload = {name: [] for name in SECTIONS}
    payload["conclusion"] = [{"text": "结论。", "fact_ids": []}]
    first = FakeClient(payload)
    svc.narrate(result, client=first)
    assert len(first.calls) == 1

    same = FakeClient(payload)  # the same narrative asked again: no call
    svc.narrate(result, client=same)
    assert same.calls == [] and result.narratives["zh"].reused_from is None

    twin = result.model_copy(deep=True, update={"case_id": "f" * 32, "narratives": {}, "narrative": None})
    svc.narrate(twin, client=same)  # another case with identical facts reuses the stored one
    assert same.calls == [] and twin.narratives["zh"].reused_from == result.case_id

    svc.narrate(twin, client=same, language="en")  # a different language is a new call
    svc.narrate(twin, client=same, force=True)  # and "write again" always calls
    assert len(same.calls) == 2 and twin.narratives["zh"].reused_from is None


def test_verifier_reads_english_scale_words_labels_sources_and_claim_context():
    from tickercase.models import Fact, NarrativeSentence
    from tickercase.narrative import verify

    facts = [
        Fact(id="F01", label_en="target price", label_zh="目标价", value="300", source="claim"),
        Fact(id="F02", label_en="target market cap", label_zh="目标市值", value="7040802746700"),
        Fact(id="F03", label_en="option-implied P(touch 358.35) (+50%)", label_zh="期权隐含", value="0.53911"),
        Fact(id="F04", label_en="M4 P(touch)", label_zh="M4", value="0.919192", source="208 of 297 windows since 1999-02-01 rose at least 26%"),
        Fact(id="F05", label_en="option-implied P(end >= 119.45)", label_zh="期权隐含", value="0.648854"),
    ]
    sentences = [
        NarrativeSentence(en="At $300 the company would be worth about $7.04 trillion.", fact_ids=["F02"]),  # $300 is claim context
        NarrativeSentence(en="Options give about 53.9% for touching roughly $358 (+50%).", fact_ids=["F03"]),
        NarrativeSentence(en="208 of 297 windows since 1999 rose at least 26%, a 91.9% touch rate.", fact_ids=["F04"]),
        NarrativeSentence(en="About a 35% chance of ending below half of today's price.", fact_ids=["F05"]),  # derived: 1 - 0.649
    ]
    verify(sentences, facts)
    assert [s.status for s in sentences] == ["verified", "verified", "verified", "unsupported"]


def test_verifier_reads_losses_and_falls_written_as_positive_amounts():
    from tickercase.models import Fact, NarrativeSentence
    from tickercase.narrative import verify

    facts = [
        Fact(id="F01", label_en="annual net income FY ending 2024-12-31", label_zh="年净利润（截至 2024-12-31）", value="-182600000", unit="USD"),
        Fact(id="F02", label_en="E1 reported annual change", label_zh="E1 已披露年变化", value="-0.17"),
    ]
    sentences = [
        NarrativeSentence(zh="公司仍在亏损，净亏损约 1.83 亿美元。", fact_ids=["F01"]),
        NarrativeSentence(en="Revenue fell 17% a year.", fact_ids=["F02"]),
        NarrativeSentence(en="Net income was $182.6 million.", fact_ids=["F01"]),  # no loss word: the sign must match
        NarrativeSentence(en="Revenue grew 17% a year.", fact_ids=["F02"]),  # the wrong direction stays flagged
    ]
    verify(sentences, facts)
    assert [s.status for s in sentences] == ["verified", "verified", "unsupported", "unsupported"]


def test_recheck_gives_older_narratives_todays_verdicts():
    from datetime import datetime, timezone

    from tickercase.models import Fact, Narrative, NarrativeSentence
    from tickercase.narrative import recheck

    facts = [Fact(id="F01", label_en="target price", label_zh="目标价", value="300", source="claim"),
             Fact(id="F02", label_en="insider open-market sale value", label_zh="内部人卖出金额", value="1430996244.02", unit="USD")]
    stale = [NarrativeSentence(en="Insiders sold about $1.43 billion; the claim is $300.", fact_ids=["F02"], status="unsupported",
                               problems=["1.43: no matching fact", "300: matches a fact the sentence does not cite"])]
    old = Narrative(status="ok", model="claude-opus-5-5", created_at=datetime(2026, 10, 5, tzinfo=timezone.utc), language="en",
                    sections={"divergences": stale}, facts=facts, total=1, verified=0, unsupported=1)
    new = recheck(old)
    assert (new.verified, new.unsupported, new.total) == (1, 0, 1)
    assert new.sections["divergences"][0].status == "verified" and not new.sections["divergences"][0].problems
    assert old.sections["divergences"][0].status == "unsupported"  # the stored narrative itself is not changed
    assert recheck(old.model_copy(update={"facts": []})) is not None and recheck(old.model_copy(update={"status": "error"})).verified == 0


def test_reused_narrative_is_checked_again(case):
    svc, result = case
    target = _fact(build_facts(result), "target price")
    payload = {name: [] for name in SECTIONS}
    payload["conclusion"] = [{"text": f"目标价 {target.value}。", "fact_ids": [target.id]}]
    svc.narrate(result, client=FakeClient(payload))
    stored = result.narratives["zh"]
    stale = stored.model_copy(update={"verified": 0, "unsupported": 1, "sections": {
        "conclusion": [stored.sections["conclusion"][0].model_copy(update={"status": "unsupported", "problems": ["old rules"]})]}})
    result.narratives["zh"] = stale
    again = FakeClient(payload)
    svc.narrate(result, client=again)  # same facts: reused without a call, but with today's verdicts
    assert again.calls == [] and result.narratives["zh"].verified == 1 and result.narratives["zh"].unsupported == 0
