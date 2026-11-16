"""Markup of the simple view (v0.16.0): the same casing, screens and keys as the console, without the instrument labels.

The functions return HTML strings; app.py places them and owns every widget. The words come from ``plain.py``.
"""

from __future__ import annotations

from typing import Optional

from ..plain import Headline, Reason, Restated, pct
from .cockpit import L, esc

CSS = """<style>
.tcs-top{display:flex;flex-direction:column;gap:2px}
.tcs-wm{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:700;letter-spacing:.24em;color:#F1EBDC;text-shadow:0 2px 0 var(--ink)}
.tcs-sub{font-family:'Noto Serif SC',serif;font-size:14px;font-weight:700;letter-spacing:.12em;color:#CFC4A6}
.tcs-h1{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(26px,3vw,40px);line-height:1.3;font-weight:900;color:var(--ink);margin:4px 0 2px}
.tcs-lead{font-family:'Noto Serif SC',Georgia,serif;font-size:18px;line-height:1.6;color:#3A3327;margin:0 0 10px}
.tcs-h2{font-family:'Noto Serif SC',Georgia,serif;font-size:26px;font-weight:900;color:var(--ink);margin:0}
.tcs-three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.tcs-card{background:rgba(255,250,235,.28);border:2px solid var(--ink);padding:12px 14px;font-family:'Noto Serif SC',Georgia,serif;font-size:16px;line-height:1.6;color:var(--ink)}
.tcs-card b{display:block;font-family:'IBM Plex Mono',monospace;font-size:13px;letter-spacing:.1em;color:#6B4E22;margin-bottom:4px}
.tcs-say{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(22px,2.3vw,30px);line-height:1.55;color:var(--scr-text)}
.tcs-say em{font-style:normal;color:var(--am);border-bottom:2px dashed var(--am)}
.tcs-sayline{font-size:18px;line-height:1.5;color:var(--scr-text);margin-top:10px}
.tcs-steps{display:flex;flex-direction:column;gap:10px;font-family:'Noto Serif SC',Georgia,serif;font-size:19px}
.tcs-step{display:flex;gap:12px;align-items:baseline}
.tcs-step.ok{color:var(--ph)}.tcs-step.run{color:var(--am)}.tcs-step.wait{color:var(--scr-line)}.tcs-step.bad{color:#FF8A7A}
.tcs-step i{font-style:normal;width:1.2em;display:inline-block}
.tcs-bar{height:12px;background:var(--z2);border:1px solid var(--scr-line);margin-top:6px}
.tcs-bar span{display:block;height:100%;background:var(--ph);transition:width .3s steps(4,end)}
.tcs-claim{font-size:17px;line-height:1.5;color:var(--dim)}
.tcs-verdict{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(26px,2.8vw,36px);line-height:1.4;font-weight:900;color:var(--scr-text)}
.tcs-verdict b{color:var(--ph)}
.tcs-scale{position:relative;height:34px;background:var(--z2);border:1px solid var(--scr-line);margin:18px 0 6px}
.tcs-scale .rng{position:absolute;top:0;bottom:0;background:var(--ph);opacity:.9;min-width:6px}
.tcs-scale .half{position:absolute;left:50%;top:-8px;bottom:-8px;border-left:2px dashed var(--am)}
.tcs-ticks{display:flex;justify-content:space-between;font-size:15px;color:var(--dim)}
.tcs-note{font-size:18px;line-height:1.6;color:var(--scr-text);margin-top:12px}
.st-key-u-b-cnf .tc-bezel,.st-key-u-c-ans .tc-bezel{margin-bottom:6px}
.tcs-why{list-style:none;margin:0;padding:0;font-family:'Noto Serif SC',Georgia,serif;font-size:17px;line-height:1.65;color:var(--ink)}
.tcs-why li{padding:10px 0;border-bottom:1px dashed #8D8163;display:flex;gap:12px}
.tcs-why .s{flex:0 0 auto;font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;white-space:nowrap;padding-top:3px}
.tcs-why .p{color:#1F6B3A}.tcs-why .m{color:#A2401F}.tcs-why .n{color:#6B5F44}
.tcs-warn{font-family:'Noto Serif SC',Georgia,serif;font-size:15px;line-height:1.6;background:rgba(255,246,214,.6);border:2px solid #A2401F;color:#7A2D14;padding:8px 12px}
.tcs-assume{font-family:'Noto Serif SC',Georgia,serif;font-size:16px;line-height:1.7;color:var(--ink);margin:0;padding-left:1.2em}
@media (max-width:900px){.tcs-three{grid-template-columns:1fr}}
</style>"""


