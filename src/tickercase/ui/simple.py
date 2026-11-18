"""Markup of the simple view (v0.16.0): the same casing, screens and keys as the console, without the instrument labels.

The functions return HTML strings; app.py places them and owns every widget. The words come from ``plain.py``.

Since v0.19.0 the page moves: the main number counts up while its marker slides along the bar, one dot per method
drops onto the bar and flies to the marker, and the reasons are cards. Everything is CSS (the page takes no script);
each sentence that was small print is still in the page, under a ⓘ that opens in place. The console's style sheet
switches every animation off for a reader who asked the system for less motion, and each element rests in its final
state without its animation.
"""

from __future__ import annotations

from typing import Optional

from ..plain import Chip, EventCard, Headline, Part, Reason, Restated, pct, rate
from .cockpit import L, esc

CSS = """<style>
@property --tcs-n{syntax:'\\3C integer>';inherits:false;initial-value:0}
.tcs-top{display:flex;flex-direction:column;gap:2px}
.tcs-wm{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:700;letter-spacing:.24em;color:#F1EBDC;text-shadow:0 2px 0 var(--ink)}
.tcs-sub{font-family:'Noto Serif SC',serif;font-size:14px;font-weight:700;letter-spacing:.12em;color:#CFC4A6}
.tcs-h1{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(26px,3vw,40px);line-height:1.3;font-weight:900;color:var(--ink);margin:4px 0 2px}
.tcs-cur{display:inline-block;margin-left:4px;color:#A2401F;animation:tcs-blink 1s steps(1,end) infinite}
.tcs-lead{font-family:'Noto Serif SC',Georgia,serif;font-size:18px;line-height:1.6;color:#3A3327;margin:0 0 10px}
.tcs-h2{font-family:'Noto Serif SC',Georgia,serif;font-size:26px;font-weight:900;color:var(--ink);margin:0}
.tcs-three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.tcs-card{background:rgba(255,250,235,.28);border:2px solid var(--ink);padding:12px 14px;font-family:'Noto Serif SC',Georgia,serif;font-size:17px;line-height:1.5;color:var(--ink);
  display:flex;gap:12px;align-items:center;box-shadow:4px 4px 0 var(--ink);animation:tcs-in .6s cubic-bezier(.2,.9,.3,1.3) both,tcs-float 6s ease-in-out 1s infinite;transition:translate .18s,box-shadow .18s}
.tcs-card:hover{translate:-2px -4px;box-shadow:8px 9px 0 var(--ink)}
.tcs-card i{flex:0 0 auto;font-style:normal;font-family:'IBM Plex Mono',monospace;font-size:34px;font-weight:700;line-height:1;color:#A2401F}
.tcs-card b{display:block;font-size:18px;font-weight:900}
.tcs-card span{display:block;font-size:14px;color:#5C5340}
.tcs-say{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(22px,2.3vw,30px);line-height:1.55;color:var(--scr-text)}
.tcs-say em{font-style:normal;color:var(--am);border-bottom:2px dashed var(--am)}
.tcs-sayline{font-size:18px;line-height:1.5;color:var(--scr-text);margin-top:10px}
.tcs-steps{display:flex;flex-direction:column;gap:10px;font-family:'Noto Serif SC',Georgia,serif;font-size:19px}
.tcs-step{display:flex;gap:12px;align-items:baseline}
.tcs-step.ok{color:var(--ph)}.tcs-step.run{color:var(--am)}.tcs-step.wait{color:var(--scr-line)}.tcs-step.bad{color:#FF8A7A}
.tcs-step i{font-style:normal;width:1.2em;display:inline-block}
.tcs-step.run i{animation:tcs-nudge .7s ease-in-out infinite}
.tcs-bar{height:12px;background:var(--z2);border:1px solid var(--scr-line);margin-top:6px;overflow:hidden}
.tcs-bar span{display:block;height:100%;background:repeating-linear-gradient(115deg,var(--ph) 0 10px,rgba(0,0,0,.28) 10px 14px);background-size:28px 100%;
  transition:width .3s steps(4,end);animation:tcs-stripes .7s linear infinite}
.tcs-claim{font-size:17px;line-height:1.5;color:var(--dim)}
.tcs-sweep{position:absolute;left:0;right:0;top:0;height:28%;z-index:1;pointer-events:none;opacity:0;
  background:linear-gradient(to bottom,transparent,rgba(160,255,190,.07) 60%,rgba(160,255,190,.13) 96%,transparent);animation:tcs-sweep 7s linear 1.2s infinite}
.tcs-verdict{font-family:'Noto Serif SC',Georgia,serif;font-size:clamp(26px,2.8vw,36px);line-height:1.4;font-weight:900;color:var(--scr-text)}
.tcs-verdict b{color:var(--ph)}
.tcs-typed{display:inline-block}
.tcs-big{display:inline-block;font-size:clamp(44px,6vw,76px);line-height:1.1;text-shadow:0 0 18px color-mix(in srgb,var(--ph) 55%,transparent);animation:tcs-glow 2.6s ease-in-out 2.8s infinite}
.tcs-num{--tcs-n:var(--to);counter-reset:tcsn var(--tcs-n);animation:tcs-count 1.7s cubic-bezier(.15,.7,.2,1) .9s both}
.tcs-num::after{content:counter(tcsn) "%"}
.tcs-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.tcs-scale{position:relative;height:34px;background:var(--z2);border:1px solid var(--scr-line);margin:64px 0 6px}
.tcs-scale .rng{position:absolute;top:-6px;bottom:-6px;width:10px;margin-left:-5px;background:var(--ph);box-shadow:0 0 14px var(--ph);z-index:3;
  animation:tcs-slide 1.7s cubic-bezier(.15,.7,.2,1) .9s both}
.tcs-scale .spr{position:absolute;top:0;bottom:0;background:var(--ph);opacity:.2;animation:tcs-fade .8s ease-out 1s both}
.tcs-scale .half{position:absolute;left:50%;top:-8px;bottom:-8px;border-left:2px dashed var(--scr-line)}
.tcs-dot{position:absolute;top:50%;width:var(--s);height:var(--s);margin:calc(var(--s) / -2) 0 0 calc(var(--s) / -2);border-radius:50%;z-index:2;
  border:2px solid var(--ph);background:#0A0D0A;animation:tcs-drop .55s cubic-bezier(.3,1.6,.5,1) both}
.tcs-dot em{position:absolute;bottom:calc(100% + var(--up));left:50%;transform:translateX(-50%);white-space:nowrap;font-style:normal;font-size:15px;line-height:1;color:var(--scr-text)}
.tcs-dot em::after{content:"";position:absolute;left:50%;top:100%;height:calc(var(--up) + 2px);border-left:1px solid var(--scr-line)}
.tcs-dot.l em{left:0;transform:none}.tcs-dot.r em{left:auto;right:0;transform:none}
.tcs-gho{position:absolute;top:50%;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:var(--ph);opacity:0;z-index:2;
  box-shadow:0 0 10px 3px var(--ph),-14px 0 12px -2px var(--ph);animation:tcs-fly .9s cubic-bezier(.5,0,.2,1) both}
.tcs-cal{position:absolute;top:50%;width:16px;height:16px;margin:-8px 0 0 -8px;rotate:45deg;border:2px solid var(--am);z-index:2;
  animation:tcs-fade .5s ease-out 2.9s both,tcs-ring 2.2s ease-out 3.4s infinite}
.tcs-ticks{display:flex;justify-content:space-between;font-size:15px;color:var(--dim)}
.tcs-chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.tcs-chip{border:1px solid var(--scr-line);padding:6px 12px;font-size:17px;line-height:1.5;color:var(--scr-text);animation:tcs-in .5s ease-out both}
.tcs-chip[open]{flex-basis:100%}
.tcs-chip summary,.tcs-rc summary{list-style:none;cursor:pointer}
.tcs-chip summary::-webkit-details-marker,.tcs-rc summary::-webkit-details-marker{display:none}
.tcs-chip summary{display:flex;gap:10px;align-items:baseline}
.tcs-chip summary b{color:var(--ph);font-size:20px}
.tcs-chip.past summary b,.tcs-chip.past .k::before{color:var(--am)}
.tcs-chip.past .k::before{content:"◇ "}
.tcs-chip.events summary b,.tcs-chip.events .k::before{color:var(--am)}
.tcs-chip.events .k::before{content:"± "}
.tcs-chip summary u,.tcs-rc summary u{text-decoration:none;opacity:.7;transition:opacity .15s,scale .15s}
.tcs-chip summary:hover u,.tcs-rc summary:hover u{opacity:1;scale:1.25}
.tcs-chip p{margin:8px 0 2px;font-size:16px;line-height:1.6;color:var(--dim)}
.st-key-u-b-cnf .tc-bezel,.st-key-u-c-ans .tc-bezel{margin-bottom:6px}
.tcs-grp{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;letter-spacing:.1em;color:#6B4E22;margin:14px 0 8px}
.tcs-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;align-items:start}
.tcs-rc{position:relative;background:rgba(255,250,235,.4);border:2px solid var(--ink);box-shadow:5px 5px 0 var(--ink);padding:12px 14px 10px;
  font-family:'Noto Serif SC',Georgia,serif;color:var(--ink);animation:tcs-in .6s cubic-bezier(.2,.9,.3,1.3) both,tcs-float 6s ease-in-out 1.5s infinite;transition:translate .18s,box-shadow .18s}
.tcs-rc:hover{translate:-2px -5px;box-shadow:9px 11px 0 var(--ink)}
.tcs-rc::before{content:"";position:absolute;left:0;right:0;top:0;height:6px;background:#6B5F44}
.tcs-rc.p::before{background:#1F6B3A}.tcs-rc.m::before{background:#A2401F}
.tcs-rc summary{display:grid;grid-template-columns:1fr auto;gap:2px 8px;align-items:baseline}
.tcs-rc .ttl{font-size:17px;font-weight:900}
.tcs-rc .tag{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;white-space:nowrap;color:#6B5F44}
.tcs-rc.p .tag{color:#1F6B3A}.tcs-rc.m .tag{color:#A2401F}
.tcs-rc .val{grid-column:1 / -1;font-family:'IBM Plex Mono',monospace;font-size:46px;font-weight:700;line-height:1.15;animation:tcs-pop .5s cubic-bezier(.3,1.7,.5,1) both;animation-delay:inherit}
.tcs-rc.p .val{color:#1F6B3A}.tcs-rc.m .val{color:#A2401F}
.tcs-rc .sh{grid-column:1 / -1;font-size:15px;line-height:1.45;color:#3A3327}
.tcs-rc .wb{align-self:center;height:8px;margin-top:8px;border:1px solid var(--ink);background:rgba(0,0,0,.08)}
.tcs-rc .wb span{display:block;height:100%;background:var(--ink);transform-origin:left;animation:tcs-grow .9s ease-out both;animation-delay:inherit}
.tcs-rc .wl{margin-top:8px;font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;color:#6B4E22;white-space:nowrap}
.tcs-rc .more{grid-column:1 / -1;justify-self:end;margin-top:4px;font-size:14px;font-weight:700;color:#6B4E22;opacity:1;border-bottom:1px dashed #6B4E22}
.tcs-rc[open] .more{opacity:.45}
.tcs-rc p{margin:8px 0 0;padding-top:8px;border-top:1px dashed #8D8163;font-size:14.5px;line-height:1.6;color:#3A3327}
.tcs-rc p small{display:block;margin-top:6px;font-size:13.5px;color:#5C5340}
.tcs-rc.sm{box-shadow:3px 3px 0 var(--ink);background:rgba(255,250,235,.22)}
.tcs-evs{grid-template-columns:1fr;margin:0 0 6px}
.tcs-rc.tcs-ev{background:rgba(255,222,150,.45)}.tcs-rc.tcs-ev::before{background:#C08A1E}
.tcs-rc.tcs-ev .tag{color:#6B4E22}.tcs-rc.tcs-ev .val{font-size:38px;color:#7A2D14}
.tcs-rc.tcs-ev a{color:#6B4E22}
.tcs-rc.sm .val{font-size:32px}
.tcs-warn{font-family:'Noto Serif SC',Georgia,serif;font-size:15px;line-height:1.6;background:rgba(255,246,214,.6);border:2px solid #A2401F;color:#7A2D14;padding:8px 12px}
.tcs-assume{font-family:'Noto Serif SC',Georgia,serif;font-size:16px;line-height:1.7;color:var(--ink);margin:0;padding-left:1.2em}
.st-key-btn_s_ex0 button,.st-key-btn_s_ex1 button,.st-key-btn_s_ex2 button{animation:tcs-float 5s ease-in-out infinite;transition:translate .18s}
.st-key-btn_s_ex1 button{animation-delay:-1.7s}.st-key-btn_s_ex2 button{animation-delay:-3.3s}
.st-key-btn_s_ex0 button:hover,.st-key-btn_s_ex1 button:hover,.st-key-btn_s_ex2 button:hover{translate:0 -3px}
@keyframes tcs-blink{0%{opacity:1}50%{opacity:0}100%{opacity:0}}
@keyframes tcs-in{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:translateY(0)}}
@keyframes tcs-float{0%{transform:translateY(0)}50%{transform:translateY(-5px)}100%{transform:translateY(0)}}
@keyframes tcs-fade{from{opacity:0}}
@keyframes tcs-char{from{opacity:0}to{opacity:1}}
@keyframes tcs-count{from{--tcs-n:0}}
@keyframes tcs-slide{from{left:0}}
@keyframes tcs-drop{from{opacity:0;transform:translateY(-70px) scale(.4)}to{opacity:1;transform:translateY(0) scale(1)}}
@keyframes tcs-fly{0%{left:var(--from);opacity:0}15%{opacity:.95}85%{opacity:.9}100%{left:var(--to);opacity:0}}
@keyframes tcs-ring{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--am) 70%,transparent)}70%{box-shadow:0 0 0 12px transparent}100%{box-shadow:0 0 0 0 transparent}}
@keyframes tcs-glow{0%{text-shadow:0 0 18px color-mix(in srgb,var(--ph) 55%,transparent)}50%{text-shadow:0 0 30px color-mix(in srgb,var(--ph) 90%,transparent)}100%{text-shadow:0 0 18px color-mix(in srgb,var(--ph) 55%,transparent)}}
@keyframes tcs-sweep{0%{transform:translateY(-110%);opacity:1}100%{transform:translateY(420%);opacity:1}}
@keyframes tcs-pop{from{opacity:0;transform:scale(.5)}to{opacity:1;transform:scale(1)}}
@keyframes tcs-grow{from{transform:scaleX(0)}}
@keyframes tcs-nudge{0%{transform:translateX(0)}50%{transform:translateX(5px)}100%{transform:translateX(0)}}
@keyframes tcs-stripes{from{background-position:0 0}to{background-position:28px 0}}
@media (max-width:900px){.tcs-three{grid-template-columns:1fr}}
</style>"""


