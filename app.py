"""TickerCase Streamlit page: one claim sentence -> public data -> probability report with layered evidence.

Run: streamlit run app.py
One script, two pages laid out as a console (v0.9): the console page (write the claim, check and confirm
every input, see which data and methods are used) and the report page (status bar, claim, K-line,
probability, data readings, report channels, signals, narrative). The page is either Chinese or English;
the switch is on the bridge at the top. The look lives in tickercase/ui.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

from tickercase.config import REPO_ROOT, load_settings, write_env_value
from tickercase.http_client import validate_user_agent
from tickercase.models import VERDICT_DISPLAY_ZH, CaseResult, ClaimDraft, Confirmation, PlainReport, ReferenceSnapshot, Text
from tickercase.narrative import estimate_cost
from tickercase.oracle import event_move, p_end_above, p_touch, scenario
from tickercase.providers.options import iv_at
from tickercase.service import CLAIM_TEXT_PREFIX, DEFAULT_PREFIX, CaseService
from tickercase.storage import CaseStore
from tickercase.ui import cockpit as ck
from tickercase.ui.kline import kline_html
from tickercase.validation import ConfirmationError, confirm, confirmation_state, fingerprint, validate_draft

# ------------------------------------------------------------------ text (zh, en)

S = {
    "language": ("语言", "Language"),
    "view": ("页面", "Page"), "view_new": ("CASE 观点", "CASE"), "view_history": ("HIST 历史", "HIST"),
    "tube": ("荧光颜色", "Phosphor"),
    "back_new": ("◀ NEW 新观点", "◀ NEW claim"),
    "data_chip": ("数据源", "DATA"),
    "mode": ("DATA · 数据源模式", "DATA · data source"),
    "mode_help": ("实时数据会请求 SEC、行情和期权接口；合成示例使用虚构公司 SYNT 的数据。", "Live calls SEC, quote and option sources; the synthetic example uses the fictional company SYNT."),
    "ex_ps": ("EX-1 合成示例 P/S", "EX-1 · synthetic P/S"), "ex_pe": ("EX-2 合成示例 P/E", "EX-2 · synthetic P/E"),
    "clear": ("CLR 清除", "CLR · clear"),
    "sec_setup": ("SEC · 联系邮箱", "SEC · contact email"),
    "sec_missing": ("实时读取 SEC 数据需要一个联系邮箱（SEC 规定，只发送给 SEC），保存在本机 .env。设置之前，READ 和 RUN 只能用回放或示例数据。",
                    "Live SEC requests need a contact email (SEC rule; sent only to SEC). It is saved to the local .env file. Until then READ and RUN can only use replay or example data."),
    "sec_email": ("你的邮箱", "Your email"), "sec_save": ("SAVE 保存", "SAVE"),
    "sec_saved": ("已保存。", "Saved."), "sec_ok": ("SEC 联系邮箱已设置", "SEC contact email is set"),
    "sec_bad": ("请输入有效邮箱。", "Enter a valid email."),
    "ai_setup": ("NAR · AI 叙述", "NAR · AI narrative"),
    "ai_optional": ("可选。把事实表写成文字，数字逐一核对；调用 Claude，按用量计费。", "Optional. Writes the fact table up as text and checks every number; one Claude call, billed by usage."),
    "ai_missing": ("填写 Anthropic API key 后可生成 AI 叙述。key 只保存在本机 .env。", "Enter an Anthropic API key to write the AI narrative. It is stored only in the local .env."),
    "ai_ok": ("Claude API key 已设置", "Claude API key is set"),
    "ai_key": ("API key", "API key"), "ai_save": ("SAVE 保存 key", "SAVE key"),
    "ai_auto": ("RUN 后自动生成叙述", "Write the narrative after each RUN"),
    "ai_auto_help": ("开启后，每次生成报告都会写一次当前语言的叙述（相同事实直接复用，不再调用）。默认关闭。",
                     "When on, each report also gets a narrative in the current language (the same facts are reused without a call). Off by default."),
    "no_advice": ("不执行交易，不构成投资建议。", "No trading. Not investment advice."),
    # console: claim input
    "u_clm": ("UNIT 01 · CLM 观点输入", "UNIT 01 · CLM claim input"),
    "clm_flow": ("READ 识别 → UNIT 02 核对确认 → RUN 生成报告", "READ → UNIT 02 check and confirm → RUN"),
    "prefill": ("READ 识别并补全 ↵", "READ · read and fill in ↵"),
    "prefill_help": ("从句子里读出代码、目标价、时间和条件，再用公开数据补全价格、股本和财务数据。改了句子就再按一次。",
                     "Reads ticker, target, time and condition from the sentence, then fills price, shares and financials from public data. Press again after editing the sentence."),
    "need_text": ("先写一句观点。", "Write the claim first."),
    "need_ticker": ("先填写股票代码。", "Enter a ticker first."),
    "recognized": ("从原文识别：{items}", "Read from the claim: {items}"),
    "recognized_none": ("没有从原文识别出代码、目标价或时间。", "Nothing could be read from the claim."),
    "multiple_target": ("目标价 = 参考价 {ref} × {x}（原文「{text}」）", "Target = reference {ref} × {x} (from “{text}”)"),
    "condition": ("怎样算实现", "What counts as coming true"),
    "condition_help": ("期间触及：截止日前任何一天达到目标价就算，适合「冲到」「脉冲」这类说法。到期站上：截止日当天仍在目标价以上。",
                       "Touch: reaching the target on any day before the deadline counts, as in “spike to”. On the date: still at or above the target on the deadline."),
    "cond_touch": ("期间任意时点触及", "Touch at any time"), "cond_end": ("到期时站在上面", "At or above on the date"),
    "filled": ("已补全 {n} 项。", "Filled {n} fields."),
    "defaults_used": ("默认假设：{items}。可在下方修改。", "Default assumptions: {items}. You can change them below."),
    "not_fetched": ("未取到：{errors}", "Not fetched: {errors}"),
    "nothing_fetched": ("没有取到公开数据。{errors}", "No public data fetched. {errors}"),
    # console: check and confirm
    "u_cnf": ("UNIT 02 · CNF 核对与确认", "UNIT 02 · CNF check and confirm"),
    "cnf_stby": ("按 READ 后，这里列出读到的每一项输入和它的来源：✎ 原文、ⓘ 公开数据、◇ 默认假设。核对无误后按 ARM 确认，RUN 才会亮；之后改动任何一项，都需要重新确认。",
                 "After READ, every input and its source is listed here: ✎ claim, ⓘ public data, ◇ default assumption. Check them and press ARM to confirm; only then does RUN light up. Any later edit needs a new confirmation."),
    "cnf_hint": ("核对每一项和它的来源，尤其是 ◇ 默认假设。无误后按 ARM 确认，RUN 才会亮。", "Check every input and its source, especially ◇ defaults. Press ARM when they are right; then RUN lights up."),
    "marks": ("✎ 原文　ⓘ 公开数据　◇ 默认假设　· 你填的", "✎ claim　ⓘ public data　◇ default assumption　· typed by you"),
    "g_claim": (("观点", "从句子里读出来的几项，最容易读错，请先看这里"), ("Claim", "Read from the sentence; the easiest to misread, so check these first")),
    "g_price": (("价格与股本", "公开数据，带来源与时间"), ("Price and shares", "Public data, with source and time")),
    "g_val": (("估值", "用哪种倍数、多少倍来估目标日的公司价值"), ("Valuation", "Which multiple, and how high, values the company at the target date")),
    "g_extra": (("申报范围与附加项", "可选；漂移率和波动率只用于「原始数据」里的旧版概率参考"), ("Filing window and extras", "Optional; drift and volatility only feed the older model reference under Raw data")),
    "you_changed": ("你修改了这一项 · 原值 {v}", "you changed this · was {v}"),
    "method": ("估值方法", "Valuation method"), "method_help": ("假设：目标日期用哪种倍数估值。", "Assumption: which multiple values the company at the target date."),
    "m_ps": ("市销率 P/S", "Price-to-sales (P/S)"), "m_pe": ("市盈率 P/E", "Price-to-earnings (P/E)"),
    "share_mode": ("目标期股份数怎么定", "Target-date share count"),
    "share_mode_help": ("大多数人无法直接估计几年后的股数。默认按 SEC 披露的近年趋势推算（已排除拆股）；也可以选择不变、自定义年变化率，或直接填目标股数。",
                        "Few people can estimate a future share count. The default extends the recent SEC-reported trend (splits excluded); you can also keep it flat, set a yearly %, or enter a count."),
    "sm_trend": ("按近年趋势", "Recent trend"), "sm_flat": ("保持不变", "Flat"), "sm_rate": ("自定义年变化率", "Custom yearly %"), "sm_absolute": ("直接填股数", "Enter a count"),
    "shares_result": ("目标期股份数 ≈ {n} 股（当前 {c} × (1 {sign} {r}%)^{y} 年）", "Target-date shares ≈ {n} (current {c} × (1 {sign} {r}%)^{y} years)"),
    "shares_need_current": ("需要当前股份数才能推算目标期股份数。", "Current shares are needed to work out the target-date count."),
    "flat_note": ("每年变化 0%：目标期股份数等于当前股份数。", "0% a year: target-date shares equal current shares."),
    "prob_note": ("漂移率和波动率只用于「原始数据」里的旧版概率参考，不影响首页概率。", "Drift and volatility only feed the older model reference under “Raw data”; the headline probability does not use them."),
    "checks": ("检查结果 · 核心字段缺失或无效时不能确认", "Checks · inputs cannot be confirmed while core fields are missing or invalid"),
    "checks_ok": ("核心字段齐全，没有无效输入。", "Core fields are complete; no invalid input."),
    "missing_core": ("还缺：", "Still missing: "), "optional_missing": ("可选项未填：", "Optional inputs empty: "),
    "confirm": ("ARM 确认输入", "ARM · confirm inputs"), "run": ("RUN 生成报告 ↵", "RUN · build report ↵"),
    "confirmed": ("已确认（{t} UTC）。修改任何输入都需要重新确认。", "Confirmed ({t} UTC). Any edit needs a new confirmation."),
    "stale": ("确认后输入已修改，原确认失效，请重新确认。", "Inputs changed after confirmation; confirm again."),
    "unconfirmed": ("尚未确认。请核对输入，尤其是标 ◇ 的默认假设，然后按 ARM。", "Not confirmed yet. Check the inputs, especially defaults marked ◇, then press ARM."),
    "invalid_confirm": ("输入无效或缺少核心字段，未确认。", "Inputs are invalid or incomplete; not confirmed."),
    "run_failed": ("运行失败：", "Run failed: "),
    "run_kept": ("输入和确认都保留着，重试不需要重新填写。", "Your inputs and confirmation are kept; retrying needs no re-entry."),
    "hidden": ("已有报告对应修改前的输入或数据来源，已隐藏。请重新确认并生成。旧报告仍在 HIST 里。",
               "The previous report belongs to older inputs or another data source and is hidden. Confirm and build again; the old report stays in HIST."),
    "open_report": ("▶ 打开当前报告", "▶ Open the current report"),
    "ready": ("这组输入已经生成过报告。", "These inputs already have a report."),
    # console: data and methods used, recent tapes
    "u_feed": ("UNIT 03 · FEED 采用的信息 · 8 CH", "UNIT 03 · FEED data we use · 8 CH"),
    "u_mth": ("UNIT 04 · MTH 四种方法 · 互相核对", "UNIT 04 · MTH four methods · cross-checked"),
    "u_hist": ("UNIT 05 · HIST 最近的磁带", "UNIT 05 · HIST recent tapes"),
    "hist_hint": ("▶ PLAY 直接打开这盘磁带的报告", "▶ PLAY opens that tape's report"),
    "play": ("▶ PLAY", "▶ PLAY"), "hist_all": ("全部历史 ▶", "All history ▶"),
    # report page
    "u_clm_r": ("UNIT 01 · CLM 观点", "UNIT 01 · CLM claim"),
    "u_kln": ("UNIT 02 · KLN K 线 · {tk}", "UNIT 02 · KLN K-line · {tk}"),
    "kln_src": ("YAHOO · 截至 {d} 收盘 · 与案例同一份数据", "YAHOO · to the {d} close · the same data as the case"),
    "kln_none": ("这份报告没有行情数据，画不出 K 线。", "This report has no price data, so there is no K-line."),
    "u_prb": ("UNIT 03 · PRB 概率 ← MTH 四种方法", "UNIT 03 · PRB probability ← MTH four methods"),
    "u_feed_r": ("UNIT 04 · FEED 数据源读数 · 8 CH", "UNIT 04 · FEED source readings · 8 CH"),
    "u_rpt": ("UNIT 05 · RPT 报告 · 5 CH", "UNIT 05 · RPT report · 5 CH"),
    "rpt_note": ("换台雪花 0.5 秒 · 内容随即出现", "0.5 s of snow per channel, then the content"),
    "tabs": (["CH1 报告", "CH2 情景推演", "CH3 价位与方法", "CH4 基本面核查", "CH5 原始数据"], ["CH1 Report", "CH2 Scenario", "CH3 Levels & methods", "CH4 Fundamentals", "CH5 Raw data"]),
    "u_sig": ("UNIT 06 · SIG 信号", "UNIT 06 · SIG signals"),
    "u_fnd": ("FND · 基本面结论", "FND · FUNDAMENTALS"), "fnd_more": ("CH4 详情 ▲", "CH4 details ▲"),
    "u_nar": ("UNIT 07 · NAR 叙述 · DOT-MATRIX", "UNIT 07 · NAR narrative · DOT-MATRIX"),
    "prob_none": ("数据不足，无法计算概率", "Not enough data to compute a probability"),
    "target": ("目标价", "Target"), "now_price": ("现价", "Now"),
    "r_layers": ("数据分层", "Evidence by layer"), "r_analysis": ("交叉验证", "Cross-check"),
    "r_agree": ("一致的信号", "Signals that agree"), "r_diverge": ("关键分歧", "Key divergences"),
    "r_time": ("时间维度", "Time view"), "r_scen": ("情景推演", "Scenarios"),
    "r_concl": ("结论", "Conclusion"), "r_up": ("什么会让判断变好", "What would strengthen the case"),
    "r_down": ("什么会让判断变差", "What would weaken the case"), "r_monitor": ("需要关注的信号", "Signals to watch"),
    "r_cols": (("", "信号", "数据", "说明了什么"), ("", "Signal", "Data", "What it says")),
    "r_scen_cols": (("情景", "假设", "目标日股价", "相对目标价", "含义"), ("Scenario", "Assumptions", "Price at target date", "vs target", "Meaning")),
    "r_mon_cols": (("信号", "当前", "触发条件", "含义"), ("Signal", "Now", "Trigger", "Meaning")),
    "r_time_cols": (("时间", "要看什么", "说明"), ("When", "What", "Note")),
    "r_none": ("这份报告没有分层内容（由旧版本生成）。", "This report has no layers (made by an older version)."),
    # AI narrative
    "ai_intro": ("由 Claude 根据本报告的事实表撰写，每句话的数字都会逐个核对出处。", "Written by Claude from this report's fact table; every number in every sentence is checked against its source."),
    "ai_button": ("PRINT 生成 AI 叙述", "PRINT · write AI narrative"),
    "ai_button_help": ("只写当前语言。同样的事实已经写过时直接复用，不再调用；否则调用一次 Claude（{model}，effort {effort}），按用量计费。",
                       "Writes the current language only. Reuses a narrative already written for the same facts; otherwise one Claude call ({model}, effort {effort}), billed by usage."),
    "ai_retry_help": ("忽略已有结果，重新调用一次 Claude（{model}，effort {effort}），按用量计费。", "Ignores the saved narrative and makes a new Claude call ({model}, effort {effort}), billed by usage."),
    "ai_summary": ("{ok}/{total} 句通过核对 · {bad} 句含无出处数字 · {model}", "{ok}/{total} sentences pass · {bad} with unsourced numbers · {model}"),
    "ai_usage": ("输入 {inp:,} · 输出 {out:,} token · 约 ${cost:.3f}", "{inp:,} input · {out:,} output tokens · about ${cost:.3f}"),
    "ai_reused": ("复用案例 {case} 中相同事实的叙述，这次没有调用 API", "Reused from case {case} with the same facts; no API call this time"),
    "ai_failed": ("AI 叙述未生成：", "AI narrative not written: "),
    "ai_facts": ("事实表与核对明细", "Fact table and check details"),
    "ai_legend": ("核 数字与引用的事实一致 · ○ 无数字的解释 · ⚠ 数字存在但引用了别的事实 · ✖ 数字找不到出处",
                  "OK numbers match the cited facts · ○ no numbers · ⚠ number exists but another fact is cited · ✖ number has no source"),
    "ai_retry": ("RETRY 重新生成", "RETRY · write again"),
    # scenario
    "sc_intro": ("如果你认为某个事件（发射、财报、审批）会让股价跳一下，在这里把它写成情景：事件日期、成功的可能性、成功或失败时股价大概怎么动。结果随滑块即时更新，不会重新拉数据。",
                 "If you think one event (a launch, earnings, an approval) will move the stock, describe it here: the date, how likely success is, and how the price moves either way. Results update as you drag; nothing is fetched again."),
    "sc_event": ("事件", "Event"), "sc_event_ph": ("例如：Neutron 首飞", "e.g. first Neutron launch"),
    "sc_date": ("预计日期", "Expected date"),
    "sc_market": ("期权市场怎么看这个日期", "What the option market prices for this date"),
    "sc_move_ok": ("期权价格里为 {d} 前后额外留出了约 **±{m}** 的波动（一个标准差），依据 {b} 和 {a} 两个到期日的隐含波动率。同一段时间里的财报等消息也会算在里面。",
                   "Option prices set aside about **±{m}** of extra movement around {d} (one standard deviation), from the implied volatility of the {b} and {a} expiries. Earnings and other news in the same stretch are included too."),
    "sc_move_flat": ("期权价格没有为 {d} 前后额外加价波动：市场目前不认为这个日期特别。", "Option prices add no extra volatility around {d}: the market does not treat the date as special right now."),
    "sc_move_na": ("算不出来：", "Not computable: "),
    "sc_use_market": ("用市场幅度填入", "Use the market's size"),
    "sc_no_term": ("本报告没有期权期限结构数据（旧版本生成或期权不可用），无法读出市场对事件的定价。", "This report has no option term structure (older version or no options), so the market's event pricing is unavailable."),
    "sc_yours": ("你的情景", "Your scenario"),
    "sc_p": ("事件成功的可能性", "Chance the event succeeds"),
    "sc_up": ("成功时股价变化", "Price move on success"), "sc_down": ("失败时股价变化", "Price move on failure"),
    "sc_vol": ("日常波动率（年化）", "Ordinary volatility (yearly)"),
    "sc_vol_help": ("事件以外的日常波动。默认取历史波动率；期权隐含波动率已经包含事件，用它会重复计算。", "Day-to-day volatility outside the event. Defaults to historical volatility; implied volatility already contains the event and would count it twice."),
    "sc_result": ("按你的情景", "Under your scenario"), "sc_if_up": ("如果成功", "If it succeeds"), "sc_if_down": ("如果失败", "If it fails"),
    "sc_market_p": ("期权市场（M1）", "Option market (M1)"),
    "sc_break": ("要让概率达到 50%，你需要相信事件成功的可能性至少是 **{p}**。", "For a 50% chance you would need to believe the event succeeds at least **{p}** of the time."),
    "sc_break_zero": ("即使事件失败，概率也已超过 50%。", "Even if the event fails the chance is already above 50%."),
    "sc_break_none": ("即使事件一定成功，按这个幅度概率也只有 **{p}**，到不了 50%。", "Even if the event certainly succeeds, this size gives only **{p}**, short of 50%."),
    "sc_implied": ("按你填的涨跌幅，现在的期权价格相当于市场认为成功的可能性约 **{p}**。你填的是 {u}。", "With your move sizes, today's option prices amount to the market giving the event about **{p}**. You entered {u}."),
    "sc_note": ("算法：普通波动之外，在事件日期加一次跳动。「到期站上」是精确解；「期间触及」把事件当天的价格近似为今天的价格。用的是无风险利率作漂移，与期权方法一致。",
                "Method: ordinary volatility plus one jump on the event date. Exact for “on the date”; for “touch” the price on the event day is approximated by today's. The drift is the risk-free rate, as in the option method."),
    "sc_need": ("这份报告缺少现价或波动率，无法推演情景。", "This report lacks a current price or volatility, so no scenario can be computed."),
    # levels and methods
    "lv_title": ("不同价位的概率", "Probability by price level"),
    "lv_note": ("曲线用期权隐含波动率（无期权时用历史波动率）按价位逐个计算。", "Each level uses the option-implied volatility at that strike (historical volatility when there are no options)."),
    "lv_end": ("到期时 ≥ 该价", "On the date ≥ level"), "lv_touch": ("期间曾触及", "Touched before"),
    "lv_level": ("价位", "Level"), "lv_prob": ("概率", "Probability"),
    "m_title": ("四种方法", "The four methods"),
    "m_cols": (("方法", "到期 ≥ 目标", "期间触及", "衡量的是什么", "计入区间"), ("Method", "End ≥ target", "Touch", "What it measures", "In range")),
    "m_yes": ("是", "yes"), "m_no": ("参考", "context"),
    "method_detail": ("每种方法的算法、输入和限制", "How each method works, its inputs and limits"),
    "why_trust": ("为什么比直接问 AI 更可信", "Why this is more reliable than asking an AI directly"),
    "why_trust_body": (
        "- 每个数字都是本次实时抓取或计算出来的，带来源链接和时间；不靠模型记忆。\n"
        "- 概率由几种独立方法分别计算并并排展示，分歧会被说明。\n"
        "- 同样的输入得到同样的结果，可以用回放模式复现。\n"
        "- 取不到的数据会明确列出，不会被编造补齐。\n"
        "- 内部人交易按类型区分，授予、行权、代扣税不算作卖出。",
        "- Every number was fetched or computed in this run, with a source link and time; nothing comes from model memory.\n"
        "- Several independent methods are computed and shown side by side, and disagreements are explained.\n"
        "- The same inputs give the same result, replayable in replay mode.\n"
        "- Missing data is listed, never filled in.\n"
        "- Insider trades are separated by type: grants, exercises and tax withholding are not counted as selling."),
    # fundamentals
    "f_intro": ("这一页检查业绩能否撑起目标价：按你确认的估值假设，算出目标价需要多少营收或利润，再和已披露的数据比较（课程文档 OA.13/OA.14 的结论与复查条件）。",
                "This page checks whether the business can carry the target: under your valuation assumptions it works out the revenue or profit the target needs and compares it with reported data (the course's OA.13/OA.14 verdict and rechecks)."),
    "as_of": ("证据截至 {d} · {r}", "Evidence as of {d} · {r}"),
    "rationale": ("判断依据", "Rationale"), "rechecks": ("何时需要重新评估", "When to review again"),
    "watch": ("观察：", "Watch: "), "threshold": ("阈值：", "Threshold: "), "linked": ("关联：", "Linked to: "),
    "limits": ("这个结论的限制", "Limits of this verdict"),
    "evidence": ("证据", "Evidence"), "rule": ("规则：", "Rule: "), "sources": ("来源与数值", "Sources and values"),
    "calcs": ("计算", "Calculations"),
    "calc_note": ("全部基于你确认的输入与假设，用 Decimal 精确计算，不依赖网络数据。", "Based only on your confirmed inputs and assumptions; exact Decimal arithmetic, no network data."),
    "provenance": ("输入值来源", "Where each input came from"),
    "sens": ("敏感性：不同估值倍数和时间下所需的年增速", "Sensitivity: required yearly growth for other multiples and horizons"),
    "sens_note": ("行是估值倍数，列是时间（年）。基数：{b}。对比已披露增速 {h}：✔ 不高于，◐ 高出不超过 5 个百分点，✖ 高出更多。加粗为你的假设。",
                  "Rows are multiples, columns are horizons (years). Base: {b}. Compared with reported growth {h}: ✔ at or below, ◐ up to 5 pp above, ✖ more. Bold marks your assumption."),
    "sens_none": ("没有可用的基期数值，无法计算敏感性。", "No base value available, so no sensitivity table."),
    "verdict_kinds": ("结论的四种结果", "The four verdicts"),
    "verdict_kinds_body": (
        "- **✔ 目前证据支持**：所需增长不高于已披露增长，且没有反对证据\n- **◐ 部分支持**：有支持，也有差距或反对项\n"
        "- **✖ 目前证据不支持**：所需增长远高于已披露增长，或还有其他反对项\n- **? 信息不足**：核心检查缺数据，无法判断\n\n规则为 rules-v1，阈值待团队验证。",
        "- **✔ Supported Today**: required growth is at or below reported growth and nothing is contrary\n"
        "- **◐ Partially Supported**: some support, with gaps or contrary items\n"
        "- **✖ Not Supported Today**: required growth far above reported growth, or other contrary items\n"
        "- **? Insufficiently Specified**: the core check lacks data\n\nRules are rules-v1; thresholds await team validation."),
    # raw data
    "facts_title": ("SEC 已披露财务数据", "SEC reported financials"), "data_table": ("数据表", "Data table"),
    "price_title": ("股价历史", "Price history"), "filings_title": ("SEC 申报记录", "SEC filings"),
    "filings_note": ("申报记录只有元数据，本版本不读取正文。", "Filing records are metadata only; documents are not read in this version."),
    "no_public": ("本次没有取到公开数据，见下方运行记录中的数据错误。", "No public data in this run; see the data errors in the run log below."),
    "prob_model": ("旧版概率参考（按你填写的漂移率）", "Older model reference (your drift)"),
    "prob_extra": ("模型输出，不参与首页概率，也不是价格预测。", "A model output; not part of the headline probability and not a price prediction."),
    "prob_off": ("未计算：在「申报范围与附加项」里填写年化漂移率 μ（例如 0.07）后重新生成。", "Not computed: enter a yearly drift μ (e.g. 0.07) under “Filing window and extras” and build again."),
    "prob_na": ("无法计算：", "Not computable: "),
    "assumptions": ("假设", "Assumptions"), "limitations": ("限制", "Limitations"),
    "run_log": ("运行记录", "Run log"), "data_errors": ("数据错误", "Data errors"), "missing_items": ("缺失项", "Missing inputs"),
    "notes": ("提示（{n}）", "Notes ({n})"),
    "download": ("下载报告 JSON", "Download report JSON"),
    # history
    "u_hist_all": ("UNIT 01 · HIST 全部磁带", "UNIT 01 · HIST all tapes"),
    "history_empty": ("还没有保存的报告。", "No saved reports yet."),
    "history_hint": ("选一行打开报告；选两行或更多进行对比。", "Select one row to open a report; select two or more to compare."),
    "compare": ("UNIT 02 · CMP 对比", "UNIT 02 · CMP comparison"),
}

FIELD_META = {
    # name: (zh label, en label, placeholder, zh help, en help)
    "claim_text": ("观点原文", "Claim", ("例如：RKLB 3 年内冲到 200", "e.g. NVDA will hit $300 in 3 years"), "要核验的原始说法，原样记录。", "The claim as stated, recorded verbatim."),
    "ticker": ("股票代码", "Ticker", "AAPL", "美股代码。", "US ticker."),
    "target_price": ("目标价", "Target price", "100", "观点给出的目标价格。", "The price the claim names."),
    "horizon_years": ("时间（年）", "Horizon (years)", "3", "观点在多少年内或多少年后兑现，可填小数。", "Years until the claim should hold; decimals allowed."),
    "currency": ("币种", "Currency", "USD", "3 位 ISO 代码。SEC 财务数据按 USD 比较。", "3-letter ISO code. SEC amounts are compared in USD."),
    "reference_price": ("参考价", "Reference price", "50", "参考值：当前或某日的股价。", "Reference value: the price on a date."),
    "reference_price_date": ("参考价日期", "Reference date", "YYYY-MM-DD", "参考价对应的交易日。", "Trading day of the reference price."),
    "reference_price_source": ("参考价来源", "Reference source", "", "记录参考价从哪里来。", "Where the reference price came from."),
    "current_shares": ("当前股份数（可选）", "Current shares (optional)", "95000000", "参考值：最近一次披露的流通股数。", "Reference value: latest reported shares."),
    "target_assumed_shares": ("目标期股份数", "Target-date shares", "100000000", "假设：目标日期的总股数。只在选择「直接填股数」时使用。", "Assumption: total shares at the target date. Used only with “Enter a count”."),
    "share_rate_pct": ("股份数年变化（%）", "Yearly share change (%)", "1.0", "假设：每年股数增减的百分比。正数是增发稀释，负数是回购。默认取近年趋势。",
                       "Assumption: yearly % change in share count. Positive = dilution, negative = buybacks. Default: recent trend."),
    "valuation_multiple": ("估值倍数", "Valuation multiple", "25", "假设：目标日期的 P/S 或 P/E。默认等于当前倍数。", "Assumption: P/S or P/E at the target date. Default: today's multiple."),
    "base_annual_metric": ("基期年度指标（可选）", "Base annual metric (optional)", "200000000", "参考值：P/S 填年营收，P/E 填年净利润。", "Reference value: annual revenue for P/S, net income for P/E."),
    "base_metric_currency": ("基期指标币种（可选）", "Base metric currency (optional)", "USD", "须与价格币种一致。", "Must match the price currency."),
    "base_metric_period": ("基期期间（可选）", "Base period (optional)", "FY2025", "基期指标对应的财年。", "Fiscal year of the base metric."),
    "filings_since": ("申报检索起始日（可选）", "Filings since (optional)", "YYYY-MM-DD", "检查 SEC 申报覆盖范围的起点。", "Start of the SEC filing window."),
    "probability_drift": ("年化漂移率 μ（可选）", "Yearly drift μ (optional)", "0.07", "0.07 = 7%。留空则不计算旧版概率参考。", "0.07 = 7%. Leave empty to skip the older model reference."),
    "probability_volatility": ("年化波动率 σ（可选）", "Yearly volatility σ (optional)", "", "留空时使用历史波动率。", "Empty uses historical volatility."),
    "price_condition": ("怎样算实现", "What counts as coming true", "", "", ""),
}
TEXT_FIELDS = [n for n in FIELD_META if n != "price_condition"]
DRAFT_FIELDS = [n for n in TEXT_FIELDS if n != "share_rate_pct"]  # the page's % field maps to share_change_rate
FIELD_ALIASES = {"share_change_rate": "share_rate_pct", "share_change_mode": "share_rate_pct"}
SHARE_MODES = ("trend", "flat", "rate", "absolute")
CLAIM_FIELDS = ("claim_text", "ticker", "target_price", "horizon_years")
METHODS = ("price_to_sales", "price_to_earnings")
MODES = {"live": ("LIVE 实时", "LIVE"), "record": ("REC 实时并录制", "REC · live + record"), "replay": ("PLAY 回放", "PLAY · replay"),
         "synthetic": ("DEMO 合成示例", "DEMO · synthetic")}
MODE_NOTES = {
    "live": ("请求 SEC、行情和期权接口；SEC 需要联系邮箱。", "Calls SEC, quote and option sources; SEC needs a contact email."),
    "record": ("同实时，并把每个响应录制到本机，之后可以回放。", "Same as live, and records every response locally for replay."),
    "replay": ("只读本机录制的响应，不联网；没有录制的数据会显示为失败。", "Reads recorded responses only, offline; anything not recorded shows as failed."),
    "synthetic": ("虚构公司 SYNT 的示例数据，不是真实行情，只用来演示。", "Example data for the fictional company SYNT; not real market data, for demos only."),
}
EXAMPLES = {"ps": REPO_ROOT / "examples" / "synthetic_claim_ps.json", "pe": REPO_ROOT / "examples" / "synthetic_claim_pe.json"}
VERDICT_STYLE = {"supported_today": ("✔", "good"), "partially_supported": ("◐", "warn"),
                 "not_supported_today": ("✖", "bad"), "insufficiently_specified": ("?", "neutral")}
STANCE = {"supporting": ("✔", "支持", "Supporting", "good"), "contrary": ("✖", "反对", "Contrary", "bad"),
          "missing": ("…", "缺失", "Missing", "neutral"), "neutral": ("·", "背景", "Context", "neutral")}
STATUS = {
    "evaluated": ("已完成", "Done"),
    "evaluated_with_provider_errors": ("已完成（部分数据源失败）", "Done (some sources failed)"),
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
STEP_LABELS = {
    "sec_filings": ("SEC 申报列表", "SEC filings"), "sec_facts": ("SEC 财务数据", "SEC financials"), "price_history": ("日线行情", "Daily prices"),
    "options": ("期权链", "Option chain"), "risk_free_rate": ("美债利率", "Treasury yield"), "price_base_rate": ("本股历史涨幅", "Own price history"),
    "benchmarks": ("大盘对标", "Benchmarks"), "base_rate": ("同规模公司基准率", "Peer base rate"), "insiders": ("内部人交易", "Insider trades"),
    "prediction_markets": ("预测市场", "Prediction markets"), "fear_greed": ("恐惧贪婪指数", "Fear & Greed"),
}
TONE_OF_ROW = {"good": "good", "bad": "bad", "missing": "neutral", "neutral": "neutral"}
NARR_BADGE = {"verified": "✔", "qualitative": "○", "cited_elsewhere": "⚠", "unsupported": "✖"}
JUMPS = (("u-clm", "CLM"), ("u-kln", "KLN"), ("u-prb", "PRB"), ("u-feed", "FEED"), ("u-rpt", "RPT"), ("u-sig", "SIG"), ("u-nar", "NAR"))
KLINE_HEIGHT = 600


def lang() -> str:
    return st.session_state.get("lang", "zh")


def zh() -> bool:
    return lang() == "zh"


def t(key: str, **kw) -> str:
    text = S[key][0 if zh() else 1]
    return text.format(**kw) if kw else text


def pick(en: str, zh_text: str) -> str:
    return zh_text if zh() and zh_text else en


def tr(text: Optional[Text]) -> str:
    return ck.tr(text, zh())


def labels(options: dict[str, tuple[str, str]]):
    """format_func with the language fixed when the widget is drawn (it may be called outside the script run)."""
    i = 0 if zh() else 1
    return lambda x: options[x][i]


esc = ck.esc


def label_of(name: str) -> str:
    meta = FIELD_META.get(FIELD_ALIASES.get(name, name))
    if meta is None:
        return name
    return meta[0] if zh() else meta[1]


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def chart_colors() -> dict:
    """Chart colours for the channel screens; they follow the phosphor switch."""
    if st.session_state.get("tube") == "amb":
        return dict(accent="#FFB23E", second="#FFE4A8", muted="#C9933F", grid="#3A2A14", surface="#0F120F", text="#F1DFC0")
    return dict(accent="#74FF9A", second="#FFB23E", muted="#5FCB82", grid="#1E3A26", surface="#0F120F", text="#D6E8D6")


def finish(chart: alt.Chart) -> alt.Chart:
    return chart.configure_view(strokeWidth=0).configure(background="transparent", font="IBM Plex Mono")


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


def pct_p(p) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.1%"
    if p > 0.999:
        return ">99.9%"
    return f"{p * 100:.1f}%"


def fmt_calc(item) -> str:
    if item.value is None:
        return "无法计算" if zh() else "not computable"
    if item.unit.startswith("ratio"):
        return fmt_pct(item.value)
    return fmt_amount(item.value, item.unit.split(" ")[0])


def issue_text(issue) -> str:
    if zh() and issue.code in ISSUE_ZH:
        return ISSUE_ZH[issue.code].format(f=label_of(issue.field))
    return f"{label_of(issue.field)} ({issue.code}): {issue.message}"


def missing_text(m) -> str:
    if zh():
        return f"{label_of(m.field)}（{MISSING_ZH.get(m.field, m.message)}）"
    return f"{label_of(m.field)} ({m.message})"


def table(cols, rows, *, nums: tuple[int, ...] = (), on: Optional[int] = None) -> str:
    """Escaped HTML table. A cell given as ("html", markup) is inserted as is."""
    def cell(x, i, tag):
        cls = []
        if i in nums:
            cls.append("num")
        if on is not None and i == on:
            cls.append("on")
        attr = f' class="{" ".join(cls)}"' if cls else ""
        body = x[1] if isinstance(x, tuple) and x and x[0] == "html" else esc(x)
        return f"<{tag}{attr}>{body}</{tag}>"
    head = "".join(cell(c, i, "th") for i, c in enumerate(cols))
    body = "".join("<tr>" + "".join(cell(c, i, "td") for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="tc-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def tone_dot(tone: str) -> tuple[str, str]:
    return ("html", f'<span class="tc-dotmark tc-{tone}"></span>')


def bullets(items: list[str]) -> str:
    return '<ul class="tc-list">' + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"


def notes_of(result: CaseResult) -> list[str]:
    return result.warnings_zh if zh() and len(result.warnings_zh) == len(result.warnings) else result.warnings


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
    st.session_state.setdefault("f_price_condition", "end")
    st.session_state.setdefault("example_values", {})
    st.session_state.setdefault("sec_mode", "live")
    st.session_state.setdefault("lang", "zh")
    st.session_state.setdefault("view", "new")
    st.session_state.setdefault("tube", "grn")
    st.session_state.setdefault("page", "home")  # "home" (console) or "result" (report) inside the "new" view
    st.session_state.setdefault("ai_auto", False)
    st.session_state.setdefault("animated", set())  # case ids whose report has already played its first-view motion
    for key in ("confirmation", "confirm_feedback", "result", "result_mode", "run_error", "prefill_msg", "prefill_snapshot", "sec_msg",
                "extract_msg", "extraction", "ai_msg", "open_case"):
        st.session_state.setdefault(key, None)
    st.session_state.setdefault("prefill_sources", {})
    st.session_state.setdefault("history_selected", None)


def _keep_widget_state() -> None:
    """The console's inputs are not drawn on the report page; writing them back keeps Streamlit from dropping them meanwhile."""
    for key in [f"f_{n}" for n in TEXT_FIELDS] + ["f_valuation_method", "f_share_mode", "f_price_condition", "sec_mode", "ai_auto"]:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _reset_confirmation() -> None:
    st.session_state["confirmation"] = None
    st.session_state["confirm_feedback"] = None