def bridge(zh: bool) -> str:
    return (f'<div class="tcs-top"><span class="tcs-wm">TICKERCASE</span>'
            f'<span class="tcs-sub">{esc(L(zh, "股票说法 · 可能性检查", "How likely is that stock claim?"))}</span></div>')


def start_head(zh: bool) -> str:
    return (f'<h1 class="tcs-h1">{esc(L(zh, "有人说某只股票会涨到多少？", "Someone says a stock will reach a certain price?"))}<br>'
            f'{esc(L(zh, "输入这句话，看看它成真的可能性有多大。", "Type the sentence in and see how likely it is."))}</h1>'
            f'<p class="tcs-lead">{esc(L(zh, "不用懂股票。我们查公开数据，给你一个可能性的范围和理由。", "No stock knowledge needed. We check public data and give you a range and the reasons."))}</p>')


def cards(zh: bool) -> str:
    items = [
        (L(zh, "① 你说一句话", "1 · You say one sentence"),
         L(zh, "写公司名或代码、目标价格和时间，像聊天一样写就行。", "Name the company, the price and the time, in your own words.")),
        (L(zh, "② 我们查公开数据", "2 · We check public data"),
         L(zh, "公司财报、股价历史、期权市场的定价，都来自免费的官方或公开来源。", "Company filings, price history and option prices, all from free official or public sources.")),
        (L(zh, "③ 给你范围和理由", "3 · You get a range and reasons"),
         L(zh, "告诉你大概有百分之几的可能，以及支持和反对的理由。不是买卖建议。", "A percentage range, with what speaks for and against it. Not advice to buy or sell.")),
    ]
    return '<div class="tcs-three">' + "".join(f'<div class="tcs-card"><b>{esc(a)}</b>{esc(b)}</div>' for a, b in items) + "</div>"


def crt(inner: str) -> str:
    return f'<div class="tc-bezel"><div class="tc-crt"><span class="tc-noise"></span><div class="tc-crt-in tc-ph" style="gap:8px">{inner}</div></div></div>'


def confirm_sentence(zh: bool, r: Restated) -> str:
    """The claim as it was read, in one sentence; the four things that can be changed are underlined."""
    who = f"{r.company}（{r.ticker}）" if (r.company and zh) else f"{r.company} ({r.ticker})" if r.company else r.ticker
    price = f"{r.target} {r.currency}"
    day = r.deadline or L(zh, "（没读到期限）", "(no deadline found)")
    em = lambda x: f"<em>{esc(x)}</em>"  # noqa: E731
    if zh:
        if r.touch:
            say = f"{em(who)} 的股价，在 {em(day)} 之前，{em('至少有一天')}达到 {em(price)}。"
        else:
            say = f"{em(who)} 的股价，在 {em(day)} {em('那一天收盘时')}，不低于 {em(price)}。"
    else:
        if r.touch:
            say = f"{em(who)} trades at {em(price)} or higher on {em('at least one day')} before {em(day)}."
        else:
            say = f"{em(who)} {em('closes')} at {em(price)} or higher on {em(day)}."
    line = ""
    if r.reference and r.needed is not None:
        if r.needed > 0:
            line = L(zh, f"现在的价格是 {r.reference} {r.currency}，也就是说还要涨 {pct(r.needed)}。",
                     f"The price today is {r.reference} {r.currency}, so it needs to rise {pct(r.needed)}.")
        else:
            line = L(zh, f"现在的价格是 {r.reference} {r.currency}，已经不低于这个目标价。",
                     f"The price today is {r.reference} {r.currency}, already at or above this target.")
    return crt(f'<div class="tcs-say">{say}</div>' + (f'<div class="tcs-sayline">{esc(line)}</div>' if line else ""))