def bridge(zh: bool) -> str:
    return (f'<div class="tcs-top"><span class="tcs-wm">TICKERCASE</span>'
            f'<span class="tcs-sub">{esc(L(zh, "股票说法 · 可能性检查", "How likely is that stock claim?"))}</span></div>')


def start_head(zh: bool) -> str:
    return (f'<h1 class="tcs-h1">{esc(L(zh, "有人说某只股票会涨到多少？", "Someone says a stock will reach a certain price?"))}<br>'
            f'{esc(L(zh, "输入这句话，看看它成真的可能性有多大。", "Type the sentence in and see how likely it is."))}'
            f'<span class="tcs-cur" aria-hidden="true">▍</span></h1>'
            f'<p class="tcs-lead">{esc(L(zh, "不用懂股票。", "No stock knowledge needed."))}</p>')


def cards(zh: bool) -> str:
    items = [
        (L(zh, "你说一句话", "You say one sentence"), L(zh, "公司、价格、时间，像聊天一样写。", "Company, price and time, in your own words.")),
        (L(zh, "我们查公开数据", "We check public data"), L(zh, "财报、股价、期权，都是免费的公开来源。", "Filings, prices and options, all free public sources.")),
        (L(zh, "给你一个数字和理由", "You get a number and reasons"), L(zh, "百分之几的可能，为什么。不是买卖建议。", "A percentage and why. Not advice to buy or sell.")),
    ]
    return '<div class="tcs-three">' + "".join(
        f'<div class="tcs-card" style="animation-delay:{0.15 * n:.2f}s,{1 + 0.9 * n:.1f}s"><i>{n + 1}</i><div><b>{esc(a)}</b><span>{esc(b)}</span></div></div>'
        for n, (a, b) in enumerate(items)) + "</div>"


