"""TickerCase Streamlit page: inputs -> confirm -> run -> calculations, filings, gaps, errors.

Run: streamlit run app.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

import streamlit as st

from tickercase.config import REPO_ROOT, load_settings
from tickercase.models import CaseResult, ClaimDraft, Confirmation
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import ConfirmationError, confirm, confirmation_state, fingerprint, validate_draft

FIELDS = [
    ("claim_text", "观点原文", "例如：SYNT 五年后股价达到 100 美元"),
    ("ticker", "股票代码 (ticker)", "SYNT"),
    ("currency", "币种 (ISO)", "USD"),
    ("target_price", "目标价", "100"),
    ("reference_price", "参考价（手动参考值）", "50"),
    ("reference_price_date", "参考价日期 YYYY-MM-DD", "2026-09-30"),
    ("reference_price_source", "参考价来源说明", "券商收盘价截图 / 手动记录"),
    ("horizon_years", "时间范围（年，假设）", "5"),
    ("target_assumed_shares", "目标期假设股份数（假设）", "100000000"),
    ("current_shares", "当前股份数（可选，手动参考值）", "95000000"),
    ("valuation_multiple", "估值倍数（假设）", "25"),
    ("base_annual_metric", "基期年度指标（可选；P/S 填营收，P/E 填净利润）", "200000000"),
    ("base_metric_currency", "基期指标币种（可选）", "USD"),
    ("base_metric_period", "基期期间（可选）", "FY2025"),
    ("filings_since", "申报检索起始日（可选）YYYY-MM-DD", "2024-01-01"),
]
METHODS = {"price_to_sales": "P/S 市销率", "price_to_earnings": "P/E 市盈率"}
SEC_MODE_LABELS = {
    "live": "live：实时请求 SEC（需 SEC_USER_AGENT）",
    "record": "record：实时请求并录制快照",
    "replay": "replay：只读已录制快照，不联网",
    "synthetic": "synthetic：合成示例数据（非真实 SEC）",
}
EXAMPLES = {
    "ps": REPO_ROOT / "examples" / "synthetic_claim_ps.json",
    "pe": REPO_ROOT / "examples" / "synthetic_claim_pe.json",
}


@st.cache_resource
def get_service() -> CaseService:
    settings = load_settings()
    return CaseService(settings, store=CaseStore(settings.case_dir))


def _init_state() -> None:
    for name, _, _ in FIELDS:
        st.session_state.setdefault(f"f_{name}", "")
    st.session_state.setdefault("f_valuation_method", "price_to_sales")
    st.session_state.setdefault("sec_mode", "live")
    st.session_state.setdefault("confirmation", None)
    st.session_state.setdefault("confirm_feedback", None)
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("result_mode", None)
    st.session_state.setdefault("run_error", None)


def _load_example(key: str) -> None:
    draft = json.loads(Path(EXAMPLES[key]).read_text(encoding="utf-8"))["draft"]
    for name, _, _ in FIELDS:
        st.session_state[f"f_{name}"] = str(draft.get(name) or "")
    st.session_state["f_valuation_method"] = draft["valuation_method"]
    st.session_state["sec_mode"] = "synthetic"
    st.session_state["confirmation"] = None
    st.session_state["confirm_feedback"] = None


def _clear_form() -> None:
    for name, _, _ in FIELDS:
        st.session_state[f"f_{name}"] = ""
    st.session_state["f_valuation_method"] = "price_to_sales"
    st.session_state["confirmation"] = None
    st.session_state["confirm_feedback"] = None


def current_draft() -> ClaimDraft:
    values = {name: (st.session_state[f"f_{name}"] or None) for name, _, _ in FIELDS}
    values["valuation_method"] = st.session_state["f_valuation_method"]
    return ClaimDraft(**values)


def fmt_value(item) -> str:
    if item.value is None:
        return "—"
    v: Decimal = item.value
    if item.unit.startswith("ratio"):
        return f"{v * 100:,.4f}%"
    return f"{v:,.2f} {item.unit}"


def render_result(result: CaseResult) -> None:
    st.subheader("结果")
    cols = st.columns(3)
    cols[0].metric("状态", result.status.value)
    cols[1].metric("analysis_status", result.analysis_status)
    cols[2].metric("verdict", "null（未实现自动判断）" if result.verdict is None else str(result.verdict))
    st.caption(f"case_id {result.case_id} · 创建 {result.created_at.isoformat()} · 数据来源模式 {result.data_modes}")
    st.info(result.disclaimer)

    for w in result.warnings:
        st.warning(w)

    if result.provider_errors:
        st.markdown("#### 数据错误")
        for e in result.provider_errors:
            st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")

    if result.calculations:
        st.markdown("#### 计算明细（全部基于你输入的数值与假设）")
        rows = [
            {
                "项目": c.name,
                "状态": c.status,
                "数值": fmt_value(c),
                "原始值": "" if c.value is None else format(c.value, "f"),
                "单位": c.unit,
                "公式": c.formula,
                "输入": json.dumps(c.inputs, ensure_ascii=False),
                "假设/说明": "; ".join(c.assumptions + ([c.reason] if c.reason else [])),
            }
            for c in result.calculations
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if result.coverage is not None:
        st.markdown("#### 申报覆盖范围")
        (st.warning if result.coverage.coverage_gap else st.caption)(result.coverage.message)

    if result.evidence_records:
        st.markdown("#### SEC 申报记录（仅元数据，未阅读正文）")
        rows = [
            {
                "表类型": r.form_type,
                "申报日": r.filing_date.isoformat(),
                "报告期": r.report_date.isoformat() if r.report_date else "",
                "accession": r.accession_number,
                "原文件": r.document_url or "",
                "索引页": r.filing_index_url,
                "data_mode": r.data_mode.value,
                "本次检索/回放时间": r.retrieved_at.isoformat(),
                "原始采集时间": r.source_captured_at.isoformat() if r.source_captured_at else "",
            }
            for r in result.evidence_records
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True,
                     column_config={"原文件": st.column_config.LinkColumn(), "索引页": st.column_config.LinkColumn()})
        st.caption(f"原始响应：{result.evidence_records[0].source_response_url}")
    elif result.calculations and not result.provider_errors:
        st.caption("本次没有返回符合条件的申报记录。")

    if result.missing_fields:
        st.markdown("#### 缺失项")
        for m in result.missing_fields:
            st.write(f"- `{m.field}`（{'阻塞' if m.blocking else '不阻塞'}，影响 {m.required_for}）：{m.message}")
    if result.validation_issues:
        st.markdown("#### 输入错误")
        for i in result.validation_issues:
            st.error(f"`{i.field}` {i.code}: {i.message}")

    st.download_button("下载结果 JSON", result.model_dump_json(indent=2), file_name=f"tickercase_{result.case_id}.json", mime="application/json")


def main() -> None:
    st.set_page_config(page_title="TickerCase", layout="wide")
    _init_state()
    st.title("TickerCase")
    st.caption("把股票观点拆成明确假设、可复算数字和可追溯的 SEC 申报来源。不输出评级、概率或投资建议。")

    c1, c2, c3 = st.columns(3)
    c1.button("加载合成示例（P/S）", on_click=_load_example, args=("ps",), key="btn_example_ps")
    c2.button("加载合成示例（P/E）", on_click=_load_example, args=("pe",), key="btn_example_pe")
    c3.button("清空", on_click=_clear_form, key="btn_clear")

    st.markdown("### 1. 观点与假设")
    left, right = st.columns(2)
    for idx, (name, label, placeholder) in enumerate(FIELDS):
        target = left if idx % 2 == 0 else right
        target.text_input(label, key=f"f_{name}", placeholder=placeholder)
    st.selectbox("估值方法（假设）", options=list(METHODS), format_func=METHODS.get, key="f_valuation_method")
    st.radio("SEC 数据模式", options=list(SEC_MODE_LABELS), format_func=SEC_MODE_LABELS.get, key="sec_mode", horizontal=False)

    draft = current_draft()
    validation = validate_draft(draft, today=get_service().today())
    for issue in validation.issues:
        st.error(f"`{issue.field}` {issue.code}: {issue.message}")
    blocking = [m for m in validation.missing_fields if m.blocking]
    if blocking:
        st.warning("缺少核心输入：" + ", ".join(m.field for m in blocking))
    for m in validation.missing_fields:
        if not m.blocking:
            st.caption(f"可选项缺失 `{m.field}`：{m.message}")

    st.markdown("### 2. 确认")
    if st.button("确认以上输入", key="btn_confirm"):
        try:
            st.session_state["confirmation"] = confirm(draft, now=get_service().now, today=get_service().today())
            st.session_state["confirm_feedback"] = None
        except ConfirmationError:
            st.session_state["confirmation"] = None
            st.session_state["confirm_feedback"] = "输入无效或缺少核心字段，未确认。"
    confirmation: Optional[Confirmation] = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    if st.session_state["confirm_feedback"]:
        st.error(st.session_state["confirm_feedback"])
    if state == "confirmed":
        st.success(f"已确认（{confirmation.confirmed_at.isoformat()}）")
    elif state == "stale":
        st.warning("确认后输入已修改，原确认失效，请重新确认。")
    else:
        st.info("尚未确认。")

    st.markdown("### 3. 运行")
    if st.button("运行评估", key="btn_run", disabled=state != "confirmed"):
        st.session_state["result"] = None
        st.session_state["run_error"] = None
        try:
            st.session_state["result"] = get_service().evaluate(draft, confirmation, sec_mode=st.session_state["sec_mode"])
            st.session_state["result_mode"] = st.session_state["sec_mode"]
        except Exception as exc:  # unexpected failure; keep page usable and show it
            st.session_state["run_error"] = f"{type(exc).__name__}: {exc}"

    if st.session_state["run_error"]:
        st.error("运行失败：" + st.session_state["run_error"])
    result: Optional[CaseResult] = st.session_state["result"]
    if result is not None:
        if result.input_fingerprint != fingerprint(draft) or st.session_state["result_mode"] != st.session_state["sec_mode"]:
            st.warning("已有结果对应修改前的输入或数据模式，已隐藏。请重新确认并运行。")
        else:
            render_result(result)


main()