def _load_example(key: str) -> None:
    draft = json.loads(Path(EXAMPLES[key]).read_text(encoding="utf-8"))["draft"]
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = str(draft.get(name) or "")
    st.session_state["f_valuation_method"] = draft["valuation_method"]
    st.session_state["f_share_mode"] = "absolute" if draft.get("target_assumed_shares") else "trend"
    st.session_state["f_price_condition"] = draft.get("price_condition") or "end"
    # example values count as untouched, so "Read and fill in" for another ticker replaces them
    st.session_state["example_values"] = {name: st.session_state[f"f_{name}"] for name in TEXT_FIELDS} | {"share_mode": st.session_state["f_share_mode"]}
    st.session_state["sec_mode"] = "synthetic"
    st.session_state["view"] = "new"
    st.session_state["page"] = "home"
    st.session_state["prefill_sources"] = {}
    st.session_state["prefill_msg"] = None
    st.session_state["extract_msg"] = None
    _reset_confirmation()


def _clear_form() -> None:
    for name in TEXT_FIELDS:
        st.session_state[f"f_{name}"] = ""
    st.session_state["f_valuation_method"] = "price_to_sales"
    st.session_state["f_share_mode"] = "trend"
    st.session_state["f_price_condition"] = "end"
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


def _msg(key: str, kind: str, zh_en: tuple[str, str]) -> None:
    st.session_state[key] = (kind, zh_en)


