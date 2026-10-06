"""AI-written narrative with deterministic number checking.

1. ``build_facts`` turns a CaseResult into a numbered fact table (F01, F02, ...);
   every value was fetched or computed by TickerCase and carries its source.
2. ``write_narrative`` asks Claude for a narrative in one language as structured
   JSON: sections of sentences, each listing the fact ids it relies on.
3. ``verify`` checks every number in every sentence against the facts it cites
   (allowing the unit changes a writer makes: %, 亿, 万亿, B, M, billion, x, and a
   loss or fall written as a positive amount when the sentence says so). A number
   with no matching fact marks the sentence "unsupported"; the page shows it.
4. ``recheck`` runs the same check again on a stored narrative, so a narrative
   written under older rules is shown with today's verdicts.

The model only phrases and connects the facts. It may not introduce numbers,
and the verifier makes any it does introduce visible.

Token use is kept down on purpose: one language per call, a compact fact table,
effort set from settings, and ``cache_key`` lets the service reuse a narrative
already written for the same facts instead of calling again.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from .models import CaseResult, Fact, Narrative, NarrativeSentence

MODEL = "claude-opus-5-5"
EFFORT = "medium"
LANGUAGES = ("zh", "en")
PROMPT_VERSION = "2"  # part of the cache key; bump when the prompt, schema or fact table format changes
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1")  # accept fallbacks="default"
# USD per million tokens: input, output, cache read (Claude API list prices, 2026-09); cache writes cost 1.25x input
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-4-5": (1.0, 5.0, 0.10)}
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
SECTIONS = ("logic_chain", "resonance", "divergences", "conclusion", "upside", "downside")
SECTION_TITLES = {
    "logic_chain": ("Core logic", "核心逻辑链"), "resonance": ("Signals that agree", "共振信号"),
    "divergences": ("Key divergences", "关键分歧"), "conclusion": ("Conclusion", "结论"),
    "upside": ("Upside risks", "上行风险"), "downside": ("Downside risks", "下行风险"),
}

SYSTEM_PROMPT = """You write the narrative part of a stock-claim research report for readers without a finance background.
You receive a table of facts. Each fact has an id, a label, a value and a source; every value was fetched from a public source or computed by deterministic code.

