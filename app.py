"""TickerCase Streamlit page (UC.1): claim and assumptions -> confirm -> investment case.

Run: streamlit run app.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

from tickercase.config import REPO_ROOT, load_settings
from tickercase.models import CaseResult, ClaimDraft, Confirmation, EvidenceItem, ReferenceSnapshot
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import ConfirmationError, confirm, confirmation_state, fingerprint, validate_draft

# ------------------------------------------------------------------ field layout

# name -> (label, placeholder, help)
FIELD_META = {
    "claim_text": ("观点原文", "例如：SYNT 五年后股价达到 100 美元", "要核验的原始说法，原样记录。"),
    "ticker": ("股票代码", "AAPL", "美股代码；SEC 和行情数据都按此代码查询。"),
    "currency": ("币种", "USD", "3 位 ISO 代码。SEC 财务数据按 USD 比较。"),
    "target_price": ("目标价", "100", "观点给出的目标价格。"),
    "horizon_years": ("时间范围（年）", "5", "假设：观点在多少年后兑现，可填小数。"),
    "reference_price": ("参考价", "50", "参考值：当前或某日的股价。可用「带入公开数据」获取。"),
    "reference_price_date": ("参考价日期", "YYYY-MM-DD", "参考价对应的交易日。"),
    "reference_price_source": ("参考价来源", "例如：收盘价截图", "记录参考价从哪里来。"),
    "current_shares": ("当前股份数（可选）", "95000000", "参考值：最近一次披露的流通股数。"),
    "target_assumed_shares": ("目标期股份数", "100000000", "假设：目标日期的股份数，与当前股份数分开填写。"),
    "valuation_multiple": ("估值倍数", "25", "假设：目标日期的 P/S 或 P/E 倍数。"),
    "base_annual_metric": ("基期年度指标（可选）", "200000000", "参考值：P/S 填年营收，P/E 填年净利润。"),
    "base_metric_currency": ("基期指标币种（可选）", "USD", "须与价格币种一致，不做汇率换算。"),
    "base_metric_period": ("基期期间（可选）", "FY2025", "基期指标对应的财年。"),
    "filings_since": ("申报检索起始日（可选）", "YYYY-MM-DD", "检查 SEC 申报覆盖范围的起点。"),
    "probability_drift": ("年化漂移率 μ（假设）", "0.07", "附加项：年化预期收益假设，0.07 = 7%。留空则不计算概率参考。"),
    "probability_volatility": ("年化波动率 σ（可选）", "留空则用历史波动率", "附加项：留空时使用行情数据计算的历史波动率。"),
}
TEXT_FIELDS = list(FIELD_META)
METHODS = {"price_to_sales": "P/S 市销率", "price_to_earnings": "P/E 市盈率"}
MODE_LABELS = {
    "live": "live：实时请求公开数据",
    "record": "record：实时请求并录制快照",
    "replay": "replay：只读录制快照，不联网",
    "synthetic": "synthetic：合成示例数据",
}
EXAMPLES = {
    "ps": REPO_ROOT / "examples" / "synthetic_claim_ps.json",
    "pe": REPO_ROOT / "examples" / "synthetic_claim_pe.json",
}
VERDICT_CN = {
    "supported_today": ("目前证据支持", "✔", "good"),
    "partially_supported": ("部分支持", "◐", "warning"),
    "not_supported_today": ("目前证据不支持", "✖", "critical"),
    "insufficiently_specified": ("信息不足，无法判断", "?", "neutral"),
}
STANCE_CN = {"supporting": "支持", "contrary": "反对", "missing": "缺失", "neutral": "背景"}
STANCE_ICON = {"supporting": "✔", "contrary": "✖", "missing": "…", "neutral": "·"}
STATUS_CN = {
    "evaluated": "已评估",
    "evaluated_with_provider_errors": "已评估（部分数据源失败）",
    "blocked_invalid_input": "输入无效",
    "blocked_unconfirmed": "未确认",
    "blocked_confirmation_stale": "确认已失效",
}
CALC_CN = {
    "required_return": "所需总收益",
    "annualized_price_return": "所需年化收益",
    "target_market_cap": "目标市值",
    "implied_share_count_change": "股份数变化",
    "required_annual_revenue": "所需年营收",
    "required_annual_net_income": "所需年净利润",
    "required_metric_cagr": "所需指标年增速（相对你填写的基期）",
}

CSS = """
<style>
.tc-verdict {border-radius: 10px; padding: 16px 20px; margin: 4px 0 12px 0; border: 1px solid rgba(128,128,128,0.25);}
.tc-verdict .tc-label {font-size: 1.6rem; font-weight: 650; line-height: 1.25;}
.tc-verdict .tc-sub {opacity: 0.8; font-size: 0.92rem; margin-top: 4px;}
.tc-good {background: rgba(12,163,12,0.10); border-left: 6px solid #0ca30c;}
.tc-warning {background: rgba(250,178,25,0.14); border-left: 6px solid #fab219;}
.tc-critical {background: rgba(208,59,59,0.10); border-left: 6px solid #d03b3b;}
.tc-neutral {background: rgba(137,135,129,0.12); border-left: 6px solid #898781;}
.tc-steps {display: flex; gap: 8px; margin: 0 0 8px 0; flex-wrap: wrap;}
.tc-step {padding: 4px 12px; border-radius: 999px; font-size: 0.85rem; border: 1px solid rgba(128,128,128,0.35); opacity: 0.6;}
.tc-step.done {opacity: 1; border-color: #0ca30c;}
.tc-step.now {opacity: 1; border-color: #2a78d6; font-weight: 600;}
</style>
"""


def series_color() -> str:
    try:
        return "#3987e5" if st.context.theme.type == "dark" else "#2a78d6"
    except Exception:  # older runtimes or tests
        return "#2a78d6"


# ------------------------------------------------------------------ formatting


def fmt_amount(value: Optional[Decimal], unit: str = "") -> str:
    if value is None:
        return "—"
    v = abs(value)
    sign = "-" if value < 0 else ""
    for size, suffix in ((Decimal("1e12"), "T"), (Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if v >= size:
            return f"{sign}{v / size:,.2f}{suffix}{(' ' + unit) if unit else ''}"
    return f"{sign}{v:,.2f}{(' ' + unit) if unit else ''}"


def fmt_pct(value: Optional[Decimal]) -> str:
    return "—" if value is None else f"{value * 100:,.2f}%"


def fmt_calc(item) -> str:
    if item.value is None:
        return "无法计算"
    if item.unit.startswith("ratio"):
        return fmt_pct(item.value)
    return fmt_amount(item.value, item.unit.split(" ")[0])


# ------------------------------------------------------------------ state


@st.cache_resource
def get_service() -> CaseService:
    settings = load_settings()
    return CaseService(settings, store=CaseStore(settings.case_dir))


def _init_state() -> None:
    for name in TEXT_FIELDS:
        st.session_state.setdefault(f"f_{name}", "")
    st.session_state.setdefault("f_valuation_method", "price_to_sales")
    st.session_state.setdefault("sec_mode", "live")
    for key in ("confirmation", "confirm_feedback", "result", "result_mode", "run_error", "prefill_msg", "prefill_snapshot"):
        st.session_state.setdefault(key, None)
    st.session_state.setdefault("prefill_sources", {})


def _reset_confirmation() -> None:
    st.session_state["confirmation"] = None
    st.session_state["confirm_feedback"] = None


def _load_example(key: str) -> None:
    draft = json.loads(Path(EXAMPLES[key]).read_text(encoding="utf-8"))["draft"]
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = str(draft.get(name) or "")
    st.session_state["f_valuation_method"] = draft["valuation_method"]
    st.session_state["sec_mode"] = "synthetic"
    st.session_state["prefill_sources"] = {}
    st.session_state["prefill_msg"] = None
    _reset_confirmation()


def _clear_form() -> None:
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = ""
    st.session_state["f_valuation_method"] = "price_to_sales"
    st.session_state["prefill_sources"] = {}
    st.session_state["prefill_msg"] = None
    st.session_state["prefill_snapshot"] = None
    _reset_confirmation()


def _metric_suggestion(snap: ReferenceSnapshot, method: str):
    return snap.revenue_suggestion if method == "price_to_sales" else snap.net_income_suggestion


def _prefill() -> None:
    ticker = st.session_state["f_ticker"].strip()
    if not ticker:
        st.session_state["prefill_msg"] = ("error", "先填写股票代码。")
        return
    try:
        snap = get_service().reference_snapshot(ticker, mode=st.session_state["sec_mode"])
    except ValueError as exc:
        st.session_state["prefill_msg"] = ("error", str(exc))
        return
    sources: dict[str, tuple[str, str]] = {}
    for name, sug in snap.suggestions.items():
        if f"f_{name}" in st.session_state:
            st.session_state[f"f_{name}"] = sug.value
            sources[name] = (sug.value, sug.source)
    metric = _metric_suggestion(snap, st.session_state["f_valuation_method"])
    if metric is not None:
        st.session_state["f_base_annual_metric"] = metric.value
        sources["base_annual_metric"] = (metric.value, metric.source)
    if snap.period_suggestion is not None:
        st.session_state["f_base_metric_period"] = snap.period_suggestion.value
        sources["base_metric_period"] = (snap.period_suggestion.value, snap.period_suggestion.source)
    st.session_state["prefill_sources"] = sources
    st.session_state["prefill_snapshot"] = snap
    names = "、".join(FIELD_META[n][0] for n in sources if n in FIELD_META)
    errors = "；".join(f"{e.provider_id}: {e.code}" for e in snap.provider_errors)
    if sources:
        msg = f"已带入 {len(sources)} 项（{names}）。这些是参考值，请核对后再确认。"
        st.session_state["prefill_msg"] = ("warning" if errors else "success", msg + (f" 未取到：{errors}" if errors else ""))
    else:
        st.session_state["prefill_msg"] = ("error", f"没有取到公开数据。{errors}")
    _reset_confirmation()


def _method_changed() -> None:
    """Keep a prefilled base metric consistent with the valuation method."""
    snap: Optional[ReferenceSnapshot] = st.session_state.get("prefill_snapshot")
    sources = st.session_state["prefill_sources"]
    if snap is None or "base_annual_metric" not in sources:
        return
    old_value, _ = sources["base_annual_metric"]
    if st.session_state["f_base_annual_metric"] != old_value:
        return  # user edited it; leave it alone
    metric = _metric_suggestion(snap, st.session_state["f_valuation_method"])
    if metric is None:
        st.session_state["f_base_annual_metric"] = ""
        sources.pop("base_annual_metric")
    else:
        st.session_state["f_base_annual_metric"] = metric.value
        sources["base_annual_metric"] = (metric.value, metric.source)


def current_draft() -> ClaimDraft:
    values = {name: (st.session_state[f"f_{name}"] or None) for name in TEXT_FIELDS}
    values["valuation_method"] = st.session_state["f_valuation_method"]
    # a source label only applies while the field still holds the value it filled in
    sources = {name: src for name, (val, src) in st.session_state["prefill_sources"].items() if st.session_state.get(f"f_{name}") == val}
    values["field_sources"] = sources or None
    return ClaimDraft(**values)


# ------------------------------------------------------------------ inputs


def field(name: str, container=None) -> None:
    label, placeholder, help_text = FIELD_META[name]
    target = container or st
    sources = st.session_state["prefill_sources"]
    if name in sources and st.session_state.get(f"f_{name}") == sources[name][0]:
        help_text = f"{help_text}\n\n已从公开数据带入：{sources[name][1]}"
        label = f"{label} ⓘ"
    if name == "claim_text":
        target.text_area(label, key=f"f_{name}", placeholder=placeholder, help=help_text, height=80)
    else:
        target.text_input(label, key=f"f_{name}", placeholder=placeholder, help=help_text)


def render_inputs() -> None:
    with st.container(border=True):
        st.markdown("**观点**")
        field("claim_text")
        c = st.columns([1.2, 0.8, 1, 1])
        field("ticker", c[0])
        field("currency", c[1])
        field("target_price", c[2])
        field("horizon_years", c[3])
        b1, b2 = st.columns([1, 3])
        b1.button("带入公开数据", key="btn_prefill", on_click=_prefill, help="按股票代码读取最新收盘价、SEC 披露的股份数和最近年度营收/净利润，填入下方参考值。")
        msg = st.session_state["prefill_msg"]
        if msg:
            {"success": b2.success, "warning": b2.warning, "error": b2.error}[msg[0]](msg[1])

    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**价格与股本**")
        c = st.columns(2)
        field("reference_price", c[0])
        field("reference_price_date", c[1])
        field("reference_price_source")
        c = st.columns(2)
        field("current_shares", c[0])
        field("target_assumed_shares", c[1])
    with right.container(border=True):
        st.markdown("**估值**")
        c = st.columns(2)
        c[0].selectbox("估值方法", options=list(METHODS), format_func=METHODS.get, key="f_valuation_method",
                       on_change=_method_changed, help="假设：目标日期用哪种倍数估值。")
        field("valuation_multiple", c[1])
        field("base_annual_metric")
        c = st.columns(2)
        field("base_metric_currency", c[0])
        field("base_metric_period", c[1])

    with st.expander("证据范围与附加项"):
        c = st.columns(3)
        field("filings_since", c[0])
        field("probability_drift", c[1])
        field("probability_volatility", c[2])
        st.caption("价格概率参考是附加内容：在你设定的漂移率和波动率下，用对数正态模型估算到期价格高于各价位的概率。它不参与结论。")


# ------------------------------------------------------------------ results


def stepper(inputs_ok: bool, state: str, has_result: bool) -> None:
    if has_result:
        classes = ("done", "done", "done")
    elif state == "confirmed":
        classes = ("done", "done", "now")
    elif inputs_ok:
        classes = ("done", "now", "")
    else:
        classes = ("now", "", "")
    labels = ("① 填写观点与假设", "② 确认输入", "③ 查看投资案例")
    html = "".join(f'<span class="tc-step {cls}">{label}</span>' for label, cls in zip(labels, classes))
    st.markdown(f'<div class="tc-steps">{html}</div>', unsafe_allow_html=True)


def render_verdict(result: CaseResult) -> None:
    v = result.verdict
    if v is None:
        return
    cn, icon, tone = VERDICT_CN[v.label]
    st.markdown(
        f'<div class="tc-verdict tc-{tone}"><div class="tc-label">{icon} {cn} · {v.display}</div>'
        f'<div class="tc-sub">证据截至 {v.as_of} · {v.rules_version} · 描述当日的证据状态，不是价格预测</div></div>',
        unsafe_allow_html=True,
    )


def render_key_numbers(result: CaseResult) -> None:
    calc = {c.name: c for c in result.calculations}
    claim = result.confirmed_claim.values
    metric = "required_annual_revenue" if claim.valuation_method.value == "price_to_sales" else "required_annual_net_income"
    growth = next((i for i in result.evidence_items if i.id == "E1"), None)
    req_from_reported = Decimal(growth.measured["required_cagr_from_reported"]) if growth and "required_cagr_from_reported" in growth.measured else None
    hist = Decimal(growth.measured["reported_cagr"]) if growth and "reported_cagr" in growth.measured else None
    cols = st.columns(4)
    cols[0].metric("所需总收益", fmt_calc(calc["required_return"]), help=calc["required_return"].formula)
    cols[1].metric("所需年化收益", fmt_calc(calc["annualized_price_return"]), help=calc["annualized_price_return"].formula)
    cols[2].metric("目标市值", fmt_calc(calc["target_market_cap"]), help=calc["target_market_cap"].formula)
    cols[3].metric(CALC_CN[metric], fmt_calc(calc[metric]), help=calc[metric].formula)
    if req_from_reported is not None:
        cols = st.columns(4)
        cols[0].metric("所需指标年增速（相对已披露）", fmt_pct(req_from_reported))
        cols[1].metric("近年已披露增速", fmt_pct(hist), delta=None if hist is None else f"差距 {fmt_pct(req_from_reported - hist)}", delta_color="off")


def render_sources(item: EvidenceItem) -> None:
    for s in item.sources:
        text = f"{s.label}" + (f" · {s.data_mode.value}" if s.data_mode else "")
        st.markdown(f"- [{text}]({s.url})" if s.url else f"- {text}")


def tab_conclusion(result: CaseResult) -> None:
    v = result.verdict
    st.markdown("#### 判断依据")
    for line in v.rationale:
        st.markdown(f"- {line}")
    st.markdown("#### 何时需要重新评估")
    rows = [{"编号": r.id, "触发条件": r.trigger, "观察对象": r.watch, "阈值": r.threshold or "", "关联": ", ".join(r.linked_to)}
            for r in result.recheck_conditions]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    with st.expander("这个结论的限制", expanded=False):
        for line in v.limitations:
            st.markdown(f"- {line}")


def tab_evidence(result: CaseResult) -> None:
    counts = {s: sum(1 for i in result.evidence_items if i.stance == s) for s in STANCE_CN}
    cols = st.columns(4)
    for col, s in zip(cols, STANCE_CN):
        col.metric(f"{STANCE_ICON[s]} {STANCE_CN[s]}", counts[s])
    order = {"contrary": 0, "supporting": 1, "missing": 2, "neutral": 3}
    for item in sorted(result.evidence_items, key=lambda i: (order[i.stance], i.id)):
        with st.container(border=True):
            st.markdown(f"**{STANCE_ICON[item.stance]} {STANCE_CN[item.stance]} · {item.id} {item.title}**")
            st.write(item.detail)
            if item.rule:
                st.caption(f"规则：{item.rule}")
            if item.sources:
                with st.expander("来源与数值"):
                    render_sources(item)
                    if item.measured:
                        st.json(item.measured, expanded=False)


def tab_calculations(result: CaseResult) -> None:
    st.caption("全部基于你确认的输入与假设，Decimal 精确计算，与网络数据无关。")
    rows = [
        {
            "项目": CALC_CN.get(c.name, c.name),
            "数值": fmt_calc(c),
            "原始值": "" if c.value is None else format(c.value, "f"),
            "公式": c.formula,
            "输入": json.dumps(c.inputs, ensure_ascii=False),
            "假设/说明": "; ".join(c.assumptions + ([c.reason] if c.reason else [])),
        }
        for c in result.calculations
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    with st.expander("输入值来源"):
        prov = result.confirmed_claim.value_provenance
        st.dataframe([{"字段": k, "来源": v} for k, v in prov.items()], hide_index=True, use_container_width=True)


def _bar_chart(points, required: Optional[Decimal], label: str, color: str) -> alt.Chart:
    df = pd.DataFrame({"财年": [f"FY{p.period_end.year}" for p in points], "数值": [float(p.value) for p in points],
                       "期末": [p.period_end.isoformat() for p in points], "申报": [f"{p.form} {p.filed}" for p in points]})
    bars = alt.Chart(df).mark_bar(size=24, color=color, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("财年:N", title=None, sort=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("数值:Q", title=f"{label}（USD）", axis=alt.Axis(format="~s")),
        tooltip=[alt.Tooltip("财年:N"), alt.Tooltip("数值:Q", format=",.0f"), alt.Tooltip("期末:N"), alt.Tooltip("申报:N")],
    )
    if required is not None and points and float(required) <= 5 * max(float(p.value) for p in points):
        rule_df = pd.DataFrame({"y": [float(required)], "t": [f"观点所需 {fmt_amount(required)}"]})
        rule = alt.Chart(rule_df).mark_rule(strokeWidth=1, strokeDash=[4, 3], color="#898781").encode(y="y:Q")
        text = alt.Chart(rule_df).mark_text(align="left", dx=4, dy=-6, color="#898781").encode(y="y:Q", text="t:N", x=alt.value(0))
        return (bars + rule + text).properties(height=260)
    return bars.properties(height=260)


def tab_public_data(result: CaseResult) -> None:
    color = series_color()
    facts, market = result.reported_facts, result.market
    claim = result.confirmed_claim.values
    if facts is not None:
        st.markdown(f"#### SEC 已披露财务数据 · {facts.company_name or facts.ticker}（{facts.data_mode.value}）")
        is_ps = claim.valuation_method.value == "price_to_sales"
        series = facts.revenue if is_ps else facts.net_income
        required = next((c.value for c in result.calculations if c.name in ("required_annual_revenue", "required_annual_net_income")), None)
        if series:
            st.altair_chart(_bar_chart(series[-6:], required, "年营收" if is_ps else "年净利润", color), use_container_width=True)
            if required is not None and float(required) > 5 * max(float(p.value) for p in series[-6:]):
                st.caption(f"观点所需 {fmt_amount(required)}，超过图中最大值 5 倍，未画参考线。")
        rows = []
        for kind, pts in (("营收", facts.revenue), ("净利润", facts.net_income), ("股份数", facts.shares_outstanding)):
            for p in pts[-5:]:
                rows.append({"指标": kind, "期末": p.period_end.isoformat(), "数值": fmt_amount(p.value), "XBRL 概念": p.concept,
                             "表格": p.form, "申报日": p.filed.isoformat(), "accession": p.accession})
        with st.expander("数据表"):
            st.dataframe(rows, hide_index=True, use_container_width=True)
            st.caption(f"原始响应：{facts.source_url}")
    if market is not None:
        st.markdown(f"#### 股价历史 · {market.symbol}（{market.data_mode.value}）")
        df = pd.DataFrame({"日期": [p.day for p in market.price_series], "收盘价": [float(p.close) for p in market.price_series]})
        line = alt.Chart(df).mark_line(strokeWidth=2, color=color).encode(
            x=alt.X("日期:T", title=None, axis=alt.Axis(format="%Y-%m", labelAngle=0)), y=alt.Y("收盘价:Q", title=f"收盘价（{market.currency or ''}）"),
        )
        hover = alt.selection_point(fields=["日期"], nearest=True, on="pointerover", empty=False)
        points = alt.Chart(df).mark_point(size=80, filled=True, color=color, stroke="white", strokeWidth=2).encode(
            x="日期:T", y="收盘价:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("日期:T", format="%Y-%m-%d"), alt.Tooltip("收盘价:Q", format=",.2f")],
        ).add_params(hover)
        ref_df = pd.DataFrame({"y": [float(claim.reference_price)], "t": [f"你的参考价 {claim.reference_price}"]})
        ref = alt.Chart(ref_df).mark_rule(strokeWidth=1, strokeDash=[4, 3], color="#898781").encode(y="y:Q")
        ref_text = alt.Chart(ref_df).mark_text(align="left", dx=4, dy=-6, color="#898781").encode(y="y:Q", text="t:N", x=alt.value(0))
        st.altair_chart((line + points + ref + ref_text).properties(height=260), use_container_width=True)
        vol = f"，历史年化波动率 {fmt_pct(market.annualized_volatility)}" if market.annualized_volatility is not None else ""
        st.caption(f"最新收盘 {format(market.last_close, 'f')}（{market.last_date}）{vol}。目标价 {claim.target_price} 未画入此图。{market.note}")
    if result.coverage is not None:
        st.markdown("#### SEC 申报记录")
        (st.warning if result.coverage.coverage_gap else st.caption)(result.coverage.message)
    if result.evidence_records:
        rows = [
            {"表类型": r.form_type, "申报日": r.filing_date.isoformat(), "报告期": r.report_date.isoformat() if r.report_date else "",
             "原文件": r.document_url or "", "索引页": r.filing_index_url, "data_mode": r.data_mode.value}
            for r in result.evidence_records
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True,
                     column_config={"原文件": st.column_config.LinkColumn(), "索引页": st.column_config.LinkColumn()})
        st.caption("申报记录只有元数据，本版本不读取正文。")
    if facts is None and market is None and not result.evidence_records:
        st.info("本次没有取到公开数据，见「运行记录」中的数据错误。")


def tab_probability(result: CaseResult) -> None:
    p = result.probability
    st.caption("附加内容：模型输出，不参与结论，也不是价格预测。")
    if p is None:
        st.info("未计算。在「证据范围与附加项」里填写年化漂移率 μ（例如 0.07），确认后重新运行即可。")
        return
    if p.status != "ok":
        st.warning(f"无法计算：{p.reason}")
        return
    cols = st.columns(4)
    cols[0].metric(f"到期价 ≥ 目标价 {p.target_price} 的概率", f"{p.target_probability * 100:.1f}%")
    cols[1].metric("中位数价格", f"{p.median_price:,.2f}")
    cols[2].metric("10% 分位价格", f"{p.p10_price:,.2f}")
    cols[3].metric("90% 分位价格", f"{p.p90_price:,.2f}")
    color = series_color()
    df = pd.DataFrame({"价格": [float(x.price) for x in p.points], "概率": [x.probability_at_or_above for x in p.points]})
    line = alt.Chart(df).mark_line(strokeWidth=2, color=color, point=alt.OverlayMarkDef(size=70, filled=True, color=color, stroke="white", strokeWidth=2)).encode(
        x=alt.X("价格:Q", title="到期价格水平"),
        y=alt.Y("概率:Q", title="P(到期价格 ≥ 该水平)", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("价格:Q", format=",.2f"), alt.Tooltip("概率:Q", format=".1%")],
    )
    tdf = pd.DataFrame({"x": [float(p.target_price)], "t": [f"目标价 {p.target_price}"]})
    rule = alt.Chart(tdf).mark_rule(strokeWidth=1, strokeDash=[4, 3], color="#898781").encode(x="x:Q")
    text = alt.Chart(tdf).mark_text(align="left", dx=4, y=8, color="#898781").encode(x="x:Q", text="t:N")
    st.altair_chart((line + rule + text).properties(height=280), use_container_width=True)
    st.dataframe([{"价格水平": f"{x.price:,.2f}", "P(到期价 ≥ 水平)": f"{x.probability_at_or_above * 100:.1f}%"} for x in p.points],
                 hide_index=True, use_container_width=True)
    st.markdown("**假设**")
    for a in p.assumptions:
        st.markdown(f"- {a}")
    st.markdown("**限制**")
    for line_text in p.limitations:
        st.markdown(f"- {line_text}")
    st.caption(f"模型：{p.model}")


def tab_run_log(result: CaseResult) -> None:
    st.caption(f"case_id {result.case_id} · 创建 {result.created_at.isoformat()} · 状态 {STATUS_CN[result.status.value]}")
    st.json(result.data_modes, expanded=False)
    if result.provider_errors:
        st.markdown("#### 数据错误")
        for e in result.provider_errors:
            st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")
    if result.missing_fields:
        st.markdown("#### 缺失项")
        for m in result.missing_fields:
            st.write(f"- `{m.field}`（{'阻塞' if m.blocking else '不阻塞'}，影响 {m.required_for}）：{m.message}")
    st.info(result.disclaimer)
    st.download_button("下载结果 JSON", result.model_dump_json(indent=2), file_name=f"tickercase_{result.case_id}.json", mime="application/json")


def render_result(result: CaseResult) -> None:
    st.markdown("### 投资案例")
    if result.validation_issues:
        for i in result.validation_issues:
            st.error(f"`{i.field}` {i.code}: {i.message}")
        return
    render_verdict(result)
    for e in result.provider_errors:
        st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")
    if result.warnings:
        with st.expander(f"提示（{len(result.warnings)}）", expanded=bool(result.provider_errors)):
            for w in result.warnings:
                st.warning(w)
    render_key_numbers(result)
    tabs = st.tabs(["结论与依据", "证据", "计算", "公开数据", "概率参考（附加）", "运行记录"])
    with tabs[0]:
        tab_conclusion(result)
    with tabs[1]:
        tab_evidence(result)
    with tabs[2]:
        tab_calculations(result)
    with tabs[3]:
        tab_public_data(result)
    with tabs[4]:
        tab_probability(result)
    with tabs[5]:
        tab_run_log(result)


# ------------------------------------------------------------------ page


def sidebar() -> None:
    with st.sidebar:
        st.markdown("## TickerCase")
        st.caption("把一句股票观点变成可检查的投资案例：明确假设、可复算数字、带日期的公开证据、结论和重新评估条件。")
        st.radio("数据模式", options=list(MODE_LABELS), format_func=MODE_LABELS.get, key="sec_mode",
                 help="live 请求 SEC 与行情接口；synthetic 使用虚构公司 SYNT 的示例数据。")
        st.markdown("**示例**")
        st.button("合成示例 · P/S", on_click=_load_example, args=("ps",), key="btn_example_ps", use_container_width=True)
        st.button("合成示例 · P/E", on_click=_load_example, args=("pe",), key="btn_example_pe", use_container_width=True)
        st.button("清空", on_click=_clear_form, key="btn_clear", use_container_width=True)
        with st.expander("结论的四种结果"):
            st.markdown(
                "- **✔ 目前证据支持**：所需增长不高于已披露增长，且没有反对证据\n"
                "- **◐ 部分支持**：有支持，也有差距或反对项\n"
                "- **✖ 目前证据不支持**：所需增长远高于已披露增长，或还有其他反对项\n"
                "- **? 信息不足**：核心检查缺数据，无法判断\n\n"
                "规则为 rules-v1，阈值待团队验证。"
            )
        st.caption("不执行交易，不构成投资建议。")


def main() -> None:
    st.set_page_config(page_title="TickerCase", page_icon="📈", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    _init_state()
    sidebar()

    draft = current_draft()
    confirmation: Optional[Confirmation] = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    result: Optional[CaseResult] = st.session_state["result"]
    result_current = result is not None and result.input_fingerprint == fingerprint(draft) and st.session_state["result_mode"] == st.session_state["sec_mode"]

    st.title("TickerCase")
    stepper(validate_draft(draft, today=get_service().today()).ok, state, result_current)

    st.markdown("### 1. 观点与假设")
    render_inputs()
    draft = current_draft()  # widgets above may have changed the values

    validation = validate_draft(draft, today=get_service().today())
    for issue in validation.issues:
        st.error(f"`{issue.field}` {issue.code}: {issue.message}")
    blocking = [m for m in validation.missing_fields if m.blocking]
    if blocking:
        st.warning("缺少核心输入：" + "、".join(FIELD_META.get(m.field, (m.field,))[0] for m in blocking))
    optional = [m for m in validation.missing_fields if not m.blocking]
    if optional:
        st.caption("可选项未填：" + "；".join(f"{FIELD_META.get(m.field, (m.field,))[0]}（{m.message}）" for m in optional))

    st.markdown("### 2. 确认并运行")
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("确认以上输入", key="btn_confirm", type="secondary", use_container_width=True):
        try:
            st.session_state["confirmation"] = confirm(draft, now=get_service().now, today=get_service().today())
            st.session_state["confirm_feedback"] = None
        except ConfirmationError:
            st.session_state["confirmation"] = None
            st.session_state["confirm_feedback"] = "输入无效或缺少核心字段，未确认。"
    confirmation = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    run_clicked = c2.button("运行评估", key="btn_run", type="primary", disabled=state != "confirmed", use_container_width=True)
    with c3:
        if st.session_state["confirm_feedback"]:
            st.error(st.session_state["confirm_feedback"])
        if state == "confirmed":
            st.success(f"已确认（{confirmation.confirmed_at:%Y-%m-%d %H:%M:%S} UTC）。确认绑定当前全部输入，修改任何一项都需要重新确认。")
        elif state == "stale":
            st.warning("确认后输入已修改，原确认失效，请重新确认。")
        else:
            st.info("尚未确认。请核对上方输入，尤其是标为假设的项目。")

    if run_clicked:
        st.session_state["result"] = None
        st.session_state["run_error"] = None
        with st.spinner("正在读取公开数据并评估……"):
            try:
                st.session_state["result"] = get_service().evaluate(draft, confirmation, sec_mode=st.session_state["sec_mode"])
                st.session_state["result_mode"] = st.session_state["sec_mode"]
            except Exception as exc:  # unexpected failure; keep page usable and show it
                st.session_state["run_error"] = f"{type(exc).__name__}: {exc}"

    if st.session_state["run_error"]:
        st.error("运行失败：" + st.session_state["run_error"])
    result = st.session_state["result"]
    if result is not None:
        if result.input_fingerprint != fingerprint(draft) or st.session_state["result_mode"] != st.session_state["sec_mode"]:
            st.warning("已有结果对应修改前的输入或数据模式，已隐藏。请重新确认并运行。")
        else:
            render_result(result)


main()