def _prefill() -> None:
    ticker = st.session_state["f_ticker"].strip()
    if not ticker:
        _msg("prefill_msg", "error", S["need_ticker"])
        return
    try:
        snap = get_service().reference_snapshot(ticker, mode=st.session_state["sec_mode"])
    except ValueError as exc:
        _msg("prefill_msg", "error", (str(exc), str(exc)))
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
        _msg("prefill_msg", "error", (S["nothing_fetched"][0].format(errors=errors), S["nothing_fetched"][1].format(errors=errors)))
    else:
        zh_text, en_text = S["filled"][0].format(n=n), S["filled"][1].format(n=n)
        if defaults:
            zh_text += " " + S["defaults_used"][0].format(items="；".join(f"{FIELD_META[k][0]} = {v}" for k, v in defaults))
            en_text += " " + S["defaults_used"][1].format(items="; ".join(f"{FIELD_META[k][1]} = {v}" for k, v in defaults))
        if errors:
            zh_text += " " + S["not_fetched"][0].format(errors=errors)
            en_text += " " + S["not_fetched"][1].format(errors=errors)
        _msg("prefill_msg", "warning" if errors else "success", (zh_text, en_text))
    _reset_confirmation()


def _extract_and_fill() -> None:
    """Read ticker, target, horizon and condition from the sentence, then fill the rest from public data."""
    text = st.session_state["f_claim_text"].strip()
    if not text:
        if st.session_state["f_ticker"].strip():
            st.session_state["extract_msg"] = None
            _prefill()  # no sentence, but a ticker: fill from public data only
        else:
            _msg("prefill_msg", "error", S["need_text"])
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
    cond_src = sources.get("price_condition")
    if ex.condition and (cond_src is None or st.session_state["f_price_condition"] == cond_src[0]):
        st.session_state["f_price_condition"] = ex.condition
        sources["price_condition"] = (ex.condition, f"{CLAIM_TEXT_PREFIX}{ex.condition_text}")
        found_zh.append(f"{S['cond_' + ex.condition][0]}（「{ex.condition_text}」）")
        found_en.append(f"{S['cond_' + ex.condition][1]} (“{ex.condition_text}”)")
    st.session_state["prefill_sources"] = sources
    st.session_state["extraction"] = ex
    zh_text = S["recognized"][0].format(items="；".join(found_zh)) if found_zh else S["recognized_none"][0]
    en_text = S["recognized"][1].format(items="; ".join(found_en)) if found_en else S["recognized_none"][1]
    if ex.notes_zh:
        zh_text += " " + " ".join(ex.notes_zh)
        en_text += " " + " ".join(ex.notes_en)
    _msg("extract_msg", "warning" if ex.notes_zh and not found_zh else "info", (zh_text, en_text))
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
        _msg("extract_msg", kind, (mzh + " " + note[0], men + " " + note[1]))


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
        _msg("sec_msg", "error", S["sec_bad"])
        return
    try:
        write_env_value("SEC_USER_AGENT", ua)
    except ValueError:
        _msg("sec_msg", "error", S["sec_bad"])
        return
    get_service.clear()
    _msg("sec_msg", "success", S["sec_saved"])


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
        _msg("ai_msg", "error", ("key 格式不对（应以 sk-ant- 开头）。", "The key should start with sk-ant-."))
        return
    write_env_value("ANTHROPIC_API_KEY", key)
    get_service.clear()
    _msg("ai_msg", "success", ("已保存。", "Saved."))