def crt(inner: str, *, live: bool = False) -> str:
    """A screen. ``live``: it flickers on and a faint line keeps sweeping down it."""
    sweep = '<span class="tcs-sweep"></span>' if live else ""
    return (f'<div class="tc-bezel"><div class="tc-crt{" tc-boot" if live else ""}"><span class="tc-noise"></span>{sweep}'
            f'<div class="tc-crt-in tc-ph" style="gap:8px">{inner}</div></div></div>')


def confirm_sentence(zh: bool, r: Restated) -> str:
    """The claim as it was read, in one sentence; the four things that can be changed are underlined."""
    who = f"{r.company}（{r.ticker}）" if (r.company and zh) else f"{r.company} ({r.ticker})" if r.company else r.ticker
    price = f"{r.target} {r.currency}" if r.target else L(zh, "____（还没有目标价）", "____ (no price yet)")
    day = r.deadline or L(zh, "____（还没有期限）", "____ (no date yet)")
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
            line = L(zh, f"现在的价格是 {r.reference} {r.currency}，也就是说还要涨 {rate(r.needed)}。",
                     f"The price today is {r.reference} {r.currency}, so it needs to rise {rate(r.needed)}.")
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
    (("对比规模相近的公司，汇总成一个数字", "Comparing with similar companies and putting it together"), ("base_rate", "market_structure")),
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