Rules:
- Use only numbers that appear in the fact table. You may convert units (0.052 -> 5.2%, 42160000000 -> 421.6亿 / 42.16B) and round, but do not compute new numbers, estimate, or bring in outside knowledge (no news, no company plans, no history not in the table).
- Every sentence lists in fact_ids the ids of every fact whose number or claim it uses. A sentence with no number may list the facts it interprets.
- The probability statements must be the computed ones (range, methods). Do not give your own probability.
- Explain what each signal means in plain words, where signals agree, where they disagree and why (they often measure different things), and what would change the picture.
- {language_rule}
- Keep it tight: logic_chain 3-5 sentences, the other sections 2-4 sentences each.
- This is research, not investment advice; do not tell the reader to buy or sell."""


LANGUAGE_RULE = {"zh": "Write every sentence in natural Simplified Chinese.", "en": "Write every sentence in natural English."}


class _OutSentence(BaseModel):
    text: str
    fact_ids: list[str]


class _Out(BaseModel):
    logic_chain: list[_OutSentence]
    resonance: list[_OutSentence]
    divergences: list[_OutSentence]
    conclusion: list[_OutSentence]
    upside: list[_OutSentence]
    downside: list[_OutSentence]


def _schema() -> dict:
    sentence = {"type": "object", "properties": {"text": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["text", "fact_ids"], "additionalProperties": False}
    return {"type": "object", "properties": {s: {"type": "array", "items": sentence} for s in SECTIONS},
            "required": list(SECTIONS), "additionalProperties": False}


# ------------------------------------------------------------------ facts


class _Facts:
    def __init__(self) -> None:
        self.items: list[Fact] = []

    def add(self, label_en: str, label_zh: str, value: Any, unit: str = "", source: str = "") -> None:
        if value is None:
            return
        text = format(value, "f") if isinstance(value, Decimal) else (f"{value:.6g}" if isinstance(value, float) else str(value))
        self.items.append(Fact(id=f"F{len(self.items) + 1:02d}", label_en=label_en, label_zh=label_zh, value=text, unit=unit, source=source))


def build_facts(r: CaseResult) -> list[Fact]:
    f = _Facts()
    c = r.confirmed_claim.values if r.confirmed_claim else None
    if c is None:
        return []
    f.add("claim", "观点原文", c.claim_text, source="user input")
    f.add("ticker", "股票代码", c.ticker, source="user input")
    f.add("target price", "目标价", c.target_price, c.currency, "claim")
    f.add("horizon", "时间范围", c.horizon_years, "years", "claim")
    if r.oracle:
        o = r.oracle
        f.add("target date", "目标日期", o.target_date.isoformat(), source="reference date + horizon")
        f.add("claim condition", "观点条件", "touch the target at any time before the date" if o.condition == "touch" else "at or above the target on the date",
              source="claim wording, confirmed by the user")
        f.add("probability range low", "概率区间下限", o.low, "probability 0-1", "TickerCase cross-check of methods")
        f.add("probability range high", "概率区间上限", o.high, "probability 0-1", "TickerCase cross-check of methods")
        f.add("probability tier", "概率等级", o.tier_label.en, source="TickerCase")
        f.add("risk-free rate", "无风险利率", o.risk_free_rate, "rate 0-1", "10-year Treasury yield (^TNX)")
        for m in o.methods:
            if m.status != "ok":
                continue
            f.add(f"{m.id} {m.name.en}: P(end >= target)", f"{m.id} {m.name.zh}：到期 ≥ 目标的概率", m.probability, "probability 0-1", m.detail.en)
            if m.touch_probability is not None:
                f.add(f"{m.id} {m.name.en}: P(touch target before date)", f"{m.id} {m.name.zh}：期间触及概率", m.touch_probability, "probability 0-1", m.detail.en)
        for row in o.ladder:
            if row.options_p is not None:
                f.add(f"option-implied P(end >= {row.level}) ({row.label.en})", f"期权隐含 到期 ≥ {row.level}（{row.label.zh}）的概率", row.options_p, "probability 0-1", "option chain")
            if row.options_touch is not None:
                f.add(f"option-implied P(touch {row.level}) ({row.label.en})", f"期权隐含 期间触及 {row.level}（{row.label.zh}）的概率", row.options_touch, "probability 0-1", "option chain")
    if r.market:
        m = r.market
        f.add("latest close", "最新收盘价", m.last_close, m.currency or "", f"{m.provider_id} {m.last_date}")
        f.add("historical volatility", "历史年化波动率", m.annualized_volatility, "rate 0-1", m.volatility_window)
    for name in ("required_return", "annualized_price_return", "target_market_cap", "required_annual_revenue", "required_annual_net_income", "target_assumed_shares"):
        item = next((x for x in r.calculations if x.name == name and x.status == "ok"), None)
        if item is not None:
            f.add(name.replace("_", " "), name, item.value, item.unit, f"formula: {item.formula}")
    for e in r.evidence_items:
        for key in ("required_cagr_from_reported", "reported_cagr", "current_multiple", "current_market_cap", "reported_annual_change", "implied_annual_change", "past_annualized_price_change"):
            if key in e.measured:
                f.add(f"{e.id} {key.replace('_', ' ')}", f"{e.id} {e.title_zh} · {key}", Decimal(e.measured[key]), "", e.title)
    # two ratios writers use; computed here so they have a source
    cap_t = next((x.value for x in r.calculations if x.name == "target_market_cap" and x.status == "ok"), None)
    e2 = next((e for e in r.evidence_items if e.id == "E2" and "current_market_cap" in e.measured), None)
    if cap_t is not None and e2 is not None and Decimal(e2.measured["current_market_cap"]) > 0:
        f.add("target market cap / current market cap", "目标市值 ÷ 当前市值", (cap_t / Decimal(e2.measured["current_market_cap"])).quantize(Decimal("0.01")), "times", "computed")
    req = next((x.value for x in r.calculations if x.name in ("required_annual_revenue", "required_annual_net_income") and x.status == "ok"), None)
    e1 = next((e for e in r.evidence_items if e.id == "E1"), None)
    latest_key = next((k for k in (e1.measured if e1 else {}) if k.startswith("latest_reported_")), None)
    if req is not None and latest_key and Decimal(e1.measured[latest_key]) > 0:
        f.add("required annual metric / latest reported", "所需年度指标 ÷ 最近披露值", (req / Decimal(e1.measured[latest_key])).quantize(Decimal("0.01")), "times", "computed")
    facts = r.reported_facts
    if facts:
        for label_en, label_zh, pts in (("annual revenue", "年营收", facts.revenue), ("annual net income", "年净利润", facts.net_income)):
            for p in pts[-3:]:
                f.add(f"{label_en} FY ending {p.period_end}", f"{label_zh}（截至 {p.period_end}）", p.value, "USD", f"SEC XBRL {p.concept}, {p.form} filed {p.filed}")
        if facts.shares_outstanding:
            s = facts.shares_outstanding[-1]
            f.add(f"shares outstanding as of {s.period_end}", f"已披露股份数（{s.period_end}）", s.value, "shares", f"SEC XBRL, {s.form} filed {s.filed}")
    if r.options:
        o = r.options
        f.add("option expiry used", "使用的期权到期日", o.expiry.isoformat(), source="Yahoo option chain")
        f.add("at-the-money implied volatility", "平值隐含波动率", o.atm_iv, "rate 0-1", "Yahoo option chain")
        f.add("implied volatility at target strike", "目标行权价隐含波动率", o.target_iv, "rate 0-1", "Yahoo option chain" + (" (extrapolated)" if o.target_iv_extrapolated else ""))
        f.add("highest listed strike", "最高行权价", o.max_strike, "", "Yahoo option chain")
        f.add("call open interest at or above target", "目标价及以上的看涨未平仓合约", o.oi_at_or_above_target, "contracts", "Yahoo option chain")
    if r.base_rate and r.base_rate.companies:
        b = r.base_rate
        f.add("base-rate companies compared", "基准率比较公司数", b.companies, "companies", f"SEC frames {b.start_year}-{b.end_year}")
        f.add("base-rate companies reaching required growth", "达到所需增速的公司数", b.achieved, "companies", f"SEC frames {b.start_year}-{b.end_year}")
        f.add("base-rate median growth", "同规模公司增速中位数", b.median_cagr, "rate 0-1 per year", "SEC frames")
        f.add("base-rate start year", "基准率起始年", b.start_year, source="SEC frames")
        f.add("base-rate end year", "基准率结束年", b.end_year, source="SEC frames")
    if r.price_base_rate and r.price_base_rate.windows:
        p = r.price_base_rate
        f.add("own-history windows", "本股历史窗口数", p.windows, "windows", p.source_url)
        f.add("own-history windows reaching the required rise", "达到所需涨幅的窗口数", p.hits, "windows", p.source_url)
        f.add("own-history best window return", "本股历史最好窗口涨幅", p.best_return, "rate", p.source_url)
    for bm in r.benchmarks:
        f.add(f"{bm.label} return {bm.start} to {bm.end}", f"{bm.label} 涨幅（{bm.start} 至 {bm.end}）", bm.total_return, "rate", "Yahoo monthly closes")
    if r.insiders:
        i = r.insiders
        f.add("insider open-market purchases (12 months)", "内部人公开市场买入笔数（12 个月）", i.purchases, "transactions", "SEC Form 4")
        f.add("insider open-market purchase value", "内部人买入金额", i.purchase_value, "USD", "SEC Form 4")
        f.add("insider open-market sales (12 months)", "内部人公开市场卖出笔数（12 个月）", i.sales, "transactions", "SEC Form 4")
        f.add("insider open-market sale value", "内部人卖出金额", i.sale_value, "USD", "SEC Form 4")
        f.add("insider sales under 10b5-1 plans (value)", "10b5-1 计划内卖出金额", i.plan_sale_value, "USD", "SEC Form 4")
        f.add("insider grants (count)", "授予记录数", i.grants, "entries", "SEC Form 4")
    if r.sentiment:
        f.add("CNN Fear & Greed score", "CNN 恐惧贪婪指数", r.sentiment.score, "0-100", "CNN")
        f.add("CNN Fear & Greed a month ago", "一个月前的恐惧贪婪指数", r.sentiment.previous_month, "0-100", "CNN")
    if r.prediction_markets:
        for mk in r.prediction_markets.markets[:3]:
            f.add(f"Polymarket: {mk.question}", f"Polymarket：{mk.question}", mk.probability_yes, "probability 0-1", mk.url)
    if r.verdict:
        f.add("evidence-as-of verdict (rules-v1)", "证据结论（rules-v1）", r.verdict.display, source="TickerCase rules-v1")
    return f.items


# ------------------------------------------------------------------ verification

NUM_RE = re.compile(r"(?<![A-Za-z0-9_])[-+−]?\d[\d,]*(?:\.\d+)?\s*(万亿|亿|万|trillion|billion|million|thousand|[KkMBT](?![a-zA-Z])|%|x|倍)?")
SCALES = {"万亿": Decimal("1e12"), "亿": Decimal("1e8"), "万": Decimal("1e4"), "K": Decimal("1e3"), "k": Decimal("1e3"),
          "M": Decimal("1e6"), "B": Decimal("1e9"), "T": Decimal("1e12"),
          "thousand": Decimal("1e3"), "million": Decimal("1e6"), "billion": Decimal("1e9"), "trillion": Decimal("1e12")}
# the claim itself (target, horizon, date): every sentence may use these numbers without citing them
CONTEXT_LABELS = ("claim", "target price", "horizon", "target date")
# words that say a number is a loss or a fall; with one of them a sentence may give a negative fact as a positive amount
NEGATIVE_WORDS = re.compile(r"\b(?:loss(?:es)?|lost|losing|deficit|declin\w*|fell|fall(?:s|ing)?|drop(?:s|ped|ping)?|down|lower|"
                            r"shr[iau]nk\w*|decreas\w*|negative|minus)\b|亏损|亏|下降|下跌|跌|减少|缩减|萎缩|回落|为负|负值", re.IGNORECASE)


def numbers_in(text: str) -> list[tuple[str, Decimal, str]]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0).strip()
        unit = m.group(1) or ""
        digits = raw[: len(raw) - len(unit)].strip().replace(",", "").replace("−", "-").replace("+", "")
        try:
            out.append((raw, Decimal(digits), unit))
        except InvalidOperation:
            continue
    return out


def _fact_numbers(fact: Fact) -> list[Decimal]:
    """Numbers a sentence citing this fact may use: its value, plus numbers in its label and source
    (e.g. "touch 358.35 (+50%)", "208 of 297 windows since 1999 rose at least 26%")."""
    values = []
    for text in (fact.value, fact.label_en, fact.label_zh, fact.source):
        for raw, value, unit in numbers_in(text):
            values.append(value * SCALES.get(unit, Decimal(1)))
    try:
        values.append(Decimal(fact.value))
    except InvalidOperation:
        pass
    return values


def _matches(value: Decimal, unit: str, candidates: list[Decimal], magnitudes: bool = False) -> bool:
    """True when the written number fits one of the candidates. With magnitudes, a negative candidate also
    matches its size written as a positive amount ("a net loss of 1.83亿" for net income -183,000,000)."""
    if magnitudes:
        candidates = candidates + [-c for c in candidates if c < 0]
    if unit in SCALES:
        forms = [value * SCALES[unit]]
    elif unit == "%":
        forms = [value / 100, value]
    else:
        forms = [value, value / 100]
    for v in forms:
        for c in candidates:
            if c == 0 and v == 0:
                return True
            if c != 0:
                rel = abs(v - c) / abs(c)
                # writers round: allow 1.5% relative, or half a unit of the last shown digit
                if rel <= Decimal("0.015") or abs(v - c) <= Decimal("0.0006"):
                    return True
            for scale in (Decimal(1), Decimal(100)):  # percentages may be rounded to whole numbers
                if c != 0 and abs(v - c * scale) <= Decimal("0.51") * (1 if scale == 100 else 0) + Decimal("0.0006"):
                    return True
    return False


def verify(sentences: list[NarrativeSentence], facts: list[Fact]) -> None:
    by_id = {f.id: f for f in facts}
    all_numbers = [n for f in facts for n in _fact_numbers(f)]
    context = [n for f in facts if f.label_en in CONTEXT_LABELS for n in _fact_numbers(f)]
    for s in sentences:
        problems: list[str] = []
        bad_ids = [i for i in s.fact_ids if i not in by_id]
        if bad_ids:
            problems.append(f"cites unknown fact ids: {', '.join(bad_ids)}")
        cited = [n for i in s.fact_ids if i in by_id for n in _fact_numbers(by_id[i])] + context
        found_any = False
        elsewhere = False
        for text in (s.zh, s.en):
            loss_words = bool(NEGATIVE_WORDS.search(text))
            for raw, value, unit in numbers_in(text):
                found_any = True
                if _matches(value, unit, cited, loss_words):
                    continue
                if _matches(value, unit, all_numbers, loss_words):
                    elsewhere = True
                    problems.append(f"{raw}: matches a fact the sentence does not cite")
                else:
                    problems.append(f"{raw}: no matching fact")
        s.problems = problems
        if bad_ids or any(p.endswith("no matching fact") for p in problems):
            s.status = "unsupported"
        elif elsewhere:
            s.status = "cited_elsewhere"
        elif found_any:
            s.status = "verified"
        else:
            s.status = "qualitative"


def _counts(sections: dict[str, list[NarrativeSentence]]) -> dict[str, int]:
    flat = [s for v in sections.values() for s in v]
    return {"total": len(flat), "verified": sum(1 for s in flat if s.status in ("verified", "qualitative")),
            "unsupported": sum(1 for s in flat if s.status == "unsupported")}


def recheck(narrative: Narrative) -> Narrative:
    """The narrative with every sentence checked again by today's rules against its own fact table.

    Verdicts are stored when a narrative is written, so an older narrative keeps the verdicts of older rules
    (before v0.8 the check did not read million/billion/trillion and made every sentence cite the claim's own
    numbers). Checking again costs no API call and gives the same answer for the same rules.
    """
    if narrative.status != "ok" or not narrative.facts:
        return narrative
    sections = {k: [s.model_copy(update={"problems": []}) for s in v] for k, v in narrative.sections.items()}
    for sentences in sections.values():
        verify(sentences, narrative.facts)
    return narrative.model_copy(update={"sections": sections, **_counts(sections)})


# ------------------------------------------------------------------ Claude call


def uses_effort(model: str) -> bool:
    return not model.startswith("claude-haiku")  # Haiku 4.5 rejects the effort setting


def fact_table(facts: list[Fact], language: str) -> str:
    """The facts as JSON, one fact per line, labels in the narrative's language only."""
    rows = [json.dumps({"id": f.id, "label": f.label_zh if language == "zh" else f.label_en, "value": f.value, "unit": f.unit, "source": f.source},
                       ensure_ascii=False) for f in facts]
    return "[\n" + ",\n".join(rows) + "\n]"