def _narrate(key_suffix: str, force: bool = False) -> None:
    result = st.session_state["history_selected"] if key_suffix != "current" else st.session_state["result"]
    if result is None:
        return
    get_service().narrate(result, language=lang(), force=force)


def _go(page: str) -> None:
    st.session_state["page"] = page


def _open_tape(case_id: str) -> None:
    st.session_state["view"] = "history"
    st.session_state["open_case"] = case_id


def _show_history() -> None:
    st.session_state["view"] = "history"


def sec_gate() -> bool:
    """True while live SEC requests are impossible because no valid contact email is set."""
    return bool(validate_user_agent(get_service().settings.sec_user_agent))


def current_draft() -> ClaimDraft:
    values = {name: (st.session_state[f"f_{name}"] or None) for name in DRAFT_FIELDS}
    values["valuation_method"] = st.session_state["f_valuation_method"]
    mode = st.session_state["f_share_mode"]
    values["share_change_mode"] = mode
    values["share_change_rate"] = _share_rate_value()
    values["price_condition"] = st.session_state["f_price_condition"]
    if mode != "absolute":
        values["target_assumed_shares"] = None
    # a source label only applies while the field still holds the value it filled in
    sources = {("share_change_rate" if name == "share_rate_pct" else name): src
               for name, (val, src) in st.session_state["prefill_sources"].items() if st.session_state.get(f"f_{name}") == val}
    if mode != "trend":
        sources.pop("share_change_rate", None)
    values["field_sources"] = sources or None
    return ClaimDraft(**values)


def show_msg(key: str, container=None) -> None:
    msg = st.session_state.get(key)
    if not msg:
        return
    kind, (zh_text, en_text) = msg
    target = container or st
    {"success": target.success, "warning": target.warning, "error": target.error, "info": target.info}[kind](zh_text if zh() else en_text)


# ------------------------------------------------------------------ bridge and footer (every page)


def bridge_unit(*, result_page: bool, warn: int) -> None:
    mode = st.session_state["sec_mode"]
    link = ("amber" if sec_gate() else "green") if mode in ("live", "record") else "off"
    chips = [f"TC {get_service().now():%Y-%m-%d · %H:%M} UTC", f"{t('data_chip')} · {MODES[mode][0 if zh() else 1]}"]
    with st.container(key="u-c-bridge"):
        left, right = st.columns([1, 1], vertical_alignment="center")
        left.markdown(ck.bridge(zh(), sys_on=result_page, link=link, warn=warn, chips=chips), unsafe_allow_html=True)
        with right, st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if result_page:
                st.button(t("back_new"), key="btn_new", on_click=_go, args=("home",))
            st.radio(t("view"), options=["new", "history"], format_func=labels({"new": S["view_new"], "history": S["view_history"]}), key="view",
                     horizontal=True, label_visibility="collapsed")
            st.radio(t("tube"), options=["grn", "amb"], format_func=lambda x: "GRN" if x == "grn" else "AMB", key="tube", horizontal=True,
                     label_visibility="collapsed")
            st.radio(t("language"), options=["zh", "en"], format_func=lambda x: "中" if x == "zh" else "EN", key="lang", horizontal=True,
                     label_visibility="collapsed")


def footer_unit() -> None:
    with st.container(key="u-o-footer"):
        html_block(ck.footer(zh()))


# ------------------------------------------------------------------ console page


def field(name: str, container=None) -> None:
    zh_label, en_label, placeholder, help_zh, help_en = FIELD_META[name]
    label, help_text = (zh_label, help_zh) if zh() else (en_label, help_en)
    target = container or st
    src = st.session_state["prefill_sources"].get(name)
    line = ""
    if src and st.session_state.get(f"f_{name}") == src[0]:
        kind = "def" if src[1].startswith(DEFAULT_PREFIX) else "claim" if src[1].startswith(CLAIM_TEXT_PREFIX) else "data"
        text = src[1].removeprefix(DEFAULT_PREFIX).removeprefix(CLAIM_TEXT_PREFIX)
        label = f"{label} {ck.MARKS[kind]}"
        line = ck.src_line(kind, (f"「{text}」" if zh() else f"“{text}”") if kind == "claim" else text, zh())
    elif src and st.session_state.get(f"f_{name}", "").strip():
        line = ck.src_line("user", t("you_changed", v=src[0]), zh())
    if name == "claim_text":
        target.text_area(label, key=f"f_{name}", placeholder=placeholder[0 if zh() else 1], help=help_text, height=110, label_visibility="collapsed")
    else:
        target.text_input(label, key=f"f_{name}", placeholder=placeholder, help=help_text)
    if line:
        target.markdown(line, unsafe_allow_html=True)