def _typed(text: str, start: float = 0.45, step: float = 0.07) -> str:
    """The words of the verdict, uncovered one character after the other (the text itself stays whole)."""
    n = max(len(text), 1)
    return f'<span class="tcs-typed" style="animation:tc-type {step * n:.2f}s steps({n},end) {start:.2f}s both">{esc(text)}</span>'


def _number(zh: bool, h: Headline) -> str:
    """The main number, large. A whole percentage counts up from zero; anything else ('<0.1%', a range) is written as it is."""
    text = h.range_text[1 if zh else 0]
    whole = f"{round(h.low * 100)}%" if (h.low is not None and h.low == h.high and 0.01 <= h.low <= 0.99) else None
    if whole and text.count(whole) == 1:
        before, after = text.split(whole)
        return (f'<b class="tcs-big">{esc(before)}<span class="tcs-num" style="--to:{round(h.low * 100)}"><span class="tcs-sr">{whole}</span></span>{esc(after)}</b>')
    return f'<b class="tcs-big">{esc(text)}</b>'


def _scale(zh: bool, h: Headline, parts: list[Part], calibrated: Optional[float]) -> str:
    """The bar from 0 to 100 %: the marker of the main number, one dot per method (its size is its share) that flies
    to the marker, the faint band from the lowest method to the highest, and a diamond where the backtest put it."""
    at = 100 * (h.low + h.high) / 2
    band = ""
    if h.spread_low is not None and h.spread_high is not None and h.spread_high - h.spread_low > 0.005:
        band = f'<span class="spr" style="left:{100 * h.spread_low:.1f}%;width:{100 * (h.spread_high - h.spread_low):.1f}%"></span>'
    dots = ""
    for n, p in enumerate(parts if len(parts) > 1 else []):
        x = 100 * p.value
        size = round(12 + 34 * p.weight)
        up = 12 + 22 * (n % 2)             # neighbours on the bar keep their labels on two heights
        side = " l" if x < 12 else " r" if x > 88 else ""
        label = f"{p.name[1 if zh else 0]} {pct(p.value)}"
        dots += (f'<span class="tcs-dot{side}" style="left:{x:.1f}%;--s:{size}px;--up:{up}px;animation-delay:{1.0 + 0.22 * n:.2f}s"><em>{esc(label)}</em></span>'
                 f'<span class="tcs-gho" aria-hidden="true" style="--from:{x:.1f}%;--to:{at:.1f}%;animation-delay:{1.7 + 0.18 * n:.2f}s"></span>')
    cal = f'<span class="tcs-cal" aria-hidden="true" style="left:{100 * calibrated:.1f}%"></span>' if calibrated is not None else ""
    return (f'<div class="tcs-scale">{band}<span class="half"></span>{dots}{cal}<span class="rng" style="left:{at:.1f}%"></span></div>'
            f'<div class="tcs-ticks"><span>{esc(L(zh, "0% 几乎不可能", "0% almost never"))}</span><span>{esc(L(zh, "50% 像抛硬币", "50% a coin flip"))}</span>'
            f'<span>{esc(L(zh, "100% 几乎肯定", "100% almost sure"))}</span></div>')