def cache_key(facts: list[Fact], *, model: str, effort: str, language: str) -> str:
    parts = (PROMPT_VERSION, model, effort if uses_effort(model) else "-", language, fact_table(facts, language))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def request_params(facts: list[Fact], *, model: str, effort: str, language: str) -> dict:
    params: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT.format(language_rule=LANGUAGE_RULE[language]),
        messages=[{"role": "user", "content": "Fact table (JSON, one fact per line):\n" + fact_table(facts, language) +
                   "\n\nWrite the narrative sections for this claim using only these facts."}],
        output_config={"format": {"type": "json_schema", "schema": _schema()}},
    )
    if uses_effort(model):
        params["output_config"]["effort"] = effort
    if model in FALLBACK_MODELS:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"
    return params


def estimate_cost(model: str, usage: dict[str, int]) -> Optional[float]:
    """USD at list price; None for a model without a known price."""
    price = PRICES.get(model)
    if price is None:
        return None
    inp, out, read = price
    return (usage.get("input_tokens", 0) * inp + usage.get("output_tokens", 0) * out
            + usage.get("cache_read_input_tokens", 0) * read + usage.get("cache_creation_input_tokens", 0) * inp * 1.25) / 1e6


def write_narrative(result: CaseResult, *, client: Any, now: Callable[[], datetime], model: str = MODEL,
                    effort: str = EFFORT, language: str = "zh") -> Narrative:
    """Ask Claude for the narrative in one language, then verify it. Errors and refusals come back as a Narrative with a status."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}, got '{language}'")
    facts = build_facts(result)
    base = dict(model=model, created_at=now(), facts=facts, language=language, effort=effort if uses_effort(model) else None)
    if not facts:
        return Narrative(status="error", error="no confirmed case to describe", **base)
    base["cache_key"] = cache_key(facts, model=model, effort=effort, language=language)
    try:
        response = client.beta.messages.create(**request_params(facts, model=model, effort=effort, language=language))
    except Exception as exc:  # typed SDK errors are reported as text; the case itself is unaffected
        return Narrative(status="error", error=f"{type(exc).__name__}: {getattr(exc, 'message', exc)}", **base)
    usage = getattr(response, "usage", None)
    base["usage"] = {k: int(getattr(usage, k, 0) or 0) for k in USAGE_FIELDS} if usage is not None else {}
    base["model"] = getattr(response, "model", model) or model
    if getattr(response, "stop_reason", None) == "refusal":
        return Narrative(status="refused", error="the model declined to write this narrative", **base)
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    try:
        parsed = _Out.model_validate_json(text)
    except ValidationError as exc:
        return Narrative(status="error", error=f"response did not match the schema: {exc.errors()[:1]}", **base)
    sections = {name: [NarrativeSentence(**{language: s.text}, fact_ids=s.fact_ids) for s in getattr(parsed, name)] for name in SECTIONS}
    for sentences in sections.values():
        verify(sentences, facts)
    return Narrative(status="ok", sections=sections, **_counts(sections), **base)