def render_clm() -> None:
    html_block(ck.unit_head(t("u_clm"), ck.chip(t("clm_flow")), "u-clm") + ck.hero(zh()))
    field("claim_text")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.button(t("ex_ps"), on_click=_load_example, args=("ps",), key="btn_example_ps")
        st.button(t("ex_pe"), on_click=_load_example, args=("pe",), key="btn_example_pe")
        st.button(t("clear"), on_click=_clear_form, key="btn_clear")
        st.space("stretch")
        st.button(t("prefill"), key="btn_prefill", on_click=_extract_and_fill, help=t("prefill_help"), type="primary")
    show_msg("extract_msg")
    show_msg("prefill_msg")


def render_sys() -> None:
    i = 0 if zh() else 1
    html_block(ck.unit_head("SYS · " + ("系统" if zh() else "SYSTEM")))
    st.radio(t("mode"), options=list(MODES), format_func=labels(MODES), key="sec_mode", horizontal=True, help=t("mode_help"))
    st.caption(MODE_NOTES[st.session_state["sec_mode"]][i])
    html_block(ck.sub_head(t("sec_setup")))
    if sec_gate():
        st.caption(t("sec_missing"))
        st.text_input(t("sec_email"), key="sec_email_input", placeholder="you@example.org")
        st.button(t("sec_save"), key="btn_sec_save", on_click=_save_sec_contact)
    else:
        html_block(ck.lamp_row("green", t("sec_ok")))
    show_msg("sec_msg")
    html_block(ck.sub_head(t("ai_setup")))
    st.caption(t("ai_optional"))
    if not get_service().settings.anthropic_api_key:
        st.caption(t("ai_missing"))
        st.text_input(t("ai_key"), key="ai_key_input", type="password", placeholder="sk-ant-...")
        st.button(t("ai_save"), key="btn_ai_save", on_click=_save_ai_key)
    else:
        html_block(ck.lamp_row("green", t("ai_ok")))
        st.toggle(t("ai_auto"), key="ai_auto", help=t("ai_auto_help"))
    show_msg("ai_msg")


def group(key: str) -> None:
    title, note = S[key][0 if zh() else 1]
    html_block(ck.group_head(title, note))


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


def render_groups() -> None:
    a, b = st.columns(2)
    with a, st.container(key="g-claim"):
        group("g_claim")
        src = st.session_state["prefill_sources"].get("price_condition")
        mark = " ✎" if src and src[0] == st.session_state["f_price_condition"] else ""
        st.radio(t("condition") + mark, options=["touch", "end"], format_func=labels({x: S[f"cond_{x}"] for x in ("touch", "end")}), key="f_price_condition",
                 horizontal=True, help=t("condition_help"))
        c = st.columns(3)
        field("ticker", c[0])
        field("target_price", c[1])
        field("horizon_years", c[2])
    with b, st.container(key="g-price"):
        group("g_price")
        c = st.columns(3)
        field("reference_price", c[0])
        field("reference_price_date", c[1])
        field("currency", c[2])
        field("reference_price_source")
        c = st.columns(2)
        field("current_shares", c[0])
        c[1].selectbox(t("share_mode"), options=list(SHARE_MODES), format_func=labels({x: S[f"sm_{x}"] for x in SHARE_MODES}), key="f_share_mode",
                       help=t("share_mode_help"))
        render_share_input()
    a, b = st.columns(2)
    with a, st.container(key="g-val"):
        group("g_val")
        c = st.columns(2)
        c[0].selectbox(t("method"), options=list(METHODS), format_func=labels({"price_to_sales": S["m_ps"], "price_to_earnings": S["m_pe"]}),
                       key="f_valuation_method", on_change=_method_changed, help=t("method_help"))
        field("valuation_multiple", c[1])
        field("base_annual_metric")
        c = st.columns(2)
        field("base_metric_currency", c[0])
        field("base_metric_period", c[1])
    with b, st.container(key="g-extra"):
        group("g_extra")
        field("filings_since")
        c = st.columns(2)
        field("probability_drift", c[0])
        field("probability_volatility", c[1])
        st.caption(t("prob_note"))


def render_checks(validation) -> None:
    html_block(ck.sub_head(t("checks")))
    for issue in validation.issues:
        st.error(issue_text(issue))
    blocking = [m for m in validation.missing_fields if m.blocking]
    if blocking:
        st.warning(t("missing_core") + ("、" if zh() else ", ").join(label_of(m.field) for m in blocking))
    optional = [m for m in validation.missing_fields if not m.blocking]
    if optional:
        st.caption(t("optional_missing") + ("；" if zh() else "; ").join(missing_text(m) for m in optional))
    for w in (validation.warnings_zh if zh() else validation.warnings):
        st.caption("⚠ " + w)
    if not validation.issues and not blocking:
        html_block(ck.lamp_row("green", t("checks_ok")))


def run_case(draft: ClaimDraft, confirmation: Confirmation, board) -> None:
    """Fetch and evaluate, showing each data step on the pipeline screen, then open the report page."""
    st.session_state["result"] = None
    st.session_state["run_error"] = None
    st.html(ck.page_css([], flowing=True))
    auto = bool(st.session_state.get("ai_auto")) and bool(get_service().settings.anthropic_api_key)
    steps_shown = STEP_LABELS | ({"narrative": ("AI 叙述", "AI narrative")} if auto else {})
    live_steps: dict[str, str] = {}

    def on_step(step: str, step_state: str) -> None:
        live_steps[step] = step_state
        board.markdown(ck.pipeline(live_steps, zh(), steps_shown), unsafe_allow_html=True)

    board.markdown(ck.pipeline(live_steps, zh(), steps_shown), unsafe_allow_html=True)
    try:
        result = get_service().evaluate(draft, confirmation, sec_mode=st.session_state["sec_mode"], progress=on_step)
        st.session_state["result"] = result
        st.session_state["result_mode"] = st.session_state["sec_mode"]
        if auto and result.oracle is not None:
            on_step("narrative", "running")
            try:
                get_service().narrate(result, language=lang())
            except Exception as exc:  # the report stands without a narrative
                _msg("ai_msg", "error", (S["ai_failed"][0] + f"{type(exc).__name__}: {exc}", S["ai_failed"][1] + f"{type(exc).__name__}: {exc}"))
        st.session_state["page"] = "result"
    except Exception as exc:  # unexpected failure; keep page usable and show it
        st.session_state["run_error"] = f"{type(exc).__name__}: {exc}"
    st.rerun()


def render_cnf(current: bool) -> None:
    filled = any(st.session_state[f"f_{n}"].strip() for n in TEXT_FIELDS if n != "claim_text") or bool(st.session_state["prefill_sources"])
    head = st.empty()
    html_block(ck.para(t("cnf_hint") if filled else t("cnf_stby")) + (f'<div class="tc-note" style="margin-top:6px">{esc(t("marks"))}</div>' if filled else ""))
    render_groups()
    draft = current_draft()  # widgets above may have changed the values
    validation = validate_draft(draft, today=get_service().today())
    if filled:
        render_checks(validation)
    row = st.columns([3, 2, 2], vertical_alignment="center")
    if row[1].button(t("confirm"), key="btn_confirm", width="stretch"):
        try:
            st.session_state["confirmation"] = confirm(draft, now=get_service().now, today=get_service().today())
            st.session_state["confirm_feedback"] = None
        except ConfirmationError:
            st.session_state["confirmation"] = None
            st.session_state["confirm_feedback"] = "invalid"
    confirmation = st.session_state["confirmation"]
    state = confirmation_state(draft, confirmation)
    run_clicked = row[2].button(t("run"), key="btn_run", type="primary", disabled=state != "confirmed", width="stretch")
    row[0].markdown(ck.keyswitch(state == "confirmed", zh()), unsafe_allow_html=True)
    if st.session_state["confirm_feedback"]:
        st.error(t("invalid_confirm"))
    if state == "confirmed":
        st.success(t("confirmed", t=f"{confirmation.confirmed_at:%Y-%m-%d %H:%M:%S}"))
    elif state == "stale":
        st.warning(t("stale"))
    elif filled:
        st.info(t("unconfirmed"))
    word = {"confirmed": "ARM", "stale": "SAFE · STALE"}.get(state, "SAFE" if filled else "STBY")
    head.markdown(ck.unit_head(t("u_cnf"), ck.chip(word), "u-cnf"), unsafe_allow_html=True)
    board = st.empty()
    if run_clicked:
        run_case(draft, confirmation, board)
    if st.session_state["run_error"]:
        st.error(t("run_failed") + st.session_state["run_error"])
        st.caption(t("run_kept"))
    result = st.session_state["result"]
    if result is None:
        return
    if current:
        html_block(ck.lamp_row("green", t("ready")) + ck.tape(result, zh()))
        st.button(t("open_report"), key="btn_open", on_click=_go, args=("result",), type="primary")
    elif result.confirmed_claim is None:  # blocked before any data was fetched
        for issue in result.validation_issues:
            st.error(issue_text(issue))
        for w in notes_of(result):
            st.warning(w)
    else:
        st.warning(t("hidden"))


def render_tapes() -> None:
    html_block(ck.unit_head(t("u_hist"), ck.chip(t("hist_hint")), "u-hist"))
    store = get_service().store
    cases = [c for c in (store.list_cases() if store is not None else []) if c.confirmed_claim is not None][:3]
    if not cases:
        st.caption(t("history_empty"))
        return
    for c in cases:
        a, b = st.columns([7, 1], vertical_alignment="center")
        a.markdown(ck.tape(c, zh()), unsafe_allow_html=True)
        b.button(t("play"), key=f"btn_tape_{c.case_id}", on_click=_open_tape, args=(c.case_id,), width="stretch")
    st.button(t("hist_all"), key="btn_hist_all", on_click=_show_history)


def view_home(current: bool) -> None:
    bridge_unit(result_page=False, warn=1 if sec_gate() and st.session_state["sec_mode"] in ("live", "record") else 0)
    left, right = st.columns([2, 1])
    with left, st.container(key="u-c-clm"):
        render_clm()
    with right, st.container(key="u-o-sys"):
        render_sys()
    with st.container(key="u-b-cnf"):
        render_cnf(current)
    with st.container(key="u-o-feed"):
        html_block(ck.feed_info(zh(), t("u_feed")))
    with st.container(key="u-b-mth"):
        html_block(ck.methods_info(zh(), t("u_mth")))
    with st.container(key="u-c-hist"):
        render_tapes()
    footer_unit()
    st.html(ck.page_css([("u-c-clm", 1), ("u-b-cnf", 2), ("u-o-feed", 3), ("u-b-mth", 4), ("u-c-hist", 5)]))


# ------------------------------------------------------------------ report channel 1: layered report