# the fourteen data steps of a run, said as five things a person would recognise
WORK_GROUPS = [
    (("读这家公司的财报和公告", "Reading the company's filings"), ("sec_filings", "sec_facts", "insiders")),
    (("看过去的股价", "Looking at past prices"), ("price_history", "price_base_rate", "benchmarks", "risk_free_rate")),
    (("看期权市场怎么给这件事定价", "Checking how the options market prices it"), ("options", "earnings_date")),
    (("看新闻和市场情绪", "Looking at headlines and market mood"), ("prediction_markets", "fear_greed", "news")),
    (("对比规模相近的公司，汇总成一个范围", "Comparing with similar companies and putting it together"), ("base_rate", "market_structure")),
]


def working(steps: dict, zh: bool, labels: Optional[dict] = None) -> str:
    """The run as five lines; a line is done when all of its data steps have ended."""
    rows, done, total = [], 0, 0
    for (zh_text, en_text), keys in WORK_GROUPS:
        states = [steps.get(k) for k in keys]
        ended = [s for s in states if s in ("ok", "failed")]
        total += len(keys)
        done += len(ended)
        if len(ended) == len(keys):
            cls, mark = ("bad", "!") if all(s == "failed" for s in states) else ("ok", "✔")
        elif any(s is not None for s in states):
            cls, mark = "run", "▸"
        else:
            cls, mark = "wait", "·"
        text = L(zh, zh_text, en_text) + ("…" if cls == "run" else "")
        if cls == "bad":
            text += L(zh, "（这部分没取到）", " (this part could not be fetched)")
        rows.append(f'<div class="tcs-step {cls}"><i>{mark}</i><span>{esc(text)}</span></div>')
    share = round(100 * done / total) if total else 0
    head = L(zh, "正在查数据，通常需要 20 到 30 秒", "Checking the data; this usually takes 20 to 30 seconds")
    return crt(f'<div class="tcs-claim">{esc(head)}</div><div class="tcs-steps">{"".join(rows)}</div><div class="tcs-bar"><span style="width:{share}%"></span></div>')


def answer_screen(zh: bool, h: Headline, claim: tuple[str, str], why: Optional[tuple[str, str]], other: Optional[tuple[str, str]]) -> str:
    i = 1 if zh else 0
    if h.low is None:
        verdict = f'<div class="tcs-verdict">{esc(h.word[i])}{L(zh, "：", ": ")}{esc(h.range_text[i])}{L(zh, "。", ".")}</div>'
        scale = ""
    else:
        verdict = f'<div class="tcs-verdict">{esc(h.word[i])}{L(zh, "：", ":")}<br><b>{esc(h.range_text[i])}</b>{L(zh, "。", ".")}</div>'
        left, width = 100 * h.low, max(100 * (h.high - h.low), 0.8)
        scale = (f'<div class="tcs-scale"><span class="rng" style="left:{left:.1f}%;width:{width:.1f}%"></span><span class="half"></span></div>'
                 f'<div class="tcs-ticks"><span>{esc(L(zh, "0% 几乎不可能", "0% almost never"))}</span><span>{esc(L(zh, "50% 像抛硬币", "50% a coin flip"))}</span>'
                 f'<span>{esc(L(zh, "100% 几乎肯定", "100% almost sure"))}</span></div>')
    notes = "".join(f'<div class="tcs-note">{esc(x[i])}</div>' for x in (why, other) if x)
    return crt(f'<div class="tcs-claim">{esc(claim[i])}</div>{verdict}{scale}{notes}')


def reasons(zh: bool, items: list[Reason]) -> str:
    if not items:
        return f'<p class="tcs-lead">{esc(L(zh, "这次没有足够的数据列出理由。", "There was not enough data to list reasons this time."))}</p>'
    word = {"+": (L(zh, "＋ 支持", "+ For"), "p"), "-": (L(zh, "－ 反对", "− Against"), "m"), "0": (L(zh, "· 中性", "· Neutral"), "n")}
    rows = "".join(f'<li><span class="s {word[r.sign][1]}">{esc(word[r.sign][0])}</span><span>{esc(r.text[1 if zh else 0])}</span></li>' for r in items)
    return f'<ul class="tcs-why">{rows}</ul>'


def assumptions(zh: bool, items: list[tuple[str, str]]) -> str:
    return '<ul class="tcs-assume">' + "".join(f"<li>{esc(x[1 if zh else 0])}</li>" for x in items) + "</ul>"


def warn(text: str) -> str:
    return f'<div class="tcs-warn">{esc(text)}</div>'
