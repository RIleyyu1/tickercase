"""TickerCase Streamlit page (UC.1): claim -> public data and default assumptions -> confirm -> investment case.

Run: streamlit run app.py
Chinese and English; the language switch is in the sidebar.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

from tickercase.config import REPO_ROOT, load_settings, write_env_value
from tickercase.http_client import validate_user_agent
from tickercase.models import VERDICT_DISPLAY_ZH, CaseResult, ClaimDraft, Confirmation, EvidenceItem, PlainReport, ReferenceSnapshot, Text
from tickercase.service import CLAIM_TEXT_PREFIX, DEFAULT_PREFIX, CaseService
from tickercase.storage import CaseStore
from tickercase.validation import ConfirmationError, confirm, confirmation_state, fingerprint, validate_draft

# ------------------------------------------------------------------ text

S = {
    "tagline": ("把一句股票观点变成可检查的投资案例：明确假设、可复算数字、带日期的公开证据、结论和重新评估条件。",
                "Turn a stock claim into an inspectable investment case: explicit assumptions, reproducible numbers, dated public evidence, a verdict and recheck conditions."),
    "view": ("视图", "View"), "view_new": ("新建案例", "New case"), "view_history": ("历史案例", "Case history"),
    "mode": ("数据模式", "Data mode"),
    "mode_help": ("live 请求 SEC 与行情接口；synthetic 使用虚构公司 SYNT 的示例数据。", "live calls SEC and the quote source; synthetic uses the fictional company SYNT."),
    "examples": ("示例", "Examples"), "ex_ps": ("合成示例 · P/S", "Synthetic example · P/S"), "ex_pe": ("合成示例 · P/E", "Synthetic example · P/E"),
    "clear": ("清空", "Clear"), "no_advice": ("不执行交易，不构成投资建议。", "No trading. Not investment advice."),
    "verdict_kinds": ("结论的四种结果", "The four verdicts"),
    "verdict_kinds_body": (
        "- **✔ 目前证据支持**：所需增长不高于已披露增长，且没有反对证据\n- **◐ 部分支持**：有支持，也有差距或反对项\n"
        "- **✖ 目前证据不支持**：所需增长远高于已披露增长，或还有其他反对项\n- **? 信息不足**：核心检查缺数据，无法判断\n\n规则为 rules-v1，阈值待团队验证。",
        "- **✔ Supported Today**: required growth is at or below reported growth and nothing is contrary\n"
        "- **◐ Partially Supported**: some support, with gaps or contrary items\n"
        "- **✖ Not Supported Today**: required growth far above reported growth, or other contrary items\n"
        "- **? Insufficiently Specified**: the core check lacks data\n\nRules are rules-v1; thresholds await team validation."),
    "sec_setup": ("SEC 联系方式", "SEC contact"),
    "sec_missing": ("live 模式读取 SEC 数据需要联系邮箱（SEC 规定，只发送给 SEC）。填写后保存到本机 .env。",
                    "Live SEC requests need a contact email (SEC rule; sent only to SEC). It is saved to the local .env file."),
    "sec_email": ("你的邮箱", "Your email"), "sec_save": ("保存", "Save"),
    "sec_saved": ("已保存。", "Saved."), "sec_ok": ("SEC 联系方式已设置", "SEC contact is set"),
    "sec_bad": ("请输入有效邮箱。", "Enter a valid email."),
    "s1": ("1. 观点", "1. Claim"),
    "s1_hint": ("写一句观点就行，例如「特斯拉 2030 年涨到 1000 美元」或「NVDA will hit $300 in 3 years」，然后点「识别并补全」。代码、目标价和时间会从原文识别，其余用公开数据和默认假设补全，你核对后确认。",
                "Write the claim as one sentence, e.g. “Tesla to $1,000 by 2030” or “特斯拉五年后翻倍”, then click “Read and fill in”. Ticker, target and time are read from the sentence; the rest comes from public data and default assumptions for you to check."),
    "prefill": ("识别并补全", "Read and fill in"),
    "need_text": ("先写一句观点。", "Write the claim first."),
    "recognized": ("从原文识别：{items}", "Read from the claim: {items}"),
    "recognized_none": ("没有从原文识别出代码、目标价或时间。", "Nothing could be read from the claim."),
    "multiple_target": ("目标价 = 参考价 {ref} × {x}（原文「{text}」）", "Target = reference {ref} × {x} (from “{text}”)"),
    "detail_fields": ("识别结果（可修改）", "What was read (editable)"),
    "r_data": ("数据摘要", "Data summary"), "r_analysis": ("分析", "Analysis"),
    "r_agree": ("一致的信号", "Signals that agree"), "r_diverge": ("关键分歧", "Key divergences"),
    "r_time": ("时间维度", "Time view"), "r_scen": ("情景推演", "Scenarios"),
    "r_concl": ("结论", "Conclusion"), "r_up": ("什么会让判断变好", "What would strengthen the case"),
    "r_down": ("什么会让判断变差", "What would weaken the case"), "r_monitor": ("需要关注的信号", "Signals to watch"),
    "r_cols": (("", "信号", "数据", "说明了什么"), ("", "Signal", "Data", "What it says")),
    "r_scen_cols": (("情景", "假设", "目标日股价", "相对目标价", "含义"), ("Scenario", "Assumptions", "Price at target date", "vs target", "Meaning")),
    "r_mon_cols": (("信号", "当前", "触发条件", "含义"), ("Signal", "Now", "Trigger", "Meaning")),
    "r_time_cols": (("时间", "要看什么", "说明"), ("When", "What", "Note")),
    "r_none": ("这个案例没有简明报告（由旧版本生成）。", "This case has no plain report (made by an older version)."),
    "prefill_help": ("按股票代码读取收盘价、SEC 披露的股份数和年度营收/净利润；空着的假设用默认值（目标期股份数 = 当前股份数，估值倍数 = 当前倍数）。",
                     "Reads the latest close, SEC-reported shares and annual revenue / net income; empty assumptions get defaults (target shares = current shares, multiple = today's multiple)."),
    "need_ticker": ("先填写股票代码。", "Enter a ticker first."),
    "filled": ("已补全 {n} 项。", "Filled {n} fields."),
    "defaults_used": ("默认假设：{items}。它们对结果影响很大，可在下方修改，并参考「计算」页的敏感性表。",
                      "Default assumptions: {items}. They drive the result; change them below and see the sensitivity table on the Calculations tab."),
    "not_fetched": ("未取到：{errors}", "Not fetched: {errors}"),
    "nothing_fetched": ("没有取到公开数据。{errors}", "No public data fetched. {errors}"),
    "rest": ("2. 其余输入（价格、股本、估值假设）", "2. Other inputs (price, shares, valuation assumptions)"),
    "price_box": ("价格与股本", "Price and shares"), "val_box": ("估值", "Valuation"),
    "extra_box": ("证据范围与附加项", "Evidence window and extras"),
    "prob_note": ("价格概率参考是附加内容：在你设定的漂移率和波动率下，用对数正态模型估算到期价格高于各价位的概率。它不参与结论。",
                  "The price probability is an extra: a lognormal model estimate of the price ending above each level, under your drift and volatility. It does not feed the verdict."),
    "method": ("估值方法", "Valuation method"), "method_help": ("假设：目标日期用哪种倍数估值。", "Assumption: which multiple values the company at the target date."),
    "missing_core": ("缺少核心输入：", "Missing core inputs: "), "optional_missing": ("可选项未填：", "Optional inputs empty: "),
    "s3": ("3. 确认并运行", "3. Confirm and run"),
    "share_mode": ("目标期股份数怎么定", "Target-date share count"),
    "share_mode_help": ("大多数人无法直接估计几年后的股数。默认按 SEC 披露的近年股数变化趋势推算（已排除拆股）；也可以选择不变、自定义年变化率，或直接填目标股数。",
                        "Few people can estimate a future share count directly. The default extends the recent SEC-reported trend (stock splits excluded); you can also keep it flat, set a yearly %, or enter a target count."),
    "shares_result": ("目标期股份数 ≈ {n} 股（当前 {c} × (1 {sign} {r}%)^{y} 年）", "Target-date shares ≈ {n} (current {c} × (1 {sign} {r}%)^{y} years)"),
    "shares_need_current": ("需要当前股份数才能推算目标期股份数。", "Current shares are needed to work out the target-date count."),
    "flat_note": ("每年变化 0%：目标期股份数等于当前股份数。", "0% a year: target-date shares equal current shares."),
    "confirm": ("确认以上输入", "Confirm inputs"), "run": ("运行评估", "Run case"),
    "confirmed": ("已确认（{t} UTC）。确认绑定当前全部输入，修改任何一项都需要重新确认。",
                  "Confirmed ({t} UTC). The confirmation covers every current input; any edit needs a new confirmation."),
    "stale": ("确认后输入已修改，原确认失效，请重新确认。", "Inputs changed after confirmation; confirm again."),
    "unconfirmed": ("尚未确认。请核对输入，尤其是标为假设的项目。", "Not confirmed yet. Check the inputs, especially the assumptions."),
    "invalid_confirm": ("输入无效或缺少核心字段，未确认。", "Inputs are invalid or incomplete; not confirmed."),
    "spinner": ("正在读取公开数据并评估……", "Reading public data and evaluating…"),
    "run_failed": ("运行失败：", "Run failed: "),
    "hidden": ("已有结果对应修改前的输入或数据模式，已隐藏。请重新确认并运行。", "The previous result belongs to older inputs or another data mode and is hidden. Confirm and run again."),
    "edit_inputs": ("修改输入", "Edit inputs"),
    "case": ("投资案例", "Investment case"),
    "as_of": ("证据截至 {d} · {r} · 描述当日的证据状态，不是价格预测", "Evidence as of {d} · {r} · describes the evidence on that date; not a price prediction"),
    "notes": ("提示（{n}）", "Notes ({n})"),
    "tabs": (["简明报告", "概率与验证", "结论与依据", "证据", "计算", "公开数据", "概率参考（模型）", "运行记录"],
             ["Plain report", "Probability & checks", "Verdict details", "Evidence", "Calculations", "Public data", "Probability (model)", "Run log"]),
    "pipeline": ("拉取数据", "Fetching data"),
    "ai_setup": ("Claude API（AI 叙述）", "Claude API (AI narrative)"),
    "ai_missing": ("填写 Anthropic API key 后可生成 AI 叙述。key 只保存在本机 .env。", "Enter an Anthropic API key to generate the AI narrative. It is stored only in the local .env."),
    "ai_ok": ("Claude API key 已设置", "Claude API key is set"),
    "ai_key": ("API key", "API key"), "ai_save": ("保存 key", "Save key"),
    "ai_title": ("AI 叙述（已逐句核对出处）", "AI narrative (checked sentence by sentence)"),
    "ai_button": ("生成 AI 叙述", "Write AI narrative"),
    "ai_button_help": ("调用一次 Claude（claude-opus-5-5），按用量计费，通常每次约 $0.1–0.5。叙述只能使用本案例的事实表，每个数字都会被核对。",
                       "One Claude call (claude-opus-5-5), billed by usage, usually about $0.10–0.50. The narrative may only use this case's fact table; every number is checked."),
    "ai_spinner": ("Claude 正在撰写，随后逐句核对……", "Claude is writing; every sentence is checked afterwards…"),
    "ai_summary": ("{ok}/{total} 句通过核对 · {bad} 句含无出处数字 · {model} · 约 ${cost:.3f}", "{ok}/{total} sentences pass · {bad} with unsourced numbers · {model} · about ${cost:.3f}"),
    "ai_failed": ("AI 叙述未生成：", "AI narrative not written: "),
    "ai_facts": ("事实表与核对明细", "Fact table and check details"),
    "ai_legend": ("✔ 数字与引用的事实一致 · ○ 无数字的解释 · ⚠ 数字存在但引用了别的事实 · ✖ 数字找不到出处",
                  "✔ numbers match the cited facts · ○ no numbers · ⚠ number exists but another fact is cited · ✖ number has no source"),
    "ai_retry": ("重新生成", "Write again"),
    "steps_summary": ("{ok} 项成功 · {failed} 项失败", "{ok} succeeded · {failed} failed"),
    "prob_title": ("概率约 {lo}–{hi} · {tier}", "Probability about {lo}–{hi} · {tier}"),
    "prob_sub": ("{when} 前后股价 ≥ {target} · 证据截至 {asof} · 多种独立方法交叉验证，每个数字可追溯", "Price ≥ {target} around {when} · evidence as of {asof} · independent methods cross-checked; every number traceable"),
    "prob_none": ("数据不足，无法计算概率", "Not enough data to compute a probability"),
    "m_cols": (("方法", "到期时 ≥ 目标", "期间曾触及", "衡量的是什么", "计入区间"), ("Method", "End ≥ target", "Touch before", "What it measures", "In range")),
    "ladder_title": ("不同价位的概率（到期时 ≥ 该价位）", "Probability by price level (end ≥ level)"),
    "ladder_cols": (("价位", "说明", "期权隐含", "历史波动模型"), ("Level", "Label", "Option-implied", "Historical-vol model")),
    "method_detail": ("每种方法的算法、输入和限制", "How each method works, its inputs and limits"),
    "why_trust": ("为什么比直接问 AI 更可信", "Why this is more reliable than asking an AI directly"),
    "why_trust_body": (
        "- 每个数字都是本次实时抓取或计算出来的，带来源链接和时间；不靠模型记忆。\n"
        "- 概率由多种独立方法分别计算并并排展示，分歧会被说明；不是一个凭感觉给的数字。\n"
        "- 同样的输入得到同样的结果（可用 replay 模式回放）。\n"
        "- 取不到的数据会明确列出，不会被编造补齐。\n"
        "- 内部人交易按交易类型区分，授予、行权、代扣税不算作卖出。",
        "- Every number was fetched or computed in this run, with a source link and time; nothing comes from model memory.\n"
        "- The probability is computed by several independent methods shown side by side, and disagreements are explained.\n"
        "- The same inputs give the same result (replayable in replay mode).\n"
        "- Missing data is listed, never filled in.\n"
        "- Insider trades are separated by type: grants, exercises and tax withholding are not counted as selling."),
    "rationale": ("判断依据", "Rationale"), "rechecks": ("何时需要重新评估", "When to review again"),
    "watch": ("观察：", "Watch: "), "threshold": ("阈值：", "Threshold: "), "linked": ("关联：", "Linked to: "),
    "limits": ("这个结论的限制", "Limits of this verdict"),
    "rule": ("规则：", "Rule: "), "sources": ("来源与数值", "Sources and values"),
    "calc_note": ("全部基于你确认的输入与假设，Decimal 精确计算，与网络数据无关。", "Based only on your confirmed inputs and assumptions; exact Decimal arithmetic, no network data."),
    "provenance": ("输入值来源", "Where each input came from"),
    "sens": ("敏感性：不同估值倍数和时间范围下的所需年增速", "Sensitivity: required yearly growth for other multiples and horizons"),
    "sens_note": ("行是估值倍数，列是时间范围（年）。基数：{b}。符号对比已披露增速 {h}：✔ 不高于，◐ 高出不超过 5 个百分点，✖ 高出更多。加粗为你的假设。",
                  "Rows are multiples, columns are horizons (years). Base: {b}. Symbols compare with reported growth {h}: ✔ at or below, ◐ up to 5 pp above, ✖ more. Bold marks your assumption."),
    "sens_none": ("没有可用的基期数值，无法计算敏感性。", "No base value available, so no sensitivity table."),
    "facts_title": ("SEC 已披露财务数据", "SEC reported financials"), "data_table": ("数据表", "Data table"),
    "price_title": ("股价历史", "Price history"), "filings_title": ("SEC 申报记录", "SEC filings"),
    "filings_note": ("申报记录只有元数据，本版本不读取正文。", "Filing records are metadata only; documents are not read in this version."),
    "no_public": ("本次没有取到公开数据，见「运行记录」中的数据错误。", "No public data in this run; see data errors in the run log."),
    "prob_extra": ("附加内容：模型输出，不参与结论，也不是价格预测。", "Extra: a model output; not part of the verdict and not a price prediction."),
    "prob_off": ("未计算。在「证据范围与附加项」里填写年化漂移率 μ（例如 0.07），确认后重新运行即可。",
                 "Not computed. Enter a yearly drift μ (e.g. 0.07) under “Evidence window and extras”, confirm and run again."),
    "prob_na": ("无法计算：", "Not computable: "),
    "assumptions": ("假设", "Assumptions"), "limitations": ("限制", "Limitations"),
    "data_errors": ("数据错误", "Data errors"), "missing_items": ("缺失项", "Missing inputs"),
    "download": ("下载结果 JSON", "Download result JSON"),
    "history_empty": ("还没有保存的案例。", "No saved cases yet."),
    "history_hint": ("选一行查看完整案例；选两行或更多进行对比。", "Select one row to open a case; select two or more to compare."),
    "compare": ("对比", "Comparison"),
}
FIELD_META = {
    # name: (zh label, en label, placeholder, zh help, en help)
    "claim_text": ("观点原文", "Claim", "例如：SYNT 五年后股价达到 100 美元", "要核验的原始说法，原样记录。", "The claim as stated, recorded verbatim."),
    "ticker": ("股票代码", "Ticker", "AAPL", "美股代码。", "US ticker."),
    "target_price": ("目标价", "Target price", "100", "观点给出的目标价格。", "The price the claim names."),
    "horizon_years": ("时间范围（年）", "Horizon (years)", "5", "假设：观点在多少年后兑现，可填小数。", "Assumption: years until the claim should hold; decimals allowed."),
    "currency": ("币种", "Currency", "USD", "3 位 ISO 代码。SEC 财务数据按 USD 比较。", "3-letter ISO code. SEC amounts are compared in USD."),
    "reference_price": ("参考价", "Reference price", "50", "参考值：当前或某日的股价。", "Reference value: the price on a date."),
    "reference_price_date": ("参考价日期", "Reference date", "YYYY-MM-DD", "参考价对应的交易日。", "Trading day of the reference price."),
    "reference_price_source": ("参考价来源", "Reference source", "", "记录参考价从哪里来。", "Where the reference price came from."),
    "current_shares": ("当前股份数（可选）", "Current shares (optional)", "95000000", "参考值：最近一次披露的流通股数。", "Reference value: latest reported shares."),
    "target_assumed_shares": ("目标期股份数", "Target-date shares", "100000000", "假设：目标日期的总股数。只在选择「直接填股数」时使用。", "Assumption: total shares at the target date. Used only with “absolute”."),
    "share_rate_pct": ("股份数年变化（%）", "Yearly share change (%)", "1.0", "假设：每年股数增减的百分比。正数是增发稀释，负数是回购。默认取近年趋势。",
                       "Assumption: yearly % change in share count. Positive = dilution, negative = buybacks. Default: recent trend."),
    "valuation_multiple": ("估值倍数", "Valuation multiple", "25", "假设：目标日期的 P/S 或 P/E。默认等于当前倍数。", "Assumption: P/S or P/E at the target date. Default: today's multiple."),
    "base_annual_metric": ("基期年度指标（可选）", "Base annual metric (optional)", "200000000", "参考值：P/S 填年营收，P/E 填年净利润。", "Reference value: annual revenue for P/S, net income for P/E."),
    "base_metric_currency": ("基期指标币种（可选）", "Base metric currency (optional)", "USD", "须与价格币种一致。", "Must match the price currency."),
    "base_metric_period": ("基期期间（可选）", "Base period (optional)", "FY2025", "基期指标对应的财年。", "Fiscal year of the base metric."),
    "filings_since": ("申报检索起始日（可选）", "Filings since (optional)", "YYYY-MM-DD", "检查 SEC 申报覆盖范围的起点。", "Start of the SEC filing window."),
    "probability_drift": ("年化漂移率 μ（假设）", "Yearly drift μ (assumption)", "0.07", "附加项：0.07 = 7%。留空则不计算概率参考。", "Extra: 0.07 = 7%. Leave empty to skip the probability section."),
    "probability_volatility": ("年化波动率 σ（可选）", "Yearly volatility σ (optional)", "", "附加项：留空时使用历史波动率。", "Extra: empty uses historical volatility."),
}
TEXT_FIELDS = list(FIELD_META)
DRAFT_FIELDS = [n for n in TEXT_FIELDS if n != "share_rate_pct"]  # the page's % field maps to share_change_rate
FIELD_ALIASES = {"share_change_rate": "share_rate_pct", "share_change_mode": "share_rate_pct"}
# one label for both languages keeps the widget stable when the language changes
SHARE_MODES = {"trend": "近年趋势 · trend", "flat": "不变 · flat", "rate": "自定义 % · custom %", "absolute": "直接填股数 · absolute"}
CLAIM_FIELDS = ("claim_text", "ticker", "target_price", "horizon_years")
# one label for both languages keeps the widget stable when the language changes
METHODS = {"price_to_sales": "P/S · 市销率 price-to-sales", "price_to_earnings": "P/E · 市盈率 price-to-earnings"}
MODE_LABELS = {
    "live": ("live：实时请求公开数据", "live: request public data"),
    "record": ("record：实时请求并录制快照", "record: request and save snapshots"),
    "replay": ("replay：只读录制快照，不联网", "replay: recorded snapshots only, offline"),
    "synthetic": ("synthetic：合成示例数据", "synthetic: example data"),
}
VIEW_LABELS = {"new": "新建 · New", "history": "历史 · History"}
EXAMPLES = {"ps": REPO_ROOT / "examples" / "synthetic_claim_ps.json", "pe": REPO_ROOT / "examples" / "synthetic_claim_pe.json"}
VERDICT_STYLE = {"supported_today": ("✔", "good"), "partially_supported": ("◐", "warning"),
                 "not_supported_today": ("✖", "critical"), "insufficiently_specified": ("?", "neutral")}
STANCE = {"supporting": ("✔", "支持", "Supporting"), "contrary": ("✖", "反对", "Contrary"),
          "missing": ("…", "缺失", "Missing"), "neutral": ("·", "背景", "Context")}
STATUS = {
    "evaluated": ("已评估", "Evaluated"),
    "evaluated_with_provider_errors": ("已评估（部分数据源失败）", "Evaluated (some sources failed)"),
    "blocked_invalid_input": ("输入无效", "Invalid input"),
    "blocked_unconfirmed": ("未确认", "Not confirmed"),
    "blocked_confirmation_stale": ("确认已失效", "Confirmation stale"),
}
CALC = {
    "required_return": ("所需总收益", "Required total return"),
    "annualized_price_return": ("所需年化收益", "Required yearly return"),
    "target_market_cap": ("目标市值", "Target market cap"),
    "implied_share_count_change": ("股份数变化", "Share count change"),
    "target_assumed_shares": ("目标期股份数（推算）", "Target-date shares (derived)"),
    "required_annual_revenue": ("所需年营收", "Required annual revenue"),
    "required_annual_net_income": ("所需年净利润", "Required annual net income"),
    "required_metric_cagr": ("所需指标年增速（相对你填写的基期）", "Required metric growth (from your base)"),
}
ISSUE_ZH = {
    "not_a_number": "{f}：不是数字", "nan_not_allowed": "{f}：不能是 NaN", "infinity_not_allowed": "{f}：不能是无穷大",
    "must_be_positive": "{f}：必须大于 0", "invalid_date": "{f}：日期格式应为 YYYY-MM-DD", "date_in_future": "{f}：日期不能晚于今天",
    "invalid_ticker": "{f}：格式不正确", "invalid_currency": "{f}：须为 3 位字母代码",
    "currency_mismatch": "{f}：与价格币种不一致，不做汇率换算", "unsupported_method": "{f}：只能是 P/S 或 P/E",
    "out_of_range": "{f}：超出允许范围", "unknown_field": "{f}：包含未知字段", "unsupported_mode": "{f}：选项无效",
}
MISSING_ZH = {
    "base_annual_metric": "未填基期指标，不计算相对基期的增速",
    "base_metric_currency": "未填基期币种，不计算相对基期的增速",
    "filings_since": "未填检索起始日，不检查申报覆盖范围",
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


def lang() -> str:
    return st.session_state.get("lang", "zh")


def t(key: str, **kw) -> str:
    zh, en = S[key]
    text = zh if lang() == "zh" else en
    return text.format(**kw) if kw else text


def pick(en: str, zh: str) -> str:
    return zh if lang() == "zh" and zh else en


def label_of(name: str) -> str:
    meta = FIELD_META.get(FIELD_ALIASES.get(name, name))
    if meta is None:
        return name
    return meta[0] if lang() == "zh" else meta[1]


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
        return "无法计算" if lang() == "zh" else "not computable"
    if item.unit.startswith("ratio"):
        return fmt_pct(item.value)
    return fmt_amount(item.value, item.unit.split(" ")[0])


def issue_text(issue) -> str:
    if lang() == "zh" and issue.code in ISSUE_ZH:
        return ISSUE_ZH[issue.code].format(f=label_of(issue.field))
    return f"`{issue.field}` {issue.code}: {issue.message}"


def missing_text(m) -> str:
    if lang() == "zh":
        return f"{label_of(m.field)}（{MISSING_ZH.get(m.field, m.message)}）"
    return f"{label_of(m.field)} ({m.message})"


# ------------------------------------------------------------------ state


@st.cache_resource
def get_service() -> CaseService:
    settings = load_settings()
    return CaseService(settings, store=CaseStore(settings.case_dir))


def _init_state() -> None:
    for name in TEXT_FIELDS:
        st.session_state.setdefault(f"f_{name}", "")
    st.session_state.setdefault("f_valuation_method", "price_to_sales")
    st.session_state.setdefault("f_share_mode", "trend")
    st.session_state.setdefault("example_values", {})
    st.session_state.setdefault("sec_mode", "live")
    st.session_state.setdefault("lang", "zh")
    st.session_state.setdefault("view", "new")
    for key in ("confirmation", "confirm_feedback", "result", "result_mode", "run_error", "prefill_msg", "prefill_snapshot", "sec_msg",
                "extract_msg", "extraction"):
        st.session_state.setdefault(key, None)
    st.session_state.setdefault("prefill_sources", {})
    st.session_state.setdefault("history_selected", None)
    st.session_state.setdefault("ai_msg", None)


def _reset_confirmation() -> None:
    st.session_state["confirmation"] = None
    st.session_state["confirm_feedback"] = None


def _load_example(key: str) -> None:
    draft = json.loads(Path(EXAMPLES[key]).read_text(encoding="utf-8"))["draft"]
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = str(draft.get(name) or "")
    st.session_state["f_valuation_method"] = draft["valuation_method"]
    st.session_state["f_share_mode"] = "absolute" if draft.get("target_assumed_shares") else "trend"
    # example values count as untouched, so "Fill in the rest" for another ticker replaces them
    st.session_state["example_values"] = {name: st.session_state[f"f_{name}"] for name in TEXT_FIELDS} | {"share_mode": st.session_state["f_share_mode"]}
    st.session_state["sec_mode"] = "synthetic"
    st.session_state["view"] = "new"
    st.session_state["prefill_sources"] = {}
    st.session_state["prefill_msg"] = None
    st.session_state["extract_msg"] = None
    _reset_confirmation()


def _clear_form() -> None:
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = ""
    st.session_state["f_valuation_method"] = "price_to_sales"
    st.session_state["f_share_mode"] = "trend"
    st.session_state["example_values"] = {}
    st.session_state["prefill_sources"] = {}
    st.session_state["prefill_msg"] = None
    st.session_state["extract_msg"] = None
    st.session_state["prefill_snapshot"] = None
    _reset_confirmation()


def _metric_suggestion(snap: ReferenceSnapshot, method: str):
    return snap.revenue_suggestion if method == "price_to_sales" else snap.net_income_suggestion


def _multiple_suggestion(snap: ReferenceSnapshot, method: str):
    return snap.assumption_suggestions.get("valuation_multiple_ps" if method == "price_to_sales" else "valuation_multiple_pe")


def _is_untouched(name: str) -> bool:
    """Empty, or still holding the value the prefill or a loaded example put there."""
    current = st.session_state.get(f"f_{name}", "")
    src = st.session_state["prefill_sources"].get(name)
    example = st.session_state["example_values"].get(name)
    return not current.strip() or (src is not None and current == src[0]) or (example is not None and current == example)


def _pct_text(rate: str) -> str:
    """'0.0104' -> '1.04'"""
    return format((Decimal(rate) * 100).quantize(Decimal("0.01")).normalize(), "f")


def _apply(name: str, sug, sources: dict) -> None:
    st.session_state[f"f_{name}"] = sug.value
    sources[name] = (sug.value, sug.source)


def _prefill() -> None:
    ticker = st.session_state["f_ticker"].strip()
    if not ticker:
        st.session_state["prefill_msg"] = ("error", S["need_ticker"])
        return
    try:
        snap = get_service().reference_snapshot(ticker, mode=st.session_state["sec_mode"])
    except ValueError as exc:
        st.session_state["prefill_msg"] = ("error", (str(exc), str(exc)))
        return
    sources: dict[str, tuple[str, str]] = dict(st.session_state["prefill_sources"])
    method = st.session_state["f_valuation_method"]
    for name, sug in snap.suggestions.items():  # public reference values are refreshed
        if f"f_{name}" in st.session_state:
            _apply(name, sug, sources)
    metric = _metric_suggestion(snap, method)
    if metric is not None:
        _apply("base_annual_metric", metric, sources)
    if snap.period_suggestion is not None:
        _apply("base_metric_period", snap.period_suggestion, sources)
    st.session_state["prefill_sources"] = sources
    defaults = []
    for name, sug in (("valuation_multiple", _multiple_suggestion(snap, method)),
                      ("filings_since", snap.assumption_suggestions.get("filings_since"))):
        if sug is not None and _is_untouched(name):  # assumptions only fill empty or untouched fields
            _apply(name, sug, sources)
            if name != "filings_since":
                defaults.append((name, sug.value))
    rate = snap.assumption_suggestions.get("share_change_rate")
    mode_untouched = st.session_state["f_share_mode"] == "trend" or _is_untouched("share_mode")
    if rate is not None and mode_untouched and (st.session_state["f_share_mode"] != "trend" or _is_untouched("share_rate_pct")):
        st.session_state["f_share_mode"] = "trend"
        st.session_state["f_target_assumed_shares"] = ""
        pct = _pct_text(rate.value)
        st.session_state["f_share_rate_pct"] = pct
        sources["share_rate_pct"] = (pct, rate.source)
        defaults.append(("share_rate_pct", pct + "%"))
    st.session_state["example_values"] = {}
    st.session_state["prefill_snapshot"] = snap
    errors = "; ".join(f"{e.provider_id}: {e.code}" for e in snap.provider_errors)
    n = len(sources)
    if not sources:
        st.session_state["prefill_msg"] = ("error", (S["nothing_fetched"][0].format(errors=errors), S["nothing_fetched"][1].format(errors=errors)))
    else:
        zh = S["filled"][0].format(n=n)
        en = S["filled"][1].format(n=n)
        if defaults:
            items_zh = "；".join(f"{FIELD_META[k][0]} = {v}" for k, v in defaults)
            items_en = "; ".join(f"{FIELD_META[k][1]} = {v}" for k, v in defaults)
            zh += " " + S["defaults_used"][0].format(items=items_zh)
            en += " " + S["defaults_used"][1].format(items=items_en)
        if errors:
            zh += " " + S["not_fetched"][0].format(errors=errors)
            en += " " + S["not_fetched"][1].format(errors=errors)
        st.session_state["prefill_msg"] = ("warning" if errors else "success", (zh, en))
    _reset_confirmation()


def _extract_and_fill() -> None:
    """Read ticker, target and horizon from the sentence, then fill the rest from public data."""
    text = st.session_state["f_claim_text"].strip()
    if not text:
        if st.session_state["f_ticker"].strip():
            st.session_state["extract_msg"] = None
            _prefill()  # no sentence, but a ticker: fill from public data only
        else:
            st.session_state["prefill_msg"] = ("error", S["need_text"])
        return
    ex = get_service().extract(text, mode=st.session_state["sec_mode"])
    sources = dict(st.session_state["prefill_sources"])
    found_zh, found_en = [], []
    for name, value, src in (("ticker", ex.ticker, ex.ticker_text), ("target_price", ex.target_price, ex.target_text),
                             ("horizon_years", ex.horizon_years, ex.horizon_text)):
        if value is None or not _is_untouched(name):
            continue
        val = format(value.normalize(), "f") if isinstance(value, Decimal) else str(value)
        st.session_state[f"f_{name}"] = val
        sources[name] = (val, f"{CLAIM_TEXT_PREFIX}{src}")
        found_zh.append(f"{FIELD_META[name][0]} {val}（「{src}」）")
        found_en.append(f"{FIELD_META[name][1]} {val} (“{src}”)")
    st.session_state["prefill_sources"] = sources
    st.session_state["extraction"] = ex
    zh = S["recognized"][0].format(items="；".join(found_zh)) if found_zh else S["recognized_none"][0]
    en = S["recognized"][1].format(items="; ".join(found_en)) if found_en else S["recognized_none"][1]
    if ex.notes_zh:
        zh += " " + " ".join(ex.notes_zh)
        en += " " + " ".join(ex.notes_en)
    st.session_state["extract_msg"] = ("warning" if ex.notes_zh else "info", (zh, en))
    if st.session_state["f_ticker"].strip():
        _prefill()
    # "doubles" / "10x": the target is the reference price times the factor
    ref = st.session_state["f_reference_price"].strip()
    if ex.target_multiple is not None and ref and _is_untouched("target_price"):
        try:
            target = (Decimal(ref) * ex.target_multiple).quantize(Decimal("0.01"))
        except Exception:
            return
        val = format(target.normalize(), "f")
        st.session_state["f_target_price"] = val
        st.session_state["prefill_sources"]["target_price"] = (val, f"{CLAIM_TEXT_PREFIX}{ex.target_text} x {ex.target_multiple} of reference {ref}")
        note = (S["multiple_target"][0].format(ref=ref, x=ex.target_multiple, text=ex.target_text),
                S["multiple_target"][1].format(ref=ref, x=ex.target_multiple, text=ex.target_text))
        kind, (mzh, men) = st.session_state["extract_msg"]
        st.session_state["extract_msg"] = (kind, (mzh + " " + note[0], men + " " + note[1]))


def _method_changed() -> None:
    """Keep the prefilled base metric and default multiple consistent with the valuation method."""
    snap: Optional[ReferenceSnapshot] = st.session_state.get("prefill_snapshot")
    sources = st.session_state["prefill_sources"]
    if snap is None:
        return
    method = st.session_state["f_valuation_method"]
    for name, sug in (("base_annual_metric", _metric_suggestion(snap, method)), ("valuation_multiple", _multiple_suggestion(snap, method))):
        if name not in sources or st.session_state[f"f_{name}"] != sources[name][0]:
            continue  # empty or edited by the user; leave it alone
        if sug is None:
            st.session_state[f"f_{name}"] = ""
            sources.pop(name)
        else:
            _apply(name, sug, sources)


def _save_sec_contact() -> None:
    email = st.session_state.get("sec_email_input", "").strip()
    ua = f"TickerCase/0.2 {email}"
    if not email or validate_user_agent(ua):
        st.session_state["sec_msg"] = ("error", S["sec_bad"])
        return
    try:
        write_env_value("SEC_USER_AGENT", ua)
    except ValueError:
        st.session_state["sec_msg"] = ("error", S["sec_bad"])
        return
    get_service.clear()
    st.session_state["sec_msg"] = ("success", S["sec_saved"])


def _share_rate_value() -> Optional[str]:
    """The page takes a percent; the draft takes a yearly rate (1.5 -> 0.015). Unparseable text passes through for validation."""
    mode = st.session_state["f_share_mode"]
    if mode == "absolute":
        return None
    if mode == "flat":
        return "0"
    text = st.session_state["f_share_rate_pct"].strip().rstrip("%").strip()
    if not text:
        return None
    try:
        return format(Decimal(text) / 100, "f")
    except Exception:
        return text


def _save_ai_key() -> None:
    key = st.session_state.get("ai_key_input", "").strip()
    if not key.startswith("sk-ant-") or len(key) < 20 or " " in key:
        st.session_state["ai_msg"] = ("error", ("key 格式不对（应以 sk-ant- 开头）。", "The key should start with sk-ant-."))
        return
    write_env_value("ANTHROPIC_API_KEY", key)
    get_service.clear()
    st.session_state["ai_msg"] = ("success", ("已保存。", "Saved."))


def _narrate(key_suffix: str) -> None:
    result = st.session_state["history_selected"] if key_suffix != "current" else st.session_state["result"]
    if result is None:
        return
    get_service().narrate(result)


def current_draft() -> ClaimDraft:
    values = {name: (st.session_state[f"f_{name}"] or None) for name in DRAFT_FIELDS}
    values["valuation_method"] = st.session_state["f_valuation_method"]
    mode = st.session_state["f_share_mode"]
    values["share_change_mode"] = mode
    values["share_change_rate"] = _share_rate_value()
    if mode != "absolute":
        values["target_assumed_shares"] = None
    # a source label only applies while the field still holds the value it filled in
    sources = {("share_change_rate" if name == "share_rate_pct" else name): src
               for name, (val, src) in st.session_state["prefill_sources"].items() if st.session_state.get(f"f_{name}") == val}
    if mode != "trend":
        sources.pop("share_change_rate", None)
    values["field_sources"] = sources or None
    return ClaimDraft(**values)


# ------------------------------------------------------------------ inputs


def field(name: str, container=None) -> None:
    zh, en, placeholder, help_zh, help_en = FIELD_META[name]
    label, help_text = (zh, help_zh) if lang() == "zh" else (en, help_en)
    target = container or st
    src = st.session_state["prefill_sources"].get(name)
    if src and st.session_state.get(f"f_{name}") == src[0]:
        zh = lang() == "zh"
        if src[1].startswith(DEFAULT_PREFIX):
            mark, kind = "◇", ("默认假设" if zh else "Default assumption")
        elif src[1].startswith(CLAIM_TEXT_PREFIX):
            mark, kind = "✎", ("从原文识别" if zh else "Read from the claim")
        else:
            mark, kind = "ⓘ", ("来自公开数据" if zh else "From public data")
        label = f"{label} {mark}"
        help_text = f"{help_text}\n\n{kind}：{src[1].removeprefix(DEFAULT_PREFIX).removeprefix(CLAIM_TEXT_PREFIX)}"
    if name == "claim_text":
        target.text_area(label, key=f"f_{name}", placeholder=placeholder, help=help_text, height=80)
    else:
        target.text_input(label, key=f"f_{name}", placeholder=placeholder, help=help_text)


def render_claim_box() -> None:
    with st.container(border=True):
        st.markdown(f"**{t('s1')}**")
        st.caption(t("s1_hint"))
        field("claim_text")
        b1, b2 = st.columns([1, 3])
        b1.button(t("prefill"), key="btn_prefill", on_click=_extract_and_fill, help=t("prefill_help"), type="primary", use_container_width=True)
        with b2:
            for key in ("extract_msg", "prefill_msg"):
                msg = st.session_state[key]
                if msg:
                    kind, (zh, en) = msg
                    {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[kind](zh if lang() == "zh" else en)
        st.caption(t("detail_fields"))
        c = st.columns([1.2, 1, 1])
        field("ticker", c[0])
        field("target_price", c[1])
        field("horizon_years", c[2])


def render_share_input() -> None:
    mode = st.session_state["f_share_mode"]
    if mode == "absolute":
        field("target_assumed_shares")
        return
    if mode == "flat":
        st.caption(t("flat_note"))
    else:
        field("share_rate_pct")
    rate = _share_rate_value()
    current, years = st.session_state["f_current_shares"].strip(), st.session_state["f_horizon_years"].strip()
    if not current:
        st.caption(t("shares_need_current"))
        return
    try:
        r, c, y = Decimal(rate or "0"), Decimal(current.replace(",", "")), Decimal(years)
        n = c * ((1 + r).ln() * y).exp()
    except Exception:
        return
    st.caption(t("shares_result", n=f"{n:,.0f}", c=f"{c:,.0f}", sign="+" if r >= 0 else "−", r=f"{abs(r) * 100:.2f}", y=format(y.normalize(), "f")))


def render_rest(expanded: bool) -> None:
    with st.expander(t("rest"), expanded=expanded):
        left, right = st.columns(2)
        with left.container(border=True):
            st.markdown(f"**{t('price_box')}**")
            c = st.columns(3)
            field("reference_price", c[0])
            field("reference_price_date", c[1])
            field("currency", c[2])
            field("reference_price_source")
            c = st.columns(2)
            field("current_shares", c[0])
            c[1].selectbox(t("share_mode"), options=list(SHARE_MODES), format_func=SHARE_MODES.get, key="f_share_mode", help=t("share_mode_help"))
            render_share_input()
        with right.container(border=True):
            st.markdown(f"**{t('val_box')}**")
            c = st.columns(2)
            c[0].selectbox(t("method"), options=list(METHODS), format_func=METHODS.get,
                           key="f_valuation_method", on_change=_method_changed, help=t("method_help"))
            field("valuation_multiple", c[1])
            field("base_annual_metric")
            c = st.columns(2)
            field("base_metric_currency", c[0])
            field("base_metric_period", c[1])
        with st.container(border=True):
            st.markdown(f"**{t('extra_box')}**")
            c = st.columns(3)
            field("filings_since", c[0])
            field("probability_drift", c[1])
            field("probability_volatility", c[2])
            st.caption(t("prob_note"))


# ------------------------------------------------------------------ results


def stepper(claim_ok: bool, inputs_ok: bool, state: str, has_result: bool) -> None:
    if has_result:
        classes = ("done", "done", "done", "done")
    elif state == "confirmed":
        classes = ("done", "done", "done", "now")
    elif inputs_ok:
        classes = ("done", "done", "now", "")
    elif claim_ok:
        classes = ("done", "now", "", "")
    else:
        classes = ("now", "", "", "")
    labels = ("① 填写观点", "② 补全并核对", "③ 确认", "④ 查看案例") if lang() == "zh" else ("① Claim", "② Fill in and check", "③ Confirm", "④ Case")
    html = "".join(f'<span class="tc-step {cls}">{label}</span>' for label, cls in zip(labels, classes))
    st.markdown(f'<div class="tc-steps">{html}</div>', unsafe_allow_html=True)


def render_verdict(result: CaseResult) -> None:
    v = result.verdict
    if v is None:
        return
    icon, tone = VERDICT_STYLE[v.label]
    zh = v.display_zh or VERDICT_DISPLAY_ZH[v.label]
    title = f"{icon} {zh} · {v.display}" if lang() == "zh" else f"{icon} {v.display} · {zh}"
    st.markdown(
        f'<div class="tc-verdict tc-{tone}"><div class="tc-label">{title}</div>'
        f'<div class="tc-sub">{t("as_of", d=v.as_of, r=v.rules_version)}</div></div>',
        unsafe_allow_html=True,
    )


def render_key_numbers(result: CaseResult) -> None:
    calc = {c.name: c for c in result.calculations}
    claim = result.confirmed_claim.values
    metric = "required_annual_revenue" if claim.valuation_method.value == "price_to_sales" else "required_annual_net_income"
    growth = next((i for i in result.evidence_items if i.id == "E1"), None)
    req = Decimal(growth.measured["required_cagr_from_reported"]) if growth and "required_cagr_from_reported" in growth.measured else None
    hist = Decimal(growth.measured["reported_cagr"]) if growth and "reported_cagr" in growth.measured else None
    i = 0 if lang() == "zh" else 1
    cols = st.columns(4)
    for col, name in zip(cols, ("required_return", "annualized_price_return", "target_market_cap", metric)):
        col.metric(CALC[name][i], fmt_calc(calc[name]), help=calc[name].formula)
    if req is not None:
        cols = st.columns(4)
        cols[0].metric("所需指标年增速（相对已披露）" if i == 0 else "Required metric growth (from reported)", fmt_pct(req))
        if hist is not None:
            cols[1].metric("近年已披露增速" if i == 0 else "Reported growth", fmt_pct(hist))
            cols[2].metric("差距（百分点）" if i == 0 else "Gap (pp)", f"{(req - hist) * 100:+.2f}")


def tab_conclusion(result: CaseResult) -> None:
    v = result.verdict
    st.markdown(f"#### {t('rationale')}")
    for line in (v.rationale_zh if lang() == "zh" and v.rationale_zh else v.rationale):
        st.markdown(f"- {line}")
    st.markdown(f"#### {t('rechecks')}")
    cols = st.columns(2)
    for idx, r in enumerate(result.recheck_conditions):
        with cols[idx % 2].container(border=True):
            st.markdown(f"**{r.id}** · {pick(r.trigger, r.trigger_zh)}")
            st.caption(t("watch") + pick(r.watch, r.watch_zh))
            extra = []
            if r.threshold:
                extra.append(t("threshold") + r.threshold)
            if r.linked_to:
                extra.append(t("linked") + ", ".join(r.linked_to))
            if extra:
                st.caption(" · ".join(extra))
    with st.expander(t("limits")):
        for line in (v.limitations_zh if lang() == "zh" and v.limitations_zh else v.limitations):
            st.markdown(f"- {line}")


def tab_evidence(result: CaseResult) -> None:
    i = 1 if lang() == "zh" else 2
    cols = st.columns(4)
    for col, s in zip(cols, STANCE):
        col.metric(f"{STANCE[s][0]} {STANCE[s][i]}", sum(1 for x in result.evidence_items if x.stance == s))
    order = {"contrary": 0, "supporting": 1, "missing": 2, "neutral": 3}
    for item in sorted(result.evidence_items, key=lambda x: (order[x.stance], x.id)):
        with st.container(border=True):
            st.markdown(f"**{STANCE[item.stance][0]} {STANCE[item.stance][i]} · {item.id} {pick(item.title, item.title_zh)}**")
            st.write(pick(item.detail, item.detail_zh))
            if item.rule:
                st.caption(t("rule") + pick(item.rule, item.rule_zh or ""))
            if item.sources:
                with st.expander(t("sources")):
                    for s in item.sources:
                        text = s.label + (f" · {s.data_mode.value}" if s.data_mode else "")
                        st.markdown(f"- [{text}]({s.url})" if s.url else f"- {text}")
                    if item.measured:
                        st.json(item.measured, expanded=False)


def render_sensitivity(result: CaseResult) -> None:
    st.markdown(f"#### {t('sens')}")
    sens = result.sensitivity
    if sens is None:
        st.caption(t("sens_none"))
        return
    hist = Decimal(sens.reported_cagr) if sens.reported_cagr else None

    def cell(v: Optional[str]) -> str:
        if v is None:
            return "—"
        d = Decimal(v)
        mark = "" if hist is None else (" ✔" if d <= hist else " ◐" if d - hist <= Decimal("0.05") else " ✖")
        return f"{d * 100:.1f}%{mark}"

    unit = "年" if lang() == "zh" else "y"
    cols = [f"{h} {unit}" for h in sens.horizons]
    rows = [f"{('倍数' if lang() == 'zh' else 'multiple')} {m}" for m in sens.multiples]
    df = pd.DataFrame([[cell(v) for v in row] for row in sens.required_cagr], index=rows, columns=cols)
    a_row = sens.multiples.index(sens.assumed_multiple) if sens.assumed_multiple in sens.multiples else None
    a_col = sens.horizons.index(sens.assumed_horizon) if sens.assumed_horizon in sens.horizons else None
    tone = {"✔": "rgba(12,163,12,0.12)", "◐": "rgba(250,178,25,0.18)", "✖": "rgba(208,59,59,0.12)"}

    def style(frame: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for r in range(frame.shape[0]):
            for c in range(frame.shape[1]):
                css = next((f"background-color: {col}" for sym, col in tone.items() if frame.iat[r, c].endswith(sym)), "")
                if r == a_row and c == a_col:
                    css += "; font-weight: 700; border: 2px solid #2a78d6"
                elif r == a_row or c == a_col:
                    css += "; font-weight: 600"
                out.iat[r, c] = css
        return out

    st.dataframe(df.style.apply(style, axis=None), use_container_width=True)
    base = f"{fmt_amount(sens.base_value)} ({sens.base_label})"
    st.caption(t("sens_note", b=base, h=fmt_pct(hist) if hist is not None else "—"))


def tab_calculations(result: CaseResult) -> None:
    st.caption(t("calc_note"))
    i = 0 if lang() == "zh" else 1
    zh = lang() == "zh"
    rows = [
        {
            ("项目" if zh else "Item"): CALC.get(c.name, (c.name, c.name))[i],
            ("数值" if zh else "Value"): fmt_calc(c),
            ("原始值" if zh else "Exact value"): "" if c.value is None else format(c.value, "f"),
            ("公式" if zh else "Formula"): c.formula,
            ("输入" if zh else "Inputs"): json.dumps(c.inputs, ensure_ascii=False),
            ("假设/说明" if zh else "Assumptions / notes"): "; ".join(c.assumptions + ([c.reason] if c.reason else [])),
        }
        for c in result.calculations
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    render_sensitivity(result)
    with st.expander(t("provenance")):
        st.dataframe([{("字段" if zh else "Field"): label_of(k), ("来源" if zh else "Source"): v}
                      for k, v in result.confirmed_claim.value_provenance.items()], hide_index=True, use_container_width=True)


def _rule(value: float, text: str, axis: str = "y") -> alt.Chart:
    df = pd.DataFrame({"v": [value], "t": [text]})
    enc = alt.Y("v:Q") if axis == "y" else alt.X("v:Q")
    rule = alt.Chart(df).mark_rule(strokeWidth=1, strokeDash=[4, 3], color="#898781").encode(**{axis: enc})
    if axis == "y":
        label = alt.Chart(df).mark_text(align="left", dx=4, dy=-6, color="#898781").encode(y="v:Q", text="t:N", x=alt.value(0))
    else:
        label = alt.Chart(df).mark_text(align="left", dx=4, y=8, color="#898781").encode(x="v:Q", text="t:N")
    return rule + label


def tab_public_data(result: CaseResult) -> None:
    color = series_color()
    zh = lang() == "zh"
    facts, market = result.reported_facts, result.market
    claim = result.confirmed_claim.values
    if facts is not None:
        st.markdown(f"#### {t('facts_title')} · {facts.company_name or facts.ticker}（{facts.data_mode.value}）")
        is_ps = claim.valuation_method.value == "price_to_sales"
        series = (facts.revenue if is_ps else facts.net_income)[-6:]
        required = next((c.value for c in result.calculations if c.name in ("required_annual_revenue", "required_annual_net_income")), None)
        if series:
            label = ("年营收" if is_ps else "年净利润") if zh else ("Annual revenue" if is_ps else "Annual net income")
            df = pd.DataFrame({"FY": [f"FY{p.period_end.year}" for p in series], "v": [float(p.value) for p in series],
                               "end": [p.period_end.isoformat() for p in series], "filed": [f"{p.form} {p.filed}" for p in series]})
            chart = alt.Chart(df).mark_bar(size=24, color=color, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("FY:N", title=None, sort=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("v:Q", title=f"{label} (USD)", axis=alt.Axis(format="~s")),
                tooltip=[alt.Tooltip("FY:N"), alt.Tooltip("v:Q", format=",.0f"), alt.Tooltip("end:N"), alt.Tooltip("filed:N")],
            )
            peak = max(float(p.value) for p in series)
            if required is not None and float(required) <= 5 * peak:
                chart = chart + _rule(float(required), ("观点所需 " if zh else "Claim needs ") + fmt_amount(required))
            st.altair_chart(chart.properties(height=260), use_container_width=True)
            if required is not None and float(required) > 5 * peak:
                st.caption((f"观点所需 {fmt_amount(required)}，超过图中最大值 5 倍，未画参考线。") if zh
                           else f"The claim needs {fmt_amount(required)}, more than 5x the chart's maximum; no line drawn.")
        rows = []
        kinds = (("营收", "Revenue", facts.revenue), ("净利润", "Net income", facts.net_income), ("股份数", "Shares", facts.shares_outstanding))
        for kzh, ken, pts in kinds:
            for p in pts[-5:]:
                rows.append({"metric": kzh if zh else ken, "period_end": p.period_end.isoformat(), "value": fmt_amount(p.value),
                             "concept": p.concept, "form": p.form, "filed": p.filed.isoformat(), "accession": p.accession})
        with st.expander(t("data_table")):
            st.dataframe(rows, hide_index=True, use_container_width=True)
            st.caption(facts.source_url)
    if market is not None:
        st.markdown(f"#### {t('price_title')} · {market.symbol}（{market.data_mode.value}）")
        df = pd.DataFrame({"day": [p.day for p in market.price_series], "close": [float(p.close) for p in market.price_series]})
        line = alt.Chart(df).mark_line(strokeWidth=2, color=color).encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%Y-%m", labelAngle=0)),
            y=alt.Y("close:Q", title=("收盘价" if zh else "Close") + f" ({market.currency or ''})"),
        )
        hover = alt.selection_point(fields=["day"], nearest=True, on="pointerover", empty=False)
        points = alt.Chart(df).mark_point(size=80, filled=True, color=color, stroke="white", strokeWidth=2).encode(
            x="day:T", y="close:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("day:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.2f")],
        ).add_params(hover)
        ref = _rule(float(claim.reference_price), ("你的参考价 " if zh else "Your reference ") + format(claim.reference_price, "f"))
        st.altair_chart((line + points + ref).properties(height=260), use_container_width=True)
        vol = fmt_pct(market.annualized_volatility) if market.annualized_volatility is not None else "—"
        st.caption((f"最新收盘 {format(market.last_close, 'f')}（{market.last_date}），历史年化波动率 {vol}。目标价 {claim.target_price} 未画入此图。"
                    if zh else f"Latest close {format(market.last_close, 'f')} ({market.last_date}); historical volatility {vol}. "
                    f"Target {claim.target_price} not drawn.") + " " + market.note)
    if result.coverage is not None:
        st.markdown(f"#### {t('filings_title')}")
        msg = pick(result.coverage.message, result.coverage.message_zh)
        (st.warning if result.coverage.coverage_gap else st.caption)(msg)
    if result.evidence_records:
        rows = [{"form": r.form_type, "filed": r.filing_date.isoformat(), "period": r.report_date.isoformat() if r.report_date else "",
                 "document": r.document_url or "", "index": r.filing_index_url, "data_mode": r.data_mode.value}
                for r in result.evidence_records]
        st.dataframe(rows, use_container_width=True, hide_index=True,
                     column_config={"document": st.column_config.LinkColumn(), "index": st.column_config.LinkColumn()})
        st.caption(t("filings_note"))
    if facts is None and market is None and not result.evidence_records:
        st.info(t("no_public"))


def tab_probability(result: CaseResult) -> None:
    p = result.probability
    zh = lang() == "zh"
    st.caption(t("prob_extra"))
    if p is None:
        st.info(t("prob_off"))
        return
    if p.status != "ok":
        st.warning(t("prob_na") + pick(p.reason or "", p.reason_zh or ""))
        return
    cols = st.columns(4)
    cols[0].metric((f"到期价 ≥ 目标价 {p.target_price} 的概率" if zh else f"P(price ≥ target {p.target_price})"), f"{p.target_probability * 100:.1f}%")
    cols[1].metric("中位数价格" if zh else "Median price", f"{p.median_price:,.2f}")
    cols[2].metric("10% 分位价格" if zh else "10th percentile", f"{p.p10_price:,.2f}")
    cols[3].metric("90% 分位价格" if zh else "90th percentile", f"{p.p90_price:,.2f}")
    color = series_color()
    df = pd.DataFrame({"price": [float(x.price) for x in p.points], "prob": [x.probability_at_or_above for x in p.points]})
    line = alt.Chart(df).mark_line(strokeWidth=2, color=color, point=alt.OverlayMarkDef(size=70, filled=True, color=color, stroke="white", strokeWidth=2)).encode(
        x=alt.X("price:Q", title="到期价格水平" if zh else "Price level at the horizon"),
        y=alt.Y("prob:Q", title="P(到期价格 ≥ 该水平)" if zh else "P(price ≥ level)", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("price:Q", format=",.2f"), alt.Tooltip("prob:Q", format=".1%")],
    )
    target = _rule(float(p.target_price), ("目标价 " if zh else "Target ") + format(p.target_price, "f"), axis="x")
    st.altair_chart((line + target).properties(height=280), use_container_width=True)
    st.dataframe([{("价格水平" if zh else "Price level"): f"{x.price:,.2f}",
                   ("P(到期价 ≥ 水平)" if zh else "P(price ≥ level)"): f"{x.probability_at_or_above * 100:.1f}%"} for x in p.points],
                 hide_index=True, use_container_width=True)
    st.markdown(f"**{t('assumptions')}**")
    for a in (p.assumptions_zh if zh and p.assumptions_zh else p.assumptions):
        st.markdown(f"- {a}")
    st.markdown(f"**{t('limitations')}**")
    for a in (p.limitations_zh if zh and p.limitations_zh else p.limitations):
        st.markdown(f"- {a}")
    st.caption(p.model)


def tab_run_log(result: CaseResult, key_suffix: str) -> None:
    status = STATUS[result.status.value][0 if lang() == "zh" else 1]
    st.caption(f"case_id {result.case_id} · {result.created_at.isoformat()} · {status}")
    st.json(result.data_modes, expanded=False)
    if result.provider_errors:
        st.markdown(f"#### {t('data_errors')}")
        for e in result.provider_errors:
            st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")
    if result.missing_fields:
        st.markdown(f"#### {t('missing_items')}")
        for m in result.missing_fields:
            st.write("- " + missing_text(m))
    st.info(result.disclaimer)
    st.download_button(t("download"), result.model_dump_json(indent=2), file_name=f"tickercase_{result.case_id}.json",
                       mime="application/json", key=f"dl_{key_suffix}")


TONE_ICON = {"good": "✔", "bad": "✖", "missing": "…", "neutral": "·"}
STEP_LABELS = {
    "sec_filings": ("SEC 申报列表", "SEC filings"), "sec_facts": ("SEC 财务数据", "SEC financials"), "price_history": ("日线行情", "Daily prices"),
    "options": ("期权链", "Option chain"), "risk_free_rate": ("美债利率", "Treasury yield"), "price_base_rate": ("本股历史涨幅", "Own price history"),
    "benchmarks": ("大盘对标", "Benchmarks"), "base_rate": ("同规模公司基准率", "Peer base rate"), "insiders": ("内部人交易", "Insider trades"),
    "prediction_markets": ("预测市场", "Prediction markets"), "fear_greed": ("恐惧贪婪指数", "Fear & Greed"),
}
TIER_TONE = {"lottery": "critical", "low": "warning", "possible": "neutral", "likely": "good", "unknown": "neutral"}


def step_chips(steps: dict) -> str:
    chips = []
    for key, state in steps.items():
        label = STEP_LABELS.get(key, (key, key))[0 if lang() == "zh" else 1]
        icon, color = ("✔", "#0ca30c") if state == "ok" else ("✖", "#d03b3b") if state == "failed" else ("…", "#898781")
        chips.append(f'<span style="display:inline-block;margin:2px 4px;padding:2px 10px;border-radius:999px;border:1px solid {color};font-size:0.82rem">'
                     f'<span style="color:{color}">{icon}</span> {label}</span>')
    return "".join(chips)


def pct_p(p) -> str:
    if p is None:
        return "—"
    return "<0.1%" if p < 0.001 else f"{p * 100:.1f}%"


def render_probability_card(result: CaseResult) -> None:
    o = result.oracle
    if o is None:
        return
    tone = TIER_TONE[o.tier]
    if o.low is None:
        title = t("prob_none")
    else:
        title = t("prob_title", lo=pct_p(o.low), hi=pct_p(o.high), tier=tr(o.tier_label))
    asof = result.verdict.as_of if result.verdict else result.created_at.date()
    sub = t("prob_sub", when=f"{o.target_date:%Y-%m}", target=format(o.target_price, "f"), asof=asof)
    st.markdown(f'<div class="tc-verdict tc-{tone}"><div class="tc-label">{title}</div><div class="tc-sub">{sub}</div></div>', unsafe_allow_html=True)
    if result.data_steps:
        ok = sum(1 for v in result.data_steps.values() if v == "ok")
        failed = sum(1 for v in result.data_steps.values() if v == "failed")
        st.markdown(f'<div style="margin:4px 0 8px 0">{step_chips(result.data_steps)}</div>', unsafe_allow_html=True)
        st.caption(t("steps_summary", ok=ok, failed=failed))


def render_methods_table(result: CaseResult) -> None:
    o = result.oracle
    if o is None:
        return
    i = 0 if lang() == "zh" else 1
    in_range = {"zh": ("是", "否", "—"), "en": ("yes", "no", "—")}["zh" if i == 0 else "en"]
    lo, hi = o.low, o.high
    rows = []
    for m in o.methods:
        used = m.status == "ok" and m.probability is not None and lo is not None and lo - 1e-12 <= m.probability <= hi + 1e-12 \
            and not (m.id == "M4" and any("重叠" in x.zh for x in m.limitations))
        rows.append((f"{m.id} {tr(m.name)}", pct_p(m.probability) if m.status == "ok" else "—", pct_p(m.touch_probability),
                     tr(m.measures), in_range[0] if used else in_range[1] if m.status == "ok" else in_range[2]))
    st.markdown(_md_table(S["m_cols"][i], rows))
    st.caption(tr(o.agreement))


def tab_oracle(result: CaseResult) -> None:
    o = result.oracle
    if o is None:
        st.caption(t("r_none"))
        return
    render_methods_table(result)
    if o.ladder:
        i = 0 if lang() == "zh" else 1
        st.markdown(f"#### {t('ladder_title')}")
        st.markdown(_md_table(S["ladder_cols"][i], [(f"{x.level:,}", tr(x.label), pct_p(x.options_p), pct_p(x.model_p)) for x in o.ladder]))
    st.markdown(f"#### {t('method_detail')}")
    for m in o.methods:
        with st.expander(f"{m.id} · {tr(m.name)} · {pct_p(m.probability) if m.status == 'ok' else '—'}"):
            st.markdown(tr(m.detail))
            if m.inputs:
                st.json(m.inputs, expanded=False)
            for lim in m.limitations:
                st.markdown(f"- {tr(lim)}")
    with st.container(border=True):
        st.markdown(f"**{t('why_trust')}**")
        st.markdown(t("why_trust_body"))


def tr(text: Optional[Text]) -> str:
    if text is None:
        return ""
    return text.zh if lang() == "zh" else text.en


def _cell(x: str) -> str:
    return (x or "").replace("|", "\\|").replace("\n", " ")


def _md_table(cols, rows) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    return head + "\n" + "\n".join("| " + " | ".join(_cell(c) for c in r) + " |" for r in rows)


NARR_BADGE = {"verified": "✔", "qualitative": "○", "cited_elsewhere": "⚠", "unsupported": "✖"}
NARR_TITLES = {"logic_chain": ("核心逻辑链", "Core logic"), "resonance": ("共振信号", "Signals that agree"), "divergences": ("关键分歧", "Key divergences"),
               "conclusion": ("结论", "Conclusion"), "upside": ("上行风险", "Upside risks"), "downside": ("下行风险", "Downside risks")}


def render_narrative(result: CaseResult, key_suffix: str) -> None:
    n = result.narrative
    st.markdown(f"#### {t('ai_title')}")
    if n is None or n.status != "ok":
        if n is not None:
            st.warning(t("ai_failed") + (n.error or n.status))
        st.button(t("ai_button") if n is None else t("ai_retry"), key=f"btn_narrate_{key_suffix}", help=t("ai_button_help"),
                  on_click=_narrate, args=(key_suffix,), type="primary")
        return
    zh = lang() == "zh"
    cost = n.usage.get("input_tokens", 0) * 4 / 1e6 + n.usage.get("output_tokens", 0) * 20 / 1e6
    st.caption(t("ai_summary", ok=n.verified, total=n.total, bad=n.unsupported, model=n.model, cost=cost))
    for key in ("logic_chain", "resonance", "divergences", "conclusion", "upside", "downside"):
        sentences = n.sections.get(key) or []
        if not sentences:
            continue
        st.markdown(f"**{NARR_TITLES[key][0 if zh else 1]}**")
        lines = []
        for x in sentences:
            refs = " ".join(x.fact_ids)
            text = x.zh if zh else x.en
            lines.append(f"- {NARR_BADGE[x.status]} {text} <span style='opacity:0.55;font-size:0.8rem'>[{refs}]</span>")
        st.markdown("\n".join(lines), unsafe_allow_html=True)
    st.caption(t("ai_legend"))
    with st.expander(t("ai_facts")):
        flagged = [(k, x) for k, v in n.sections.items() for x in v if x.problems]
        for k, x in flagged:
            st.markdown(f"- {NARR_BADGE[x.status]} **{NARR_TITLES[k][0 if zh else 1]}**：{'；'.join(x.problems)}")
        st.dataframe([{"id": f.id, "label": f.label_zh if zh else f.label_en, "value": f.value, "unit": f.unit, "source": f.source} for f in n.facts],
                     hide_index=True, use_container_width=True)
    st.button(t("ai_retry"), key=f"btn_narrate_{key_suffix}", on_click=_narrate, args=(key_suffix,), help=t("ai_button_help"))


def render_report(report: Optional[PlainReport]) -> None:
    if report is None:
        st.caption(t("r_none"))
        return
    i = 0 if lang() == "zh" else 1
    st.markdown(f"#### {t('r_data')}")
    for layer in report.layers:
        st.markdown(f"**{tr(layer.title)}**")
        st.markdown(_md_table(S["r_cols"][i], [(TONE_ICON[r.tone], tr(r.signal), tr(r.data), tr(r.meaning)) for r in layer.rows]))
    st.markdown(f"#### {t('r_analysis')}")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown(f"**{t('r_agree')}**")
        for x in report.agreements:
            st.markdown(f"- {tr(x)}")
    with c2.container(border=True):
        st.markdown(f"**{t('r_diverge')}**")
        for x in report.divergences:
            st.markdown(f"- {tr(x)}")
    if report.time_view:
        st.markdown(f"**{t('r_time')}**")
        st.markdown(_md_table(S["r_time_cols"][i], [(tr(r.signal), tr(r.data), tr(r.meaning)) for r in report.time_view]))
    if report.scenarios:
        st.markdown(f"#### {t('r_scen')}")
        st.markdown(_md_table(S["r_scen_cols"][i], [(tr(x.name), tr(x.assumptions), x.price or "—", x.vs_target or "—", tr(x.meaning))
                                                    for x in report.scenarios]))
        st.caption(tr(report.scenario_note))
    st.markdown(f"#### {t('r_concl')}")
    st.markdown(tr(report.verdict_meaning))
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown(f"**✔ {t('r_up')}**")
        for x in report.upside:
            st.markdown(f"- {tr(x)}")
    with c2.container(border=True):
        st.markdown(f"**✖ {t('r_down')}**")
        for x in report.downside:
            st.markdown(f"- {tr(x)}")
    if report.monitor:
        st.markdown(f"**{t('r_monitor')}**")
        st.markdown(_md_table(S["r_mon_cols"][i], [(tr(m.signal), tr(m.current), tr(m.threshold), tr(m.meaning)) for m in report.monitor]))


def render_result(result: CaseResult, key_suffix: str = "current") -> None:
    if result.validation_issues:
        for i in result.validation_issues:
            st.error(issue_text(i))
        return
    if result.confirmed_claim is None:
        return
    if result.oracle is not None:
        render_probability_card(result)
    else:
        render_verdict(result)
    if result.report is not None:
        st.markdown(f"<div style='font-size:1.08rem; line-height:1.6; margin: 0 0 8px 0'>{tr(result.report.headline)}</div>", unsafe_allow_html=True)
    for e in result.provider_errors:
        st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")
    warnings = result.warnings_zh if lang() == "zh" and len(result.warnings_zh) == len(result.warnings) else result.warnings
    if warnings:
        with st.expander(t("notes", n=len(warnings)), expanded=bool(result.provider_errors)):
            for w in warnings:
                st.warning(w)
    tabs = st.tabs(S["tabs"][0 if lang() == "zh" else 1])
    with tabs[0]:
        if result.oracle is not None:
            render_methods_table(result)
            render_narrative(result, key_suffix)
        render_report(result.report)
    with tabs[1]:
        tab_oracle(result)
    with tabs[2]:
        if result.oracle is not None:
            render_verdict(result)
        render_key_numbers(result)
        if result.verdict is not None:
            tab_conclusion(result)
    with tabs[3]:
        tab_evidence(result)
    with tabs[4]:
        tab_calculations(result)
    with tabs[5]:
        tab_public_data(result)
    with tabs[6]:
        tab_probability(result)
    with tabs[7]:
        tab_run_log(result, key_suffix)


# ------------------------------------------------------------------ views


def sidebar() -> None:
    with st.sidebar:
        st.markdown("## TickerCase")
        st.radio("语言 / Language", options=["zh", "en"], format_func=lambda x: "中文" if x == "zh" else "English", key="lang", horizontal=True)
        st.caption(t("tagline"))
        # radio labels are the same in both languages so switching language keeps the selection
        st.radio(t("view"), options=["new", "history"], format_func=VIEW_LABELS.get, key="view", horizontal=True)
        st.radio(t("mode"), options=list(MODE_LABELS), key="sec_mode", horizontal=True, help=t("mode_help"))
        st.caption(MODE_LABELS[st.session_state["sec_mode"]][0 if lang() == "zh" else 1])
        if st.session_state["sec_mode"] in ("live", "record"):
            if validate_user_agent(get_service().settings.sec_user_agent):
                with st.container(border=True):
                    st.markdown(f"**{t('sec_setup')}**")
                    st.caption(t("sec_missing"))
                    st.text_input(t("sec_email"), key="sec_email_input", placeholder="you@example.org")
                    st.button(t("sec_save"), key="btn_sec_save", on_click=_save_sec_contact)
            else:
                st.caption("✔ " + t("sec_ok"))
            msg = st.session_state["sec_msg"]
            if msg:
                (st.success if msg[0] == "success" else st.error)(msg[1][0 if lang() == "zh" else 1])
        with st.container(border=True):
            st.markdown(f"**{t('ai_setup')}**")
            if get_service().settings.anthropic_api_key:
                st.caption("✔ " + t("ai_ok"))
            else:
                st.caption(t("ai_missing"))
                st.text_input(t("ai_key"), key="ai_key_input", type="password", placeholder="sk-ant-...")
                st.button(t("ai_save"), key="btn_ai_save", on_click=_save_ai_key)
            msg = st.session_state.get("ai_msg")
            if msg:
                (st.success if msg[0] == "success" else st.error)(msg[1][0 if lang() == "zh" else 1])
        st.markdown(f"**{t('examples')}**")
        st.button(t("ex_ps"), on_click=_load_example, args=("ps",), key="btn_example_ps", use_container_width=True)
        st.button(t("ex_pe"), on_click=_load_example, args=("pe",), key="btn_example_pe", use_container_width=True)
        st.button(t("clear"), on_click=_clear_form, key="btn_clear", use_container_width=True)
        with st.expander(t("verdict_kinds")):
            st.markdown(t("verdict_kinds_body"))
        st.caption(t("no_advice"))


def view_history() -> None:
    st.title(t("view_history"))
    store = get_service().store
    cases = store.list_cases() if store is not None else []
    if not cases:
        st.info(t("history_empty"))
        return
    zh = lang() == "zh"

    def verdict_text(c: CaseResult) -> str:
        if c.verdict is None:
            return "—"
        return (c.verdict.display_zh or VERDICT_DISPLAY_ZH[c.verdict.label]) if zh else c.verdict.display

    rows = []
    for c in cases:
        v = c.confirmed_claim.values if c.confirmed_claim else None
        rows.append({
            ("时间" if zh else "Time"): c.created_at.strftime("%Y-%m-%d %H:%M"),
            ("代码" if zh else "Ticker"): v.ticker if v else "—",
            ("观点" if zh else "Claim"): v.claim_text if v else "—",
            ("结论" if zh else "Verdict"): verdict_text(c),
            ("数据模式" if zh else "Data mode"): c.data_modes.get("sec_filings", "—"),
            ("状态" if zh else "Status"): STATUS[c.status.value][0 if zh else 1],
        })
    st.caption(t("history_hint"))
    event = st.dataframe(rows, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="multi-row", key="history_table")
    selected = [cases[i] for i in (event.selection.rows if event and event.selection else [])]
    if len(selected) == 1:
        st.session_state["history_selected"] = selected[0]
        render_result(selected[0], key_suffix=selected[0].case_id)
    elif len(selected) >= 2:
        st.markdown(f"### {t('compare')}")
        table = {}
        for c in selected:
            v = c.confirmed_claim.values if c.confirmed_claim else None
            calc = {x.name: x for x in c.calculations}
            e1 = next((i for i in c.evidence_items if i.id == "E1"), None)
            col = f"{c.created_at:%m-%d %H:%M} {v.ticker if v else ''}"
            req_metric = calc.get("required_annual_revenue") or calc.get("required_annual_net_income")
            table[col] = {
                ("观点" if zh else "Claim"): v.claim_text if v else "—",
                ("目标价" if zh else "Target"): format(v.target_price, "f") if v else "—",
                ("参考价" if zh else "Reference"): f"{format(v.reference_price, 'f')} ({v.reference_price_date})" if v else "—",
                ("时间范围" if zh else "Horizon"): format(v.horizon_years, "f") if v else "—",
                ("估值" if zh else "Valuation"): f"{'P/S' if v.valuation_method.value == 'price_to_sales' else 'P/E'} {format(v.valuation_multiple, 'f')}" if v else "—",
                ("目标期股份数" if zh else "Target shares"): fmt_amount(v.target_assumed_shares) if v else "—",
                ("所需年指标" if zh else "Required annual metric"): fmt_calc(req_metric) if req_metric else "—",
                ("所需增速" if zh else "Required growth"): fmt_pct(Decimal(e1.measured["required_cagr_from_reported"])) if e1 and "required_cagr_from_reported" in e1.measured else "—",
                ("已披露增速" if zh else "Reported growth"): fmt_pct(Decimal(e1.measured["reported_cagr"])) if e1 and "reported_cagr" in e1.measured else "—",
                ("结论" if zh else "Verdict"): verdict_text(c),
                ("证据截至" if zh else "Evidence as of"): str(c.verdict.as_of) if c.verdict else "—",
                ("数据模式" if zh else "Data mode"): c.data_modes.get("sec_filings", "—"),
            }
        st.dataframe(pd.DataFrame(table), use_container_width=True)


def view_new() -> None:
    draft = current_draft()
    confirmation: Optional[Confirmation] = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    result: Optional[CaseResult] = st.session_state["result"]
    result_current = (result is not None and result.input_fingerprint == fingerprint(draft)
                      and st.session_state["result_mode"] == st.session_state["sec_mode"])
    validation = validate_draft(draft, today=get_service().today())
    claim_ok = all(st.session_state[f"f_{n}"].strip() for n in CLAIM_FIELDS)

    st.title("TickerCase")
    stepper(claim_ok, validation.ok, state, result_current)

    if result_current:
        v = result.confirmed_claim.values
        st.caption(f"**{v.ticker}** · {v.claim_text} · {('目标价' if lang() == 'zh' else 'target')} {v.target_price} · "
                   f"{('目标期股数' if lang() == 'zh' else 'target shares')} {fmt_amount(v.target_assumed_shares)} · "
                   f"{v.horizon_years} {('年' if lang() == 'zh' else 'y')} · "
                   f"{'P/S' if v.valuation_method.value == 'price_to_sales' else 'P/E'} {v.valuation_multiple}")
        holder = st.expander(t("edit_inputs"), expanded=False)
    else:
        holder = st.container()
    with holder:
        render_claim_box()
        rest_complete = all(not m.blocking or m.field in CLAIM_FIELDS for m in validation.missing_fields) and not validation.issues
        render_rest(expanded=not rest_complete or bool(st.session_state["prefill_sources"]))

    draft = current_draft()  # widgets above may have changed the values
    validation = validate_draft(draft, today=get_service().today())
    if not result_current:
        for issue in validation.issues:
            st.error(issue_text(issue))
        blocking = [m for m in validation.missing_fields if m.blocking]
        if blocking:
            st.warning(t("missing_core") + ("、" if lang() == "zh" else ", ").join(label_of(m.field) for m in blocking))
        optional = [m for m in validation.missing_fields if not m.blocking]
        if optional:
            st.caption(t("optional_missing") + ("；" if lang() == "zh" else "; ").join(missing_text(m) for m in optional))
        for w in (validation.warnings_zh if lang() == "zh" else validation.warnings):
            st.caption("⚠ " + w)

    st.markdown(f"### {t('s3')}")
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button(t("confirm"), key="btn_confirm", use_container_width=True):
        try:
            st.session_state["confirmation"] = confirm(draft, now=get_service().now, today=get_service().today())
            st.session_state["confirm_feedback"] = None
        except ConfirmationError:
            st.session_state["confirmation"] = None
            st.session_state["confirm_feedback"] = "invalid"
    confirmation = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    run_clicked = c2.button(t("run"), key="btn_run", type="primary", disabled=state != "confirmed", use_container_width=True)
    with c3:
        if st.session_state["confirm_feedback"]:
            st.error(t("invalid_confirm"))
        if state == "confirmed":
            st.success(t("confirmed", t=f"{confirmation.confirmed_at:%Y-%m-%d %H:%M:%S}"))
        elif state == "stale":
            st.warning(t("stale"))
        else:
            st.info(t("unconfirmed"))

    if run_clicked:
        st.session_state["result"] = None
        st.session_state["run_error"] = None
        with st.status(t("pipeline"), expanded=True) as status:
            board = st.empty()
            live_steps: dict[str, str] = {}

            def on_step(step: str, state: str) -> None:
                live_steps[step] = state
                board.markdown(step_chips(live_steps), unsafe_allow_html=True)

            try:
                st.session_state["result"] = get_service().evaluate(draft, confirmation, sec_mode=st.session_state["sec_mode"], progress=on_step)
                status.update(state="complete")
                st.session_state["result_mode"] = st.session_state["sec_mode"]
            except Exception as exc:  # unexpected failure; keep page usable and show it
                st.session_state["run_error"] = f"{type(exc).__name__}: {exc}"
        st.rerun()  # redraw with the input form collapsed above the new result

    if st.session_state["run_error"]:
        st.error(t("run_failed") + st.session_state["run_error"])
    result = st.session_state["result"]
    if result is not None:
        if result.input_fingerprint != fingerprint(draft) or st.session_state["result_mode"] != st.session_state["sec_mode"]:
            st.warning(t("hidden"))
        else:
            st.markdown(f"### {t('case')}")
            render_result(result)


def main() -> None:
    st.set_page_config(page_title="TickerCase", page_icon="📈", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    _init_state()
    sidebar()
    if st.session_state["view"] == "history":
        view_history()
    else:
        view_new()


main()