def render_report(report: Optional[PlainReport]) -> None:
    if report is None:
        st.caption(t("r_none"))
        return
    i = 0 if zh() else 1
    html_block(f'<div class="tc-h">{esc(t("r_layers"))}</div>')
    for layer in report.layers:
        html_block(f'<div class="tc-layer">{esc(tr(layer.title))}</div>'
                   + table(S["r_cols"][i], [(tone_dot(TONE_OF_ROW[r.tone]), tr(r.signal), tr(r.data), tr(r.meaning)) for r in layer.rows], nums=(2,)))
    html_block(f'<div class="tc-h">{esc(t("r_analysis"))}</div>')
    c1, c2 = st.columns(2)
    c1.markdown(f'<div class="tc-card"><b>{esc(t("r_agree"))}</b>{bullets([tr(x) for x in report.agreements])}</div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="tc-card"><b>{esc(t("r_diverge"))}</b>{bullets([tr(x) for x in report.divergences])}</div>', unsafe_allow_html=True)
    if report.time_view:
        html_block(f'<div class="tc-layer">{esc(t("r_time"))}</div>'
                   + table(S["r_time_cols"][i], [(tr(r.signal), tr(r.data), tr(r.meaning)) for r in report.time_view]))
    if report.scenarios:
        html_block(f'<div class="tc-h">{esc(t("r_scen"))}</div>'
                   + table(S["r_scen_cols"][i], [(tr(x.name), tr(x.assumptions), x.price or "—", x.vs_target or "—", tr(x.meaning))
                                                 for x in report.scenarios], nums=(2, 3)))
        st.caption(tr(report.scenario_note))
    html_block(f'<div class="tc-h">{esc(t("r_concl"))}</div><div class="tc-sub" style="font-size:.95rem;opacity:1">{esc(tr(report.verdict_meaning))}</div>')
    c1, c2 = st.columns(2)
    c1.markdown(f'<div class="tc-card"><b class="tc-good">↑ {esc(t("r_up"))}</b>{bullets([tr(x) for x in report.upside])}</div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="tc-card"><b class="tc-bad">↓ {esc(t("r_down"))}</b>{bullets([tr(x) for x in report.downside])}</div>', unsafe_allow_html=True)
    if report.monitor:
        html_block(f'<div class="tc-h">{esc(t("r_monitor"))}</div>'
                   + table(S["r_mon_cols"][i], [(tr(m.signal), tr(m.current), tr(m.threshold), tr(m.meaning)) for m in report.monitor]))


# ------------------------------------------------------------------ report channel 2: scenario


def _years_left(result: CaseResult) -> float:
    return max((result.oracle.target_date - result.created_at.date()).days / 365.25, 1 / 365.25)


def _use_market_move(key_suffix: str, move: float) -> None:
    pct = int(round(min(move, 3.0) * 100))
    st.session_state[f"sc_up_{key_suffix}"] = max(5, pct)
    st.session_state[f"sc_down_{key_suffix}"] = -min(90, max(5, pct))


def kpi(label: str, value: str, main: bool = False) -> str:
    return f'<div class="tc-kpi{" main" if main else ""}"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div></div>'


@st.fragment
def tab_scenario(result: CaseResult, key_suffix: str) -> None:
    o = result.oracle
    st.caption(t("sc_intro"))
    vol0 = o.base_volatility or o.implied_volatility if o is not None else None
    if o is None or not o.spot or not vol0:
        st.info(t("sc_need"))
        return
    today = result.created_at.date()
    k = key_suffix
    c1, c2 = st.columns([2, 1])
    c1.text_input(t("sc_event"), key=f"sc_name_{k}", placeholder=t("sc_event_ph"))
    default_date = min(today + timedelta(days=182), o.target_date - timedelta(days=1))
    ev_date = c2.date_input(t("sc_date"), value=default_date, min_value=today + timedelta(days=1), max_value=o.target_date, key=f"sc_date_{k}")

    with st.container(border=True):
        st.markdown(f"**{t('sc_market')}**")
        term = result.options.term if result.options is not None else []
        if not term:
            st.caption(t("sc_no_term"))
        else:
            ev = event_move(term, ev_date, today)
            if ev.status == "ok":
                st.markdown(t("sc_move_ok", d=ev_date, m=f"{ev.move * 100:.0f}%", b=ev.before_expiry or today, a=ev.after_expiry))
                st.button(t("sc_use_market"), key=f"sc_use_{k}", on_click=_use_market_move, args=(k, ev.move))
            elif ev.status == "not_priced":
                st.markdown(t("sc_move_flat", d=ev_date))
            else:
                st.caption(t("sc_move_na") + tr(ev.note))
            if ev.note and ev.status == "ok":
                st.caption(tr(ev.note))

    st.markdown(f"**{t('sc_yours')}**")
    st.session_state.setdefault(f"sc_up_{k}", 60)
    st.session_state.setdefault(f"sc_down_{k}", -30)
    st.session_state.setdefault(f"sc_p_{k}", 50)
    st.session_state.setdefault(f"sc_vol_{k}", int(round(min(vol0, 2.0) * 100)))
    c = st.columns(2)
    p_succ = c[0].slider(t("sc_p"), 0, 100, key=f"sc_p_{k}", format="%d%%")
    vol = c[1].slider(t("sc_vol"), 5, 200, key=f"sc_vol_{k}", format="%d%%", help=t("sc_vol_help"))
    c = st.columns(2)
    up = c[0].slider(t("sc_up"), 0, 300, key=f"sc_up_{k}", format="+%d%%")
    down = c[1].slider(t("sc_down"), -90, 0, key=f"sc_down_{k}", format="%d%%")

    touch = o.condition == "touch"
    m1 = next((m for m in o.methods if m.id == "M1" and m.status == "ok"), None)
    market_p = (m1.touch_probability if touch else m1.probability) if m1 else None
    r = float(o.risk_free_rate) if o.risk_free_rate is not None else 0.0
    res = scenario(spot=o.spot, target=float(o.target_price), vol=vol / 100, rate=r, years=_years_left(result),
                   event_years=(ev_date - today).days / 365.25, up=up / 100, down=down / 100, p_success=p_succ / 100, touch=touch, market_p=market_p)
    html_block('<div class="tc-kpis">' + kpi(t("sc_result"), pct_p(res.probability), main=True) + kpi(t("sc_if_up"), pct_p(res.p_if_success))
               + kpi(t("sc_if_down"), pct_p(res.p_if_failure)) + (kpi(t("sc_market_p"), pct_p(market_p)) if market_p is not None else "") + "</div>")
    th = chart_colors()
    lo, hi = ck.sqrt_x(res.p_if_failure), ck.sqrt_x(res.p_if_success)
    marks = [(res.probability, th["second"], f'{t("sc_result")} {pct_p(res.probability)}', ""), (0.5, th["muted"], "50%", "below")]
    if market_p is not None:
        marks.append((market_p, th["accent"], f"M1 {pct_p(market_p)}", "below"))
    mk = "".join(f'<div class="mk {cls}" style="left:{ck.sqrt_x(p):.2f}%;background:{col}"><b>{esc(lbl)}</b></div>' for p, col, lbl, cls in marks)
    html_block(f'<div class="tc-gauge"><div class="rail"></div><div class="fill" style="left:{lo:.2f}%;width:{max(hi - lo, .5):.2f}%"></div>{mk}</div>')
    if res.break_even == 0:
        st.markdown(t("sc_break_zero"))
    elif res.break_even is not None:
        st.markdown(t("sc_break", p=f"{res.break_even * 100:.0f}%"))
    else:
        st.markdown(t("sc_break_none", p=pct_p(res.p_if_success)))
    if res.market_implied is not None:
        st.markdown(t("sc_implied", p=f"{res.market_implied * 100:.0f}%", u=f"{p_succ}%"))
    st.caption(t("sc_note"))


# ------------------------------------------------------------------ report channel 3: levels and methods


def tab_levels(result: CaseResult) -> None:
    o = result.oracle
    th = chart_colors()
    i = 0 if zh() else 1
    touch = o.condition == "touch"
    if o.spot:
        years = _years_left(result)
        r = float(o.risk_free_rate) if o.risk_free_rate is not None else 0.0
        target = float(o.target_price)
        top = max(target * 1.3, o.spot * 2.2)
        rows = []
        for j in range(61):
            level = o.spot * 0.5 + (top - o.spot * 0.5) * j / 60
            iv = None
            if result.options is not None:
                iv, _ = iv_at(result.options.calls, level)
            vol = iv or o.base_volatility
            if not vol:
                continue
            rows.append({"level": level, "kind": t("lv_end"), "p": p_end_above(o.spot, level, vol, years, r)})
            rows.append({"level": level, "kind": t("lv_touch"), "p": p_touch(o.spot, level, vol, years, r)})
        if rows:
            html_block(f'<div class="tc-h">{esc(t("lv_title"))}</div>')
            df = pd.DataFrame(rows)
            hover = alt.selection_point(fields=["level"], nearest=True, on="pointerover", empty=False)
            base = alt.Chart(df).encode(
                x=alt.X("level:Q", title=t("lv_level"), scale=alt.Scale(domain=[o.spot * 0.5, top], nice=False), axis=alt.Axis(grid=False, labelColor=th["muted"], titleColor=th["muted"])),
                y=alt.Y("p:Q", title=t("lv_prob"), scale=alt.Scale(domain=[0, 1]),
                        axis=alt.Axis(format="%", gridColor=th["grid"], labelColor=th["muted"], titleColor=th["muted"], domain=False)),
                color=alt.Color("kind:N", scale=alt.Scale(domain=[t("lv_end"), t("lv_touch")], range=[th["accent"], th["second"]]),
                                legend=alt.Legend(orient="top", title=None, labelColor=th["text"])),
            )
            lines = base.mark_line(strokeWidth=2.2)
            points = base.mark_point(size=70, filled=True, stroke=th["surface"], strokeWidth=2).encode(
                opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                tooltip=[alt.Tooltip("level:Q", title=t("lv_level"), format=",.2f"), alt.Tooltip("kind:N", title=" "),
                         alt.Tooltip("p:Q", title=t("lv_prob"), format=".1%")]).add_params(hover)
            rules = alt.Chart(pd.DataFrame({"v": [target, o.spot], "lbl": [f"{t('target')} {target:g}", f"{t('now_price')} {o.spot:,.2f}"]}))
            rule = rules.mark_rule(strokeDash=[4, 3], color=th["muted"], strokeWidth=1).encode(x="v:Q")
            text = rules.mark_text(align="left", dx=4, y=10, color=th["muted"], fontSize=11).encode(x="v:Q", text="lbl:N")
            st.altair_chart(finish((lines + points + rule + text).properties(height=300)), width="stretch")
            st.caption(t("lv_note"))

    html_block(f'<div class="tc-h">{esc(t("m_title"))}</div>')
    rows = []
    for m in o.methods:
        p = m.touch_probability if touch else m.probability
        used = m.status == "ok" and p is not None and o.low is not None and o.low - 1e-12 <= p <= o.high + 1e-12 and ck.counted(result, m)
        rows.append((f"{m.id} {tr(m.name)}", pct_p(m.probability) if m.status == "ok" else "—", pct_p(m.touch_probability) if m.status == "ok" else "—",
                     tr(m.measures), t("m_yes") if used else (t("m_no") if m.status == "ok" else "—")))
    html_block(table(S["m_cols"][i], rows, nums=(1, 2), on=2 if touch else 1))
    st.caption(tr(o.agreement))
    st.markdown(f"**{t('method_detail')}**")
    for m in o.methods:
        p = m.touch_probability if touch else m.probability
        with st.expander(f"{m.id} · {tr(m.name)} · {pct_p(p) if m.status == 'ok' else '—'}"):
            st.markdown(tr(m.detail))
            for lim in m.limitations:
                st.markdown(f"- {tr(lim)}")
            if m.inputs:
                st.json(m.inputs, expanded=False)
    with st.expander(t("why_trust")):
        st.markdown(t("why_trust_body"))


# ------------------------------------------------------------------ report channel 4: fundamentals


def render_verdict(result: CaseResult) -> None:
    v = result.verdict
    if v is None:
        return
    icon, tone = VERDICT_STYLE[v.label]
    title = f"{icon} {(v.display_zh or VERDICT_DISPLAY_ZH[v.label]) if zh() else v.display}"
    html_block(f'<div class="tc-card tc-verdict tc-{tone}"><div class="lbl">{esc(title)}</div>'
               f'<div class="tc-sub">{esc(t("as_of", d=v.as_of, r=v.rules_version))}</div></div>')