def answer_screen(zh: bool, h: Headline, claim: tuple[str, str], chips: Optional[list[Chip]] = None, parts: Optional[list[Part]] = None,
                  calibrated: Optional[float] = None) -> str:
    """The answer: the word, the number, the bar. What was three paragraphs is three chips that open in place."""
    i = 1 if zh else 0
    if h.low is None:
        return crt(f'<div class="tcs-claim">{esc(claim[i])}</div>'
                   f'<div class="tcs-verdict">{esc(h.word[i])}{L(zh, "：", ": ")}{esc(h.range_text[i])}{L(zh, "。", ".")}</div>', live=True)
    verdict = f'<div class="tcs-verdict">{_typed(h.word[i] + L(zh, "：", ":"))}<br>{_number(zh, h)}</div>'
    chip_html = "".join(
        f'<details class="tcs-chip {c.kind}" style="animation-delay:{3.0 + 0.15 * n:.2f}s"><summary><span class="k">{esc(c.label[i])}</span>'
        + (f"<b>{esc(c.value[i])}</b>" if c.value[i] else "") + f'<u>ⓘ</u></summary><p>{esc(c.full[i])}</p></details>'
        for n, c in enumerate(chips or []))
    return crt(f'<div class="tcs-claim">{esc(claim[i])}</div>{verdict}{_scale(zh, h, parts or [], calibrated)}'
               + (f'<div class="tcs-chips">{chip_html}</div>' if chip_html else ""), live=True)