def render_key_numbers(result: CaseResult) -> None:
    calc = {c.name: c for c in result.calculations}
    claim = result.confirmed_claim.values
    metric = "required_annual_revenue" if claim.valuation_method.value == "price_to_sales" else "required_annual_net_income"
    growth = next((i for i in result.evidence_items if i.id == "E1"), None)
    req = Decimal(growth.measured["required_cagr_from_reported"]) if growth and "required_cagr_from_reported" in growth.measured else None
    hist = Decimal(growth.measured["reported_cagr"]) if growth and "reported_cagr" in growth.measured else None
    i = 0 if zh() else 1
    cards = [kpi(CALC[name][i], fmt_calc(calc[name])) for name in ("required_return", "annualized_price_return", "target_market_cap", metric) if name in calc]
    if req is not None:
        cards.append(kpi("所需指标年增速（相对已披露）" if zh() else "Required metric growth (from reported)", fmt_pct(req)))
        if hist is not None:
            cards.append(kpi("近年已披露增速" if zh() else "Reported growth", fmt_pct(hist)))
            cards.append(kpi("差距（百分点）" if zh() else "Gap (pp)", f"{(req - hist) * 100:+.2f}"))
    html_block('<div class="tc-kpis">' + "".join(cards) + "</div>")


def render_conclusion(result: CaseResult) -> None:
    v = result.verdict
    html_block(f'<div class="tc-h">{esc(t("rationale"))}</div>' + bullets(v.rationale_zh if zh() and v.rationale_zh else v.rationale))
    html_block(f'<div class="tc-h">{esc(t("rechecks"))}</div>')
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
        for line in (v.limitations_zh if zh() and v.limitations_zh else v.limitations):
            st.markdown(f"- {line}")