def reasons(zh: bool, items: list[Reason], basis: str = "", context: str = "") -> str:
    """The reasons as cards in two groups: what the main number rests on, then the numbers shown for context.
    A card shows a title, one number and one line; the full sentence and the limit of the number open under ⓘ."""
    if not items:
        return f'<p class="tcs-lead">{esc(L(zh, "这次没有足够的数据列出理由。", "There was not enough data to list reasons this time."))}</p>'
    word = {"+": (L(zh, "＋ 支持", "+ For"), "p"), "-": (L(zh, "－ 反对", "− Against"), "m"), "0": (L(zh, "· 中性", "· Neutral"), "n")}
    i = 1 if zh else 0

    def card(r: Reason, n: int, small: bool) -> str:
        tag, cls = word[r.sign]
        title = r.title[i] if r.title else tag
        share = ""
        if r.weight:
            share = (f'<span class="wb"><span style="width:{round(r.weight * 100)}%"></span></span>'
                     f'<span class="wl">{L(zh, "占", "share")} {round(r.weight * 100)}%</span>')
        body = esc(r.text[i]) + (f"<small>{esc(r.note[i])}</small>" if r.note else "")
        return (f'<details class="tcs-rc {cls}{" sm" if small else ""}" style="animation-delay:{0.25 + 0.18 * n:.2f}s,{1.5 + 0.8 * n:.1f}s">'
                f'<summary><span class="ttl">{esc(title)}</span><span class="tag">{esc(tag)}</span>'
                + (f'<span class="val">{esc(r.value)}</span>' if r.value else "")
                + (f'<span class="sh">{esc(r.short[i])}</span>' if r.short else "")
                + f'{share}<u class="more">ⓘ {L(zh, "细节", "details")}</u></summary><p>{body}</p></details>')

    main, rest = [r for r in items if r.counted], [r for r in items if not r.counted]
    out = ""
    if main:
        out += ((f'<div class="tcs-grp">{esc(basis)}</div>' if basis else "")
                + '<div class="tcs-cards">' + "".join(card(r, n, False) for n, r in enumerate(main)) + "</div>")
    if rest:
        out += ((f'<div class="tcs-grp">{esc(context)}</div>' if context else "")
                + '<div class="tcs-cards">' + "".join(card(r, len(main) + n, True) for n, r in enumerate(rest)) + "</div>")
    return out


def events(zh: bool, items: list[EventCard]) -> str:
    """Major events of the last weeks, one amber card each; the sources and what it means for the number open under it."""
    i = 1 if zh else 0
    out = ""
    for n, e in enumerate(items):
        links = " · ".join(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}</a>' for label, url in e.sources if url)
        out += (f'<details class="tcs-rc tcs-ev" style="animation-delay:{0.1 + 0.15 * n:.2f}s,{1.2 + 0.8 * n:.1f}s"><summary>'
                f'<span class="ttl">{esc(e.title[i])}</span><span class="tag">{esc(e.tag[i])}</span>'
                + (f'<span class="val">{esc(e.value)}</span>' if e.value else "")
                + f'<span class="sh">{esc(e.short[i])}</span><u class="more">ⓘ {L(zh, "细节", "details")}</u></summary>'
                f'<p>{esc(e.full[i])}' + (f"<small>{L(zh, '来源：', 'Sources: ')}{links}</small>" if links else "") + "</p></details>")
    return f'<div class="tcs-cards tcs-evs">{out}</div>' if out else ""


def assumptions(zh: bool, items: list[tuple[str, str]]) -> str:
    return '<ul class="tcs-assume">' + "".join(f"<li>{esc(x[1 if zh else 0])}</li>" for x in items) + "</ul>"


def warn(text: str) -> str:
    return f'<div class="tcs-warn">{esc(text)}</div>'