def render_evidence(result: CaseResult) -> None:
    i = 1 if zh() else 2
    html_block(f'<div class="tc-h">{esc(t("evidence"))}</div>')
    order = {"contrary": 0, "supporting": 1, "missing": 2, "neutral": 3}
    for item in sorted(result.evidence_items, key=lambda x: (order[x.stance], x.id)):
        icon, _, _, tone = STANCE[item.stance]
        with st.container(border=True):
            html_block(f'<b class="tc-{tone}">{icon} {esc(STANCE[item.stance][i])}</b> · <b>{esc(item.id)} {esc(pick(item.title, item.title_zh))}</b>')
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
    html_block(f'<div class="tc-h">{esc(t("sens"))}</div>')
    sens = result.sensitivity
    if sens is None:
        st.caption(t("sens_none"))
        return
    hist = Decimal(sens.reported_cagr) if sens.reported_cagr else None
    th = chart_colors()

    def cell(v: Optional[str]) -> str:
        if v is None:
            return "—"
        d = Decimal(v)
        mark = "" if hist is None else (" ✔" if d <= hist else " ◐" if d - hist <= Decimal("0.05") else " ✖")
        return f"{d * 100:.1f}%{mark}"

    unit = "年" if zh() else "y"
    cols = [f"{h} {unit}" for h in sens.horizons]
    rows = [f"{('倍数' if zh() else 'multiple')} {m}" for m in sens.multiples]
    df = pd.DataFrame([[cell(v) for v in row] for row in sens.required_cagr], index=rows, columns=cols)
    a_row = sens.multiples.index(sens.assumed_multiple) if sens.assumed_multiple in sens.multiples else None
    a_col = sens.horizons.index(sens.assumed_horizon) if sens.assumed_horizon in sens.horizons else None
    # no red: within reach is green, a stretch is pale amber, far out of reach is strong amber
    tone = {"✔": "rgba(116,255,154,0.16)", "◐": "rgba(255,178,62,0.14)", "✖": "rgba(255,178,62,0.32)"}

    def style(frame: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for rr in range(frame.shape[0]):
            for cc in range(frame.shape[1]):
                css = next((f"background-color: {col}" for sym, col in tone.items() if frame.iat[rr, cc].endswith(sym)), "")
                if rr == a_row and cc == a_col:
                    css += f"; font-weight: 700; border: 2px solid {th['accent']}"
                elif rr == a_row or cc == a_col:
                    css += "; font-weight: 600"
                out.iat[rr, cc] = css
        return out

    st.dataframe(df.style.apply(style, axis=None))
    st.caption(t("sens_note", b=f"{fmt_amount(sens.base_value)} ({sens.base_label})", h=fmt_pct(hist) if hist is not None else "—"))


def render_calculations(result: CaseResult) -> None:
    html_block(f'<div class="tc-h">{esc(t("calcs"))}</div>')
    st.caption(t("calc_note"))
    i = 0 if zh() else 1
    rows = [
        {
            ("项目" if zh() else "Item"): CALC.get(c.name, (c.name, c.name))[i],
            ("数值" if zh() else "Value"): fmt_calc(c),
            ("原始值" if zh() else "Exact value"): "" if c.value is None else format(c.value, "f"),
            ("公式" if zh() else "Formula"): c.formula,
            ("输入" if zh() else "Inputs"): json.dumps(c.inputs, ensure_ascii=False),
            ("假设/说明" if zh() else "Assumptions / notes"): "; ".join(c.assumptions + ([c.reason] if c.reason else [])),
        }
        for c in result.calculations
    ]
    st.dataframe(rows, hide_index=True)
    with st.expander(t("provenance")):
        st.dataframe([{("字段" if zh() else "Field"): label_of(k), ("来源" if zh() else "Source"): v}
                      for k, v in result.confirmed_claim.value_provenance.items()], hide_index=True)


def tab_fundamentals(result: CaseResult) -> None:
    st.caption(t("f_intro"))
    render_verdict(result)
    render_key_numbers(result)
    if result.verdict is not None:
        render_conclusion(result)
    render_evidence(result)
    render_calculations(result)
    render_sensitivity(result)
    with st.expander(t("verdict_kinds")):
        st.markdown(t("verdict_kinds_body"))


# ------------------------------------------------------------------ report channel 5: raw data


def _rule(value: float, text: str, color: str) -> alt.Chart:
    df = pd.DataFrame({"v": [value], "t": [text]})
    rule = alt.Chart(df).mark_rule(strokeWidth=1, strokeDash=[4, 3], color=color).encode(y="v:Q")
    label = alt.Chart(df).mark_text(align="left", dx=4, dy=-6, color=color, fontSize=11).encode(y="v:Q", text="t:N", x=alt.value(0))
    return rule + label


def tab_raw(result: CaseResult, key_suffix: str) -> None:
    th = chart_colors()
    facts, market = result.reported_facts, result.market
    claim = result.confirmed_claim.values
    axis = dict(labelColor=th["muted"], titleColor=th["muted"], gridColor=th["grid"], domain=False)
    if facts is not None:
        html_block(f'<div class="tc-h">{esc(t("facts_title"))} · {esc(facts.company_name or facts.ticker)}</div>')
        is_ps = claim.valuation_method.value == "price_to_sales"
        series = (facts.revenue if is_ps else facts.net_income)[-6:]
        required = next((c.value for c in result.calculations if c.name in ("required_annual_revenue", "required_annual_net_income")), None)
        if series:
            label = ("年营收" if is_ps else "年净利润") if zh() else ("Annual revenue" if is_ps else "Annual net income")
            df = pd.DataFrame({"FY": [f"FY{p.period_end.year}" for p in series], "v": [float(p.value) for p in series],
                               "end": [p.period_end.isoformat() for p in series], "filed": [f"{p.form} {p.filed}" for p in series]})
            chart = alt.Chart(df).mark_bar(size=26, color=th["accent"]).encode(
                x=alt.X("FY:N", title=None, sort=None, axis=alt.Axis(labelAngle=0, labelColor=th["muted"], domain=False, ticks=False)),
                y=alt.Y("v:Q", title=f"{label} (USD)", axis=alt.Axis(format="~s", **axis)),
                tooltip=[alt.Tooltip("FY:N"), alt.Tooltip("v:Q", format=",.0f"), alt.Tooltip("end:N"), alt.Tooltip("filed:N")],
            )
            peak = max(float(p.value) for p in series)
            if required is not None and float(required) <= 5 * peak:
                chart = chart + _rule(float(required), ("观点所需 " if zh() else "Claim needs ") + fmt_amount(required), th["second"])
            st.altair_chart(finish(chart.properties(height=240)), width="stretch")
            if required is not None and float(required) > 5 * peak:
                st.caption(f"观点所需 {fmt_amount(required)}，超过图中最大值 5 倍，未画参考线。" if zh()
                           else f"The claim needs {fmt_amount(required)}, more than 5x the chart's maximum; no line drawn.")
        rows = []
        for kzh, ken, pts in (("营收", "Revenue", facts.revenue), ("净利润", "Net income", facts.net_income), ("股份数", "Shares", facts.shares_outstanding)):
            for p in pts[-5:]:
                rows.append({"metric": kzh if zh() else ken, "period_end": p.period_end.isoformat(), "value": fmt_amount(p.value),
                             "concept": p.concept, "form": p.form, "filed": p.filed.isoformat(), "accession": p.accession})
        with st.expander(t("data_table")):
            st.dataframe(rows, hide_index=True)
            st.caption(facts.source_url)
    if market is not None:
        html_block(f'<div class="tc-h">{esc(t("price_title"))} · {esc(market.symbol)}</div>')
        vol = fmt_pct(market.annualized_volatility) if market.annualized_volatility is not None else "—"
        close = f"{float(market.last_close):,.2f}"
        st.caption(f"最新收盘 {close}（{market.last_date}），历史年化波动率 {vol}；K 线见 UNIT 02。" if zh()
                   else f"Latest close {close} ({market.last_date}); historical volatility {vol}. The K-line is in UNIT 02.")
    if result.coverage is not None:
        html_block(f'<div class="tc-h">{esc(t("filings_title"))}</div>')
        (st.warning if result.coverage.coverage_gap else st.caption)(pick(result.coverage.message, result.coverage.message_zh))
    if result.evidence_records:
        rows = [{"form": r.form_type, "filed": r.filing_date.isoformat(), "period": r.report_date.isoformat() if r.report_date else "",
                 "document": r.document_url or "", "index": r.filing_index_url, "data_mode": r.data_mode.value}
                for r in result.evidence_records]
        st.dataframe(rows, hide_index=True, column_config={"document": st.column_config.LinkColumn(), "index": st.column_config.LinkColumn()})
        st.caption(t("filings_note"))
    if facts is None and market is None and not result.evidence_records:
        st.info(t("no_public"))
    with st.expander(t("prob_model")):
        render_model_probability(result)
    html_block(f'<div class="tc-h">{esc(t("run_log"))}</div>')
    status = STATUS[result.status.value][0 if zh() else 1]
    st.caption(f"case_id {result.case_id} · {result.created_at.isoformat()} · {status}")
    st.json(result.data_modes, expanded=False)
    notes = notes_of(result)
    if notes:
        st.markdown(f"**{t('notes', n=len(notes))}**")
        for w in notes:
            st.warning(w)
    if result.provider_errors:
        st.markdown(f"**{t('data_errors')}**")
        for e in result.provider_errors:
            st.error(f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") + f": {e.message}")
    if result.missing_fields:
        st.markdown(f"**{t('missing_items')}**")
        for m in result.missing_fields:
            st.write("- " + missing_text(m))
    st.caption(result.disclaimer)
    st.download_button(t("download"), result.model_dump_json(indent=2), file_name=f"tickercase_{result.case_id}.json",
                       mime="application/json", key=f"dl_{key_suffix}")


def render_model_probability(result: CaseResult) -> None:
    p = result.probability
    st.caption(t("prob_extra"))
    if p is None:
        st.caption(t("prob_off"))
        return
    if p.status != "ok":
        st.warning(t("prob_na") + pick(p.reason or "", p.reason_zh or ""))
        return
    html_block('<div class="tc-kpis">'
               + kpi((f"到期价 ≥ {p.target_price} 的概率" if zh() else f"P(price ≥ {p.target_price})"), f"{p.target_probability * 100:.1f}%")
               + kpi("中位数价格" if zh() else "Median price", f"{p.median_price:,.2f}")
               + kpi("10% 分位价格" if zh() else "10th percentile", f"{p.p10_price:,.2f}")
               + kpi("90% 分位价格" if zh() else "90th percentile", f"{p.p90_price:,.2f}") + "</div>")
    st.markdown(f"**{t('assumptions')}**")
    for a in (p.assumptions_zh if zh() and p.assumptions_zh else p.assumptions):
        st.markdown(f"- {a}")
    st.markdown(f"**{t('limitations')}**")
    for a in (p.limitations_zh if zh() and p.limitations_zh else p.limitations):
        st.markdown(f"- {a}")


# ------------------------------------------------------------------ report page units


def render_kline(result: CaseResult) -> None:
    v = result.confirmed_claim.values
    m = result.market
    has_data = m is not None and (m.price_series or (m.kline is not None and m.kline.daily))
    html_block(ck.unit_head(t("u_kln", tk=v.ticker), ck.chip(t("kln_src", d=m.last_date)) if has_data else "", "u-kln"))
    if not has_data:
        st.info(t("kln_none"))
        return
    touch_text, end_text = ck.kline_notes(result, zh())
    st.iframe(kline_html(m, target=float(v.target_price), deadline=ck.deadline_of(result), zh=zh(), ticker=v.ticker, last_day=m.last_date,
                         touch_text=touch_text, end_text=end_text, anim=False, phosphor=st.session_state["tube"]), height=KLINE_HEIGHT)


def render_nar(result: CaseResult, key_suffix: str, anim: bool) -> None:
    n = result.narratives.get(lang())
    if n is None and result.narrative is not None and result.narrative.language == "both":  # written before v0.8
        n = result.narrative
    settings = get_service().settings
    model_kw = dict(model=settings.narrative_model, effort=settings.narrative_effort)
    if n is None or n.status != "ok":
        html_block(ck.unit_head(t("u_nar"), "", "u-nar") + ck.para(t("ai_intro")))
        if n is not None:
            st.warning(t("ai_failed") + (n.error or n.status))
        no_key = not settings.anthropic_api_key
        if no_key:
            st.caption(t("ai_missing"))
            c = st.columns([3, 1], vertical_alignment="bottom")
            c[0].text_input(t("ai_key"), key="ai_key_input", type="password", placeholder="sk-ant-...")
            c[1].button(t("ai_save"), key="btn_ai_save", on_click=_save_ai_key, width="stretch")
            show_msg("ai_msg")
        st.button(t("ai_button") if n is None else t("ai_retry"), key=f"btn_narrate_{key_suffix}", help=t("ai_button_help", **model_kw),
                  on_click=_narrate, args=(key_suffix,), type="primary", disabled=no_key)
        st.caption(t("ai_button_help", **model_kw))
        return
    summary = t("ai_summary", ok=n.verified, total=n.total, bad=n.unsupported, model=n.model + (f" · effort {n.effort}" if n.effort else ""))
    cost = estimate_cost(n.model, n.usage)
    if n.reused_from:
        summary += " · " + t("ai_reused", case=n.reused_from[:8])
    elif cost is not None:
        summary += " · " + t("ai_usage", inp=n.usage.get("input_tokens", 0), out=n.usage.get("output_tokens", 0), cost=cost)
    html_block(ck.unit_head(t("u_nar"), ck.chip(summary), "u-nar") + ck.nar_paper(n, zh(), anim))
    st.caption(t("ai_legend"))
    with st.expander(t("ai_facts")):
        for k, x in [(k, x) for k, v in n.sections.items() for x in v if x.problems]:
            st.markdown(f"- {NARR_BADGE[x.status]} **{ck.L(zh(), *ck.NARR_TITLES.get(k, (k, k)))}**: {'；'.join(x.problems)}")
        st.dataframe([{"id": f.id, "label": f.label_zh if zh() else f.label_en, "value": f.value, "unit": f.unit, "source": f.source} for f in n.facts],
                     hide_index=True)
    st.button(t("ai_retry"), key=f"btn_narrate_{key_suffix}", on_click=_narrate, args=(key_suffix, True), help=t("ai_retry_help", **model_kw))


def render_result_units(result: CaseResult, key_suffix: str, *, anim: bool, start: int = 1) -> list[tuple[str, int]]:
    """The report units in order; returns (container key, junction number) for the data bus."""
    if result.validation_issues:
        for i in result.validation_issues:
            st.error(issue_text(i))
        return []
    if result.confirmed_claim is None:
        return []
    z = zh()
    keys: list[str] = []
    with st.container(key="u-c-status-bar"):
        html_block(ck.status_bar(result, z, [j for j in JUMPS if result.oracle is not None or j[0] != "u-prb"]))
    with st.container(key="u-b-clm"):
        html_block(ck.claim_unit(result, z, t("u_clm_r")))
    keys.append("u-b-clm")
    with st.container(key="u-c-kln"):
        render_kline(result)
    keys.append("u-c-kln")
    if result.oracle is not None:
        with st.container(key="u-c-prb"):
            html_block(ck.prb_unit(result, z, t("u_prb"), anim))
        keys.append("u-c-prb")
    with st.container(key="u-o-feed"):
        html_block(ck.feeds_unit(result, z, t("u_feed_r"), anim))
    keys.append("u-o-feed")
    with st.container(key="u-c-rpt"):
        html_block(ck.unit_head(t("u_rpt"), ck.chip(t("rpt_note")), "u-rpt"))
        tabs = st.tabs(S["tabs"][0 if z else 1])
        with tabs[0]:
            render_report(result.report)
        with tabs[1]:
            if result.oracle is not None:
                tab_scenario(result, key_suffix)
            else:
                st.info(t("sc_need"))
        with tabs[2]:
            if result.oracle is not None:
                tab_levels(result)
            else:
                st.caption(t("r_none"))
        with tabs[3]:
            tab_fundamentals(result)
        with tabs[4]:
            tab_raw(result, key_suffix)
    keys.append("u-c-rpt")
    left, right = st.columns([2, 1])
    with left, st.container(key="u-c-sig"):
        html_block(ck.sig_unit(result, z, t("u_sig"), notes=notes_of(result)))
    with right, st.container(key="u-r-fnd"):
        html_block(ck.fnd_unit(result, z, t("u_fnd"), t("fnd_more")))
    keys.append("u-c-sig")
    with st.container(key="u-b-nar"):
        render_nar(result, key_suffix, anim)
    keys.append("u-b-nar")
    return [(k, start + i) for i, k in enumerate(keys)]


def view_result(result: CaseResult) -> None:
    anim = result.case_id not in st.session_state["animated"]  # first view of a new report plays its motion once
    st.session_state["animated"].add(result.case_id)
    bridge_unit(result_page=True, warn=len(ck.warnings_of(result, zh())))
    nodes = render_result_units(result, "current", anim=anim)
    footer_unit()
    st.html(ck.page_css(nodes))


# ------------------------------------------------------------------ history page


def view_history() -> None:
    bridge_unit(result_page=False, warn=0)
    store = get_service().store
    cases = store.list_cases() if store is not None else []

    def prob_text(c: CaseResult) -> str:
        o = c.oracle
        if o is None or o.low is None:
            return "—"
        return f"{pct_p(o.low)} – {pct_p(o.high)}"

    def verdict_text(c: CaseResult) -> str:
        if c.verdict is None:
            return "—"
        return (c.verdict.display_zh or VERDICT_DISPLAY_ZH[c.verdict.label]) if zh() else c.verdict.display

    selected: list[CaseResult] = []
    with st.container(key="u-c-hist"):
        html_block(ck.unit_head(t("u_hist_all"), ck.chip(t("history_hint")), "u-hist"))
        if not cases:
            st.info(t("history_empty"))
        else:
            rows = []
            for c in cases:
                v = c.confirmed_claim.values if c.confirmed_claim else None
                rows.append({
                    ("时间" if zh() else "Time"): c.created_at.strftime("%Y-%m-%d %H:%M"),
                    ("代码" if zh() else "Ticker"): v.ticker if v else "—",
                    ("观点" if zh() else "Claim"): v.claim_text if v else "—",
                    ("概率" if zh() else "Probability"): prob_text(c),
                    ("基本面结论" if zh() else "Fundamentals"): verdict_text(c),
                    ("数据" if zh() else "Data"): MODES.get(c.data_modes.get("sec_filings", ""), ("—", "—"))[0 if zh() else 1]
                    if c.data_modes.get("sec_filings") in MODES else c.data_modes.get("sec_filings", "—"),
                })
            event = st.dataframe(rows, hide_index=True, on_select="rerun", selection_mode="multi-row", key="history_table")
            selected = [cases[i] for i in (event.selection.rows if event and event.selection else [])]
            if selected:
                st.session_state["open_case"] = None
            elif st.session_state["open_case"]:  # a tape played from the console
                selected = [c for c in cases if c.case_id == st.session_state["open_case"]][:1]
    nodes = [("u-c-hist", 1)]
    if len(selected) == 1:
        st.session_state["history_selected"] = selected[0]
        nodes += render_result_units(selected[0], selected[0].case_id, anim=False, start=2)
    elif len(selected) >= 2:
        tbl = {}
        for c in selected:
            v = c.confirmed_claim.values if c.confirmed_claim else None
            calc = {x.name: x for x in c.calculations}
            e1 = next((i for i in c.evidence_items if i.id == "E1"), None)
            req_metric = calc.get("required_annual_revenue") or calc.get("required_annual_net_income")
            tbl[f"{c.created_at:%m-%d %H:%M} {v.ticker if v else ''}"] = {
                ("观点" if zh() else "Claim"): v.claim_text if v else "—",
                ("概率" if zh() else "Probability"): prob_text(c),
                ("条件" if zh() else "Condition"): (t("cond_touch") if c.oracle and c.oracle.condition == "touch" else t("cond_end")) if c.oracle else "—",
                ("目标价" if zh() else "Target"): format(v.target_price, "f") if v else "—",
                ("参考价" if zh() else "Reference"): f"{format(v.reference_price, 'f')} ({v.reference_price_date})" if v else "—",
                ("时间（年）" if zh() else "Horizon"): format(v.horizon_years, "f") if v else "—",
                ("估值" if zh() else "Valuation"): f"{'P/S' if v.valuation_method.value == 'price_to_sales' else 'P/E'} {format(v.valuation_multiple, 'f')}" if v else "—",
                ("所需年指标" if zh() else "Required annual metric"): fmt_calc(req_metric) if req_metric else "—",
                ("所需增速" if zh() else "Required growth"): fmt_pct(Decimal(e1.measured["required_cagr_from_reported"])) if e1 and "required_cagr_from_reported" in e1.measured else "—",
                ("已披露增速" if zh() else "Reported growth"): fmt_pct(Decimal(e1.measured["reported_cagr"])) if e1 and "reported_cagr" in e1.measured else "—",
                ("基本面结论" if zh() else "Fundamentals"): verdict_text(c),
            }
        with st.container(key="u-c-compare"):
            html_block(ck.unit_head(t("compare")))
            st.dataframe(pd.DataFrame(tbl))
        nodes.append(("u-c-compare", 2))
    footer_unit()
    st.html(ck.page_css(nodes))


# ------------------------------------------------------------------ page


def view_new() -> None:
    draft = current_draft()
    result: Optional[CaseResult] = st.session_state["result"]
    current = (result is not None and result.confirmed_claim is not None and result.input_fingerprint == fingerprint(draft)
               and st.session_state["result_mode"] == st.session_state["sec_mode"])
    if current and st.session_state["page"] == "result":
        view_result(result)
    else:
        view_home(current)


def main() -> None:
    st.set_page_config(page_title="TickerCase", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
    _init_state()
    _keep_widget_state()
    st.html(ck.CSS)
    if st.session_state["tube"] == "amb":
        st.html(ck.AMBER_CSS)
    if st.session_state["view"] == "history":
        view_history()
    else:
        view_new()


main()
