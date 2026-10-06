"""Cockpit look (v0.9): CSS for the Streamlit page and HTML for the console units.

The page is one machine: a beige casing (the main block) with a data bus down the
left edge, and units mounted in it. Units are keyed Streamlit containers styled by
their key prefix (``u-c-`` charcoal, ``u-o-`` olive, ``u-b-`` beige, ``u-r-`` brass,
``g-`` a recessed group plate); native widgets keep their keys and behaviour and are
only restyled. Everything else here returns HTML strings, so it can be tested
without Streamlit.

Rules kept from the design: light only inside screens (phosphor green or amber),
cel shading only on hardware, red only on warning lamps, motion in steps and only
on the first view of a new report (``anim``), and reduced motion turns it off.
"""

from __future__ import annotations

import html
import math
from datetime import date, timedelta
from typing import Iterable, Optional

from ..models import VERDICT_DISPLAY_ZH, CaseResult, Narrative
from ..oracle import MIN_INDEPENDENT_WINDOWS, independent_windows

PIX = "app/static/fonts/fusion-pixel-12px-monospaced-zh_hans.otf.woff2"
VT = "app/static/fonts/VT323-Regular.woff2"
GRAIN = "app/static/textures/grain.png"
SNOW = "app/static/textures/snow.png"


def L(zh: bool, z, e):
    return z if zh else e


def esc(x) -> str:
    """HTML-escape text for st.markdown; "$" becomes an entity, or two amounts in one block would be read as inline math."""
    return html.escape("" if x is None else str(x), quote=True).replace("$", "&#36;")


def pct(p: Optional[float], digits: int = 1) -> str:
    if p is None:
        return "—"
    return f"{p * 100:.{digits}f}%"


def amount(value, zh: bool) -> str:
    """215938000000 -> '2,159.38 亿' / '215.94B'."""
    if value is None:
        return "—"
    v = float(value)
    for size, unit in (((1e12, " 万亿"), (1e8, " 亿"), (1e4, " 万")) if zh else ((1e12, "T"), (1e9, "B"), (1e6, "M"))):
        if abs(v) >= size:
            return f"{v / size:,.2f}".rstrip("0").rstrip(".") + unit
    return f"{v:,.0f}"


def tr(text, zh: bool) -> str:
    if text is None:
        return ""
    return (text.zh or text.en) if zh else (text.en or text.zh)


def deadline_of(result: CaseResult) -> date:
    if result.oracle is not None:
        return result.oracle.target_date
    v = result.confirmed_claim.values
    return v.reference_price_date + timedelta(days=round(float(v.horizon_years) * 365.25))


# ------------------------------------------------------------------ small parts


def dymo(text: str, small: bool = False) -> str:
    return f'<span class="tc-dymo{" tc-dymo-s" if small else ""}">{esc(text)}</span>'


def lamp(kind: str, cls: str = "", style: str = "") -> str:
    extra = f' style="{style}"' if style else ""
    return f'<span class="tc-lamp tc-l-{kind}{(" " + cls) if cls else ""}" aria-hidden="true"{extra}></span>'


def chip(text: str) -> str:
    return f'<span class="tc-chip">{esc(text)}</span>'


def lamp_row(kind: str, text: str) -> str:
    return f'<div class="tc-lrow">{lamp(kind)}<span>{esc(text)}</span></div>'


def sub_head(text: str) -> str:
    return f'<div class="tc-subhead">{esc(text)}</div>'


def unit_head(label: str, right: str = "", anchor: str = "") -> str:
    a = f'<a id="{esc(anchor)}" class="tc-anchor"></a>' if anchor else ""
    return f'{a}<div class="tc-uhead">{dymo(label)}{right}</div>'


EMBLEM = (
    '<span class="tc-emb" aria-hidden="true"><span class="tc-emb-leaf"></span>'
    '<span class="tc-emb-patch" style="left:2px;top:34px"></span><span class="tc-emb-patch" style="left:34px;top:46px;background:#A58A55"></span>'
    '<span class="tc-emb-patch" style="left:18px;top:2px;background:#BBA16B"></span>'
    '<span class="tc-emb-art"><span class="tc-wick" style="left:13px;top:22px;height:26px"></span><span class="tc-yin" style="left:10px;top:28px;width:8px;height:13px"></span>'
    '<span class="tc-wick" style="left:49px;top:14px;height:30px"></span><span class="tc-yang" style="left:46px;top:19px;width:8px;height:16px"></span></span>'
    '<span class="tc-emb-mist" style="left:2px;top:44px;width:24px"></span><span class="tc-emb-mist" style="left:38px;top:48px;width:24px"></span>'
    '<span class="tc-emb-art"><span class="tc-wick" style="left:31px;top:17px;height:8px"></span><span class="tc-cbody"></span>'
    '<span class="tc-wick" style="left:31px;top:51px;height:7px"></span><span class="tc-flame"><span class="tc-fl"></span><span class="tc-core"></span></span></span></span>'
)

STRIPES = ('<span class="tc-stripes" aria-hidden="true"><span style="background:#C49A4A"></span><span style="background:#A8623A"></span>'
           '<span style="background:#7E3B2A"></span></span>')


# ------------------------------------------------------------------ bridge, claim condition, warnings


def bridge(zh: bool, *, sys_on: bool, link: str, warn: int, chips: Iterable[str]) -> str:
    lamps = [("amber", "PWR"), ("green" if sys_on else "amber", "SYS" if sys_on else "STBY"), (link, "LINK"),
             ("red" if warn else "off", f"WARN {warn}" if warn else "WARN")]
    lamp_html = "".join(f'<span class="tc-lampcol">{lamp(k)}{dymo(t, small=True)}</span>' for k, t in lamps)
    return (f'<div class="tc-bridge"><span class="tc-brand">{EMBLEM}<span class="tc-brand-t"><b>TICKERCASE</b>'
            f'<span>{esc(L(zh, "TC-8 型 · 观点概率控制台", "TC-8 · claim probability console"))}</span></span></span>'
            f'<span class="tc-lamps">{lamp_html}</span>{"".join(chip(c) for c in chips)}</div>')


def is_touch(result: CaseResult) -> bool:
    if result.oracle is not None:
        return result.oracle.condition == "touch"
    return result.confirmed_claim is not None and result.confirmed_claim.values.price_condition == "touch"


def cond_text(result: CaseResult, zh: bool) -> str:
    return L(zh, "期间任意时点触及", "Touch at any time") if is_touch(result) else L(zh, "到期时站在上面", "At or above on the date")


def other_side(result: CaseResult, zh: bool) -> tuple[str, str]:
    """The condition the claim does not use, from the market-based methods: ('29–31%', 'options 30.1% · model 29.4%')."""
    o = result.oracle
    if o is None:
        return "", ""
    vals = []
    for m in o.methods:
        if m.id in ("M1", "M2") and m.status == "ok":
            p = m.probability if o.condition == "touch" else m.touch_probability
            if p is not None:
                vals.append((m.id, p))
    if not vals:
        return "", ""
    lo, hi = min(p for _, p in vals), max(p for _, p in vals)
    rng = pct(lo, 0) if round(lo * 100) == round(hi * 100) else f"{lo * 100:.0f}–{hi * 100:.0f}%"
    names = {"M1": L(zh, "期权", "options"), "M2": L(zh, "模型", "model")}
    return rng, " · ".join(f"{names[i]} {pct(p)}" for i, p in vals)


def other_label(result: CaseResult, zh: bool) -> str:
    t = format(result.oracle.target_price, "f")
    if result.oracle.condition == "touch":
        return L(zh, f"到期收在 {t} 以上", f"close above {t} on the date")
    return L(zh, f"期间触及 {t}", f"touch {t} before the date")


def warnings_of(result: CaseResult, zh: bool) -> list[str]:
    """System warnings (red lamps): failed data sources and filing coverage gaps. Market signals are not warnings."""
    out = [f"[{e.provider_id}] {e.code}" + (f" (HTTP {e.http_status})" if e.http_status else "") for e in result.provider_errors]
    if result.coverage is not None and result.coverage.coverage_gap:
        out.append((result.coverage.message_zh or result.coverage.message) if zh else result.coverage.message)
    return out


def status_bar(result: CaseResult, zh: bool, jumps: Iterable[tuple[str, str]]) -> str:
    v = result.confirmed_claim.values
    o = result.oracle
    touch = is_touch(result)
    if o is not None and o.low is not None:
        rng = (f'<span class="tc-rd tc-ph tc-row"><span class="tc-vt" style="font-size:34px;line-height:30px">{pct(o.low, 0)}–{pct(o.high, 0)}</span>'
               f'<span class="tc-inv">{esc(tr(o.tier_label, zh))}</span></span>')
    else:
        rng = f'<span class="tc-rd tc-ph tc-row"><span style="font-size:12px">{esc(L(zh, "数据不足，无法计算概率", "Not enough data to compute a probability"))}</span></span>'
    d, target = deadline_of(result).isoformat(), format(v.target_price, "f")
    claim = (L(zh, f"{v.ticker} · {d} 前触及 {target}", f"{v.ticker} · touch {target} by {d}") if touch
             else L(zh, f"{v.ticker} · {d} 收在 {target} 以上", f"{v.ticker} · at or above {target} on {d}"))
    side = ""
    if o is not None:
        r, _ = other_side(result, zh)
        side = chip(f"{other_label(result, zh)} ≈ {r}") if r else ""
    n = len(warnings_of(result, zh))
    warn = (f'<a href="#u-sig" class="tc-warnlink">{lamp("red")}{dymo(f"WARN {n}", small=True)}</a>' if n
            else f'<span class="tc-warnlink">{lamp("off")}{dymo("WARN 0", small=True)}</span>')
    links = "".join(f'<a class="tc-jump" href="#{esc(h)}">{esc(t)}</a>' for h, t in jumps)
    return (f'<div class="tc-status"><b class="tc-serif">{esc(claim)}</b>{rng}{side}{warn}<span class="tc-grow"></span>'
            f'<nav class="tc-jumps" aria-label="{esc(L(zh, "跳到单元", "Jump to unit"))}">{links}</nav></div>')


# ------------------------------------------------------------------ result units


def readout(label: str, value: str, *, amber: bool = False, pixel: bool = False) -> str:
    cls = ("" if pixel else "tc-vt") + (" tc-am" if amber else "")
    size = "24px" if pixel else "36px"
    return (f'<div class="tc-rd tc-ph"><span class="tc-dim" style="font-size:12px;line-height:14px">{esc(label)}</span>'
            f'<span class="{cls}" style="font-size:{size};line-height:34px">{esc(value)}</span></div>')


def claim_unit(result: CaseResult, zh: bool, label: str) -> str:
    v = result.confirmed_claim.values
    calc = {c.name: c for c in result.calculations if c.status == "ok"}
    req, ann = calc.get("required_return"), calc.get("annualized_price_return")
    req_text = (f"{float(req.value) * 100:+.1f}%" if req else "—") + (f" · {float(ann.value) * 100:.1f}%/{L(zh, '年', 'yr')}" if ann else "")
    sources = [name for step, name in (("sec_facts", "SEC"), ("price_history", "YAHOO"), ("prediction_markets", "POLYMARKET"), ("fear_greed", "CNN"))
               if result.data_steps.get(step) == "ok"]
    modes = sorted({m for k, x in result.data_modes.items() if k in ("sec_filings", "sec_facts", "market_prices") for m in x.split(",") if m != "unavailable"})
    case = f"CASE {result.case_id[:8].upper()} · {'/'.join(modes).upper() or '—'}"
    return (unit_head(label, dymo(case), "u-clm")
            + f'<h1 class="tc-claim">{esc(v.claim_text or v.ticker)}</h1>'
            + '<div class="tc-grid4">'
            + readout(L(zh, "COND · 判定条件", "COND · condition"), cond_text(result, zh), pixel=True)
            + readout(L(zh, "T-DATE · 目标日", "T-DATE · deadline"), deadline_of(result).isoformat(), amber=True)
            + readout(L(zh, f"LAST · {v.reference_price_date} 收盘 {v.currency}", f"LAST · close {v.reference_price_date} {v.currency}"), f"{float(v.reference_price):,.2f}")
            + readout(L(zh, "REQ · 需要的涨幅", "REQ · required move"), req_text, amber=True)
            + "</div>"
            + f'<div class="tc-note">{esc(L(zh, "数据", "Data"))} · {result.created_at:%Y-%m-%d %H:%M} UTC'
            + (f' · {esc(" · ".join(sources))}' if sources else "") + "</div>")


def counted(result: CaseResult, m) -> bool:
    """Whether a method may enter the headline range (M4 needs enough independent windows; M3 does not measure a touch)."""
    if m.id == "M4" and independent_windows(result.price_base_rate) < MIN_INDEPENDENT_WINDOWS:
        return False
    if m.id == "M3" and result.oracle.condition == "touch":
        return False
    return True


def sqrt_x(p: float) -> float:
    """Square-root position on the 0-100% scale, so small odds stay visible."""
    return 100 * math.sqrt(max(0.0, min(1.0, p)))


def _zone(x: float) -> str:
    return "z1" if x < 22.4 else "z2" if x < 44.7 else "z3" if x < 70.7 else "z4"


def split_methods(result: CaseResult) -> tuple[list, list]:
    """(methods inside the headline range, computed methods shown as context), each as (method, probability)."""
    o = result.oracle
    touch = o.condition == "touch"
    used, ref = [], []
    for m in o.methods:
        p = m.touch_probability if touch else m.probability
        if m.status != "ok" or p is None:
            continue
        inside = o.low is not None and o.low - 1e-12 <= p <= o.high + 1e-12 and counted(result, m)
        (used if inside else ref).append((m, p))
    return used, ref


def mini_cassette(spin: bool = False) -> str:
    return (f'<span class="tc-minicas{" tc-spin" if spin else ""}" aria-hidden="true"><span class="tc-reel"><span class="tc-hub"></span></span>'
            f'<span class="tc-reel tc-reel-s"><span class="tc-hub"></span></span></span>')


def prb_unit(result: CaseResult, zh: bool, label: str, anim: bool) -> str:
    o = result.oracle
    touch = o.condition == "touch"
    target = format(o.target_price, "f")
    head = L(zh, f"P({o.target_date} 前{'触及' if touch else '收在'} {target}{'' if touch else ' 以上'})",
             f"P({'touch' if touch else 'close above'} {target} by {o.target_date})")
    used, ref = split_methods(result)
    used_ids = {m.id for m, _ in used}
    lines = []
    if used:
        lines.append(("", L(zh, "计入区间　", "In range　") + " · ".join(f"{m.id} {p * 100:.1f}" for m, p in used)))
    if ref:
        lines.append(("tc-dim", L(zh, "仅作参考　", "Context only　") + " · ".join(f"{m.id} {p * 100:.1f}" for m, p in ref)))
    _, side_detail = other_side(result, zh)
    if side_detail:
        lines.append(("tc-am", f"{other_label(result, zh)}{L(zh, '：', ': ')}{side_detail}"))
    lines_html = "".join(f'<div class="{c} tc-line">{esc(t)}</div>' for c, t in lines)
    if o.low is not None:
        big = (f'<span class="tc-vt tc-huge">{pct(o.low, 0)}–{pct(o.high, 0)}</span>'
               f'<div class="tc-col"><span class="tc-inv tc-tier">{esc(tr(o.tier_label, zh))}</span>{lines_html}</div>')
    else:
        big = f'<span class="tc-nodata">{esc(L(zh, "数据不足，无法计算概率", "Not enough data to compute a probability"))}</span><div class="tc-col">{lines_html}</div>'
    lo, hi = (sqrt_x(o.low), sqrt_x(o.high)) if o.low is not None else (None, None)
    cells = "".join(f'<span class="tc-cell {_zone(i * 2 + 1)}{" lit" if lo is not None and lo - 1 <= i * 2 + 1 <= hi + 1 else ""}"></span>' for i in range(50))
    top, bottom, last_top = [], [], -99.0
    for m, p in sorted(used + ref, key=lambda x: x[1]):
        x, inside = sqrt_x(p), m.id in used_ids
        cls = "tc-am" if inside else "tc-dim"
        if x - last_top > 6:
            top.append(f'<span class="{cls} tc-mark" style="left:{x:.1f}%">{"▼" if inside else "▽"}{esc(m.id)}</span>')
            last_top = x
        else:
            bottom.append(f'<span class="{cls} tc-mark" style="left:{x:.1f}%">{"▲" if inside else "△"}{esc(m.id)}</span>')
    ticks = "".join(f'<span class="tc-dim tc-tick" style="left:{sqrt_x(p):.1f}%{";transform:translateX(-100%)" if p == 1 else ""}">{lbl}</span>'
                    for p, lbl in ((0.01, "1%"), (0.05, "5%"), (0.2, "20%"), (0.5, "50%"), (1.0, "100%")))
    zones = L(zh, ("彩票级 &lt;5%", "不太可能 5–20%", "有可能 20–50%", "较可能 &gt;50%"), ("Lottery &lt;5%", "Unlikely 5–20%", "Possible 20–50%", "Likely &gt;50%"))
    zone_html = "".join(f'<span{" class=tc-am" if i == 3 else ""}>{z}</span>' for i, z in enumerate(zones))
    n_ok = len([m for m in o.methods if m.status == "ok"])
    screen = (f'<div class="tc-bezel"><div class="tc-crt{" tc-boot" if anim else ""}"><span class="tc-noise"></span><div class="tc-crt-in tc-ph" style="gap:12px">'
              f'<span class="tc-dim tc-small">PRB&gt; {esc(head)} · {esc(L(zh, f"{n_ok} 种方法交叉核对", f"{n_ok} methods cross-checked"))}</span>'
              f'<div class="tc-bigrow">{big}</div>'
              f'<div class="tc-marks">{"".join(top)}</div><div class="tc-cells" aria-hidden="true">{cells}</div><div class="tc-marks">{"".join(bottom)}{ticks}</div>'
              f'<div class="tc-zones tc-dim">{zone_html}</div>'
              f'<span class="tc-dim tc-small">{esc(L(zh, "平方根刻度 · 亮格为区间 · ▼▲ 计入 · ▽△ 仅参考", "square-root scale · lit cells = range · ▼▲ counted · ▽△ context"))}</span>'
              f'</div></div></div>')
    strips = []
    for m in o.methods:
        p = m.touch_probability if touch else m.probability
        ok = m.status == "ok" and p is not None
        tag = L(zh, "计入", "in range") if m.id in used_ids else (L(zh, "参考", "context") if m.status == "ok" else L(zh, "缺数据", "no data"))
        lit = min(24, int(sqrt_x(p) // 4)) if ok else -1
        mcells = "".join(f'<span class="tc-mcell {_zone(i * 4 + 2)}{" lit" if i == lit else ""}"></span>' for i in range(25))
        a = pct(m.touch_probability) if m.status == "ok" else "—"
        b = pct(m.probability) if m.status == "ok" else "—"
        strips.append(
            f'<div class="tc-strip{"" if m.status == "ok" else " tc-off"}"><span class="tc-stub" aria-hidden="true"></span>'
            f'<div class="tc-row" style="gap:10px">{mini_cassette(spin=anim and ok)}'
            f'<div class="tc-col" style="gap:2px;min-width:0"><span><b style="font-size:18px">{esc(m.id)}</b> <span class="tc-tag">{esc(tag)}</span></span>'
            f'<b class="tc-serif" style="font-size:16px">{esc(tr(m.name, zh))}</b></div></div>'
            f'<div class="tc-grid2">{readout(L(zh, "A · 触及", "A · touch"), a, amber=touch)}{readout(L(zh, "B · 收在", "B · close"), b, amber=not touch)}</div>'
            f'<div class="tc-rd" aria-hidden="true" style="padding:5px 6px"><div class="tc-mcells">{mcells}</div></div>'
            f'<span class="tc-basis">{esc(tr(m.measures, zh))}</span></div>')
    return (unit_head(label, chip(L(zh, "平方根刻度 · 同一把尺子", "square-root scale · one ruler")), "u-prb")
            + f'<div class="tc-prb"><div class="tc-prb-screen">{screen}</div><div class="tc-prb-methods">{"".join(strips)}</div></div>')


def kline_notes(result: CaseResult, zh: bool) -> tuple[str, str]:
    """Labels for the K-line's future zone: (touch line, close-above line); only computed numbers, no forecast path."""
    o = result.oracle
    if o is None:
        return "", ""
    t = format(o.target_price, "f")
    main = f"{pct(o.low, 0)}–{pct(o.high, 0)} {tr(o.tier_label, zh)}" if o.low is not None else L(zh, "数据不足", "not enough data")
    side, _ = other_side(result, zh)
    touch_label, end_label = L(zh, f"期间触及 {t}", f"touch {t}"), L(zh, f"到期收在 {t} 以上", f"close above {t}")
    if o.condition == "touch":
        return f"{touch_label} · {main}", (f"{end_label} · {side}" if side else "")
    return (f"{touch_label} · {side}" if side else ""), f"{end_label} · {main}"


def feeds_unit(result: CaseResult, zh: bool, label: str, anim: bool) -> str:
    o, mk, op = result.oracle, result.market, result.options
    facts, br, ins, pm, se = result.reported_facts, result.base_rate, result.insiders, result.prediction_markets, result.sentiment
    tgt = format(o.target_price, "f") if o is not None else ""
    latest = None
    if facts is not None:
        series = facts.revenue or facts.net_income
        latest = series[-1] if series else None
    first = pm.markets[0] if pm and pm.markets else None
    rows = [
        ("CH-01 OPT", "options", L(zh, "期权链", "Option chain"), pct(op.target_iv, 0) if op and op.target_iv is not None else "—",
         L(zh, f"行权价 {tgt} 的隐含波动", f"implied vol at {tgt}"),
         f"YAHOO · {op.expiry} · {L(zh, '看涨未平仓', 'call OI')} {op.total_call_oi:,}" if op else "YAHOO", "M1"),
        ("CH-02 PRC", "price_history", L(zh, "价格历史", "Price history"), f"{float(mk.last_close):,.2f}" if mk else "—",
         (L(zh, f"{mk.last_date} 收盘 · 年化波动 {float(mk.annualized_volatility) * 100:.0f}%", f"close {mk.last_date} · vol {float(mk.annualized_volatility) * 100:.0f}%/yr")
          if mk and mk.annualized_volatility is not None else ""),
         L(zh, "YAHOO · 日线 · K 线见 UNIT 02", "YAHOO · daily · K-line in UNIT 02"), "M1 · M2 · M4 · FND"),
        ("CH-03 TNX", "risk_free_rate", L(zh, "10 年期美债", "10-year Treasury"),
         pct(float(o.risk_free_rate), 2) if o is not None and o.risk_free_rate is not None else "—", L(zh, "无风险利率", "risk-free rate"), "YAHOO · ^TNX", "M1 · M2"),
        ("CH-04 FIN", "sec_facts", L(zh, "财报事实", "Reported facts"), amount(latest.value, zh) if latest else "—",
         (L(zh, f"截至 {latest.period_end} 的财年", f"fiscal year ending {latest.period_end}") if latest else ""),
         f"SEC XBRL · {latest.form} {latest.filed}" if latest else "SEC XBRL", "M3 · FND"),
        ("CH-05 PEER", "base_rate", L(zh, "同规模公司", "Peer companies"), f"{br.achieved} / {br.companies}" if br and br.companies else "—",
         (L(zh, f"达到年增速 {float(br.required_cagr) * 100:.1f}%", f"reached {float(br.required_cagr) * 100:.1f}% a year") if br and br.companies else ""),
         f"SEC FRAMES · {br.start_year}–{br.end_year}" if br else "SEC FRAMES", "M3"),
        ("CH-06 INS", "insiders", L(zh, "内部人交易", "Insider trades"), (L(zh, f"卖 {ins.sales} · 买 {ins.purchases}", f"sell {ins.sales} · buy {ins.purchases}") if ins else "—"),
         (L(zh, f"12 个月公开市场 · 卖出约 {amount(ins.sale_value, zh)} 美元", f"12-month open market · sold about ${amount(ins.sale_value, zh)}") if ins else ""),
         "SEC FORM 4", "SIG"),
        ("CH-07 PMKT", "prediction_markets", L(zh, "预测市场", "Prediction market"),
         pct(first.probability_yes, 0) if first and first.probability_yes is not None else "—", first.question if first else "", "POLYMARKET", "SIG"),
        ("CH-08 SENT", "fear_greed", L(zh, "市场情绪", "Market mood"), f"{se.score:.1f}" if se else "—",
         (L(zh, f"恐惧贪婪 · 一月前 {se.previous_month:.1f}", f"Fear & Greed · a month ago {se.previous_month:.1f}") if se and se.previous_month is not None else ""),
         "CNN FEAR & GREED", "SIG"),
    ]
    cards = []
    for i, (ch, step, name, value, sub, src, to) in enumerate(rows):
        state = result.data_steps.get(step)
        kind, word = ("green", "OK") if state == "ok" else ("red", "FAIL") if state == "failed" else ("off", "—")
        cards.append(f'<div class="tc-feedm"><div class="tc-row tc-between">{dymo(ch, small=True)}'
                     f'<span class="tc-row" style="gap:6px">{lamp(kind, "tc-pop" if anim else "", f"animation-delay:{0.2 + i / 12:.2f}s" if anim else "")}'
                     f'<b class="tc-tiny">{word}</b></span></div>'
                     f'<b class="tc-serif" style="font-size:16px">{esc(name)}</b>'
                     f'<div class="tc-rd tc-ph"><span class="tc-vt" style="font-size:30px;line-height:28px">{esc(value)}</span>'
                     f'<span class="tc-dim" style="font-size:12px;line-height:14px">{esc(sub)}</span></div>'
                     f'<span class="tc-src">{esc(src)}</span><span class="tc-to">→ {esc(to)}</span></div>')
    return (unit_head(label, chip(L(zh, "每个读数都有来源 · → 表示进入哪种方法", "every reading has a source · → feeds which method")), "u-feed")
            + f'<div class="tc-feeds">{"".join(cards)}</div>')


def sig_unit(result: CaseResult, zh: bool, label: str, notes: Iterable[str] = ()) -> str:
    rows = []
    rep = result.report
    if rep is not None:
        rows += [("green", "++", tr(x, zh)) for x in rep.agreements]
        rows += [("amber", "!!", tr(x, zh)) for x in rep.divergences]
    warns = warnings_of(result, zh)
    rows += [("red", "WARN", w) for w in warns]
    if not warns:
        rows.append(("off", "WARN 0", L(zh, "没有系统警示：数据源全部成功，申报覆盖完整。", "No system warnings: every data source answered and filing coverage is complete.")))
    rows += [("off", "NOTE", n) for n in notes]
    body = "".join(f'<div class="tc-sigrow">{lamp(k)}<span><b>{esc(t)}</b>　{esc(x)}</span></div>' for k, t, x in rows)
    return unit_head(label, "", "u-sig") + body


def fnd_unit(result: CaseResult, zh: bool, label: str, more: str) -> str:
    v = result.verdict
    head = f'<div class="tc-uhead"><span class="tc-engr tc-tiny" style="letter-spacing:.18em">{esc(label)}</span></div>'
    if v is None:
        return head + f'<p class="tc-engr tc-serif">{esc(L(zh, "这份报告没有基本面结论。", "This report has no fundamentals verdict."))}</p>'
    tone = {"supported_today": "green", "partially_supported": "amber", "not_supported_today": "off", "insufficiently_specified": "off"}[v.label]
    name = (v.display_zh or VERDICT_DISPLAY_ZH[v.label]) if zh else v.display
    meaning = tr(result.report.verdict_meaning, zh) if result.report is not None else ""
    return (head + f'<div class="tc-row" style="gap:12px;margin-bottom:6px">{lamp(tone)}<span class="tc-engr tc-serif tc-fndname">{esc(name)}</span></div>'
            f'<div class="tc-engr tc-tiny">{esc(v.rules_version.upper())} · {esc(L(zh, "截至", "as of"))} {v.as_of}</div>'
            f'<p class="tc-engr tc-serif tc-fndtext">{esc(meaning or L(zh, "证据视图，不是预测。", "An evidence view, not a forecast."))}</p>'
            f'<a class="tc-jump" href="#u-rpt">{esc(more)}</a>')


NARR_ORDER = ("logic_chain", "resonance", "divergences", "conclusion", "upside", "downside")
NARR_TITLES = {"logic_chain": ("核心逻辑链", "Core logic"), "resonance": ("共振信号", "Signals that agree"), "divergences": ("关键分歧", "Key divergences"),
               "conclusion": ("结论", "Conclusion"), "upside": ("上行风险", "Upside risks"), "downside": ("下行风险", "Downside risks")}


def nar_paper(n: Narrative, zh: bool, anim: bool) -> str:
    """Continuous printer paper: one stamped line per sentence; the seal says how its numbers checked out."""
    badge = {"verified": L(zh, "核", "OK"), "qualitative": "○", "cited_elsewhere": "⚠", "unsupported": "✖"}
    rows = []
    for key in NARR_ORDER:
        sentences = n.sections.get(key) or []
        if not sentences:
            continue
        rows.append(f'<div class="tc-ptitle">{esc(L(zh, *NARR_TITLES[key]))}</div>')
        for s in sentences:
            text = (s.zh if zh else s.en) or s.zh or s.en
            seal = f'<span class="tc-seal{" tc-seal-bad" if s.status == "unsupported" else ""}" title="{esc(s.status)}">{esc(badge[s.status])}</span>'
            facts = f'<span class="tc-pfacts">[{esc(" ".join(s.fact_ids))}]</span>' if s.fact_ids else ""
            rows.append(f'<div class="tc-prow">{seal}<div class="tc-col" style="gap:4px;min-width:0"><span class="tc-ptext">{esc(text)}</span>{facts}</div></div>')
    return (f'<div class="tc-slot" aria-hidden="true"></div><div class="tc-paperwrap"><div class="tc-paper{" tc-feed" if anim else ""}">'
            f'<span class="tc-holes" style="left:0"></span><span class="tc-holes" style="right:0"></span>{"".join(rows)}</div></div>')


def pipeline(steps: dict, zh: bool, labels: dict) -> str:
    keys = list(labels)
    done = sum(1 for k in keys if steps.get(k) == "ok")
    segs, rows = [], []
    for k in keys:
        state = steps.get(k)
        segs.append(f'<span class="tc-seg{ {"ok": " on", "failed": " bad", "running": " run"}.get(state, "")}"></span>')
        kind, cls = {"ok": ("green", ""), "failed": ("red", ""), "running": ("amber", "tc-blink")}.get(state, ("off", ""))
        word = {"ok": L(zh, "✔ 完成", "✔ done"), "failed": L(zh, "✖ 失败", "✖ failed"), "running": L(zh, "… 进行中", "… running")}.get(state, L(zh, "· 等待", "· waiting"))
        rows.append(f'<div class="tc-prow2">{lamp(kind, cls)}<span class="tc-grow">{esc(labels[k][0 if zh else 1])}</span><span>{esc(word)}</span></div>')
    return (f'<div class="tc-bezel"><div class="tc-crt"><span class="tc-noise"></span><div class="tc-crt-in tc-ph" style="gap:10px">'
            f'<div class="tc-row tc-between tc-small" style="flex-wrap:wrap;gap:4px 16px"><span class="tc-am">PIPELINE&gt; {esc(L(zh, "正在拉取数据", "fetching data"))} · {done} / {len(keys)}</span>'
            f'<span class="tc-dim">{esc(L(zh, "通常 15–30 秒", "usually 15–30 s"))}</span></div>'
            f'<div class="tc-segs" style="grid-template-columns:repeat({len(keys)},minmax(0,1fr))">{"".join(segs)}</div>'
            f'<div class="tc-steps">{"".join(rows)}</div></div></div></div>')


def keyswitch(armed: bool, zh: bool) -> str:
    """The arming key: drawn only; the confirm key next to it does the work."""
    return (f'<div class="tc-ks-wrap" aria-hidden="true"><span class="tc-ks-lbl">SAFE<br>{esc(L(zh, "未确认", "off"))}</span>'
            f'<span class="tc-ks"><span class="tc-ks-plate"></span><span class="tc-ks-ring"></span>'
            f'<span class="tc-ks-key" style="transform:rotate({45 if armed else -45}deg)"><span class="tc-ks-blade"></span><span class="tc-ks-bow"></span></span></span>'
            f'<span class="tc-ks-lbl">ARM<br>{esc(L(zh, "已确认", "armed"))}</span></div>')


def tape(c: CaseResult, zh: bool) -> str:
    v = c.confirmed_claim.values if c.confirmed_claim else None
    o = c.oracle
    rng = f"{o.low * 100:.1f}–{o.high * 100:.1f}%" if o is not None and o.low is not None else "—"
    tier = tr(o.tier_label, zh) if o is not None and o.low is not None else ""
    cond = (L(zh, "期间触及", "touch") if o.condition == "touch" else L(zh, "到期收在", "close")) if o is not None else ""
    meta = " · ".join(x for x in (f"{c.created_at:%m-%d}", c.case_id[:8].upper(), cond) if x)
    return (f'<div class="tc-tape">{mini_cassette()}'
            f'<span class="tc-tape-label"><b class="tc-serif">{esc(v.claim_text if v else "—")}</b><span>{esc(meta)}</span></span>'
            f'<span class="tc-rd tc-ph" style="align-items:flex-end"><span class="tc-vt" style="font-size:26px;line-height:24px;white-space:nowrap">{esc(rng)}</span>'
            + (f'<span class="tc-inv" style="font-size:12px;line-height:15px">{esc(tier)}</span>' if tier else "") + '</span></div>')


# ------------------------------------------------------------------ console (home) parts


def hero(zh: bool) -> str:
    return (f'<div class="tc-hero"><span class="tc-hero-1">{esc(L(zh, "一句股价观点，算成一个概率。", "One sentence about a stock, turned into a probability."))}</span>'
            f'<span class="tc-hero-2">{esc(L(zh, "每个数字都附来源与时间。", "Every number carries its source and time."))}</span></div>')


MARKS = {"claim": "✎", "data": "ⓘ", "def": "◇", "user": "·"}


def src_line(kind: str, text: str, zh: bool) -> str:
    """Under a field: where its value came from (claim text, public data, default assumption or typed by the user)."""
    word = {"claim": L(zh, "原文", "claim"), "data": L(zh, "公开数据", "public data"), "def": L(zh, "默认假设", "default"), "user": L(zh, "你填的", "typed")}[kind]
    return f'<div class="tc-fsrc"><span class="tc-mk tc-mk-{kind}">{MARKS[kind]} {esc(word)}</span> {esc(text)}</div>'


def group_head(title: str, note: str) -> str:
    return f'<div class="tc-ghead"><b class="tc-serif">{esc(title)}</b><span>{esc(note)}</span></div>'


def para(text: str, cls: str = "") -> str:
    return f'<p class="tc-para{(" " + cls) if cls else ""}">{esc(text)}</p>'


FEEDS_INFO = [
    ("CH-01 OPT", ("期权链", "Option chain"), "YAHOO", ("隐含波动率、各价位的触及概率、事件日前后的定价", "implied volatility, touch odds by price, pricing around events"), ("盘中", "intraday"), "M1"),
    ("CH-02 PRC", ("价格历史", "Price history"), "YAHOO", ("历史波动率、本股过去每个窗口的涨幅、K 线", "historical volatility, past windows of this stock, K-line"), ("每日", "daily"), "M1 · M2 · M4 · FND"),
    ("CH-03 TNX", ("10 年期美债", "10-year Treasury"), "YAHOO · ^TNX", ("无风险利率，期权与模型的折现基准", "risk-free rate for options and the model"), ("每日", "daily"), "M1 · M2"),
    ("CH-04 FIN", ("财报事实", "Reported facts"), "SEC · XBRL", ("营收、净利润、股份数，算出目标需要的业绩", "revenue, net income and shares behind the target"), ("每季", "quarterly"), "M3 · FND"),
    ("CH-05 PEER", ("同规模公司", "Peer companies"), "SEC · FRAMES", ("同规模公司过去达到所需增速的比例", "how often similar-size companies reached the growth"), ("每年", "yearly"), "M3"),
    ("CH-06 INS", ("内部人交易", "Insider trades"), "SEC · FORM 4", ("过去 12 个月公开市场的买入与卖出", "open-market buying and selling over 12 months"), ("每日", "daily"), "SIG"),
    ("CH-07 PMKT", ("预测市场", "Prediction market"), "POLYMARKET", ("相关短期价格合约的概率，仅作参考", "related short-term price contracts, context only"), ("实时", "live"), "SIG"),
    ("CH-08 SENT", ("市场情绪", "Market mood"), "CNN · FEAR & GREED", ("整体市场环境，不进入概率计算", "market environment, not used in the probability"), ("每日", "daily"), "SIG"),
]

METHODS_INFO = [
    ("M1", "TYPE-OPT", ("期权隐含", "Option-implied"), ("用期权链的隐含波动率，算期间触及和到期收在目标价以上的概率。", "Uses the option chain's implied volatility for touch and close-above odds."), ["OPT", "TNX", "PRC"]),
    ("M2", "TYPE-VOL", ("历史波动率模型", "Historical-volatility model"), ("用过去的实际波动幅度做同样的计算，与 M1 对照。", "The same calculation with past realised volatility, to compare with M1."), ["PRC", "TNX"]),
    ("M3", "TYPE-PEER", ("同规模公司基准率", "Peer base rate"), ("同规模公司里，有多少达到了目标所需的业绩增速；触及型观点只作参考。", "How many similar-size companies reached the growth the target needs; context for touch claims."), ["FIN", "PEER"]),
    ("M4", "TYPE-HIST", ("本股历史", "This stock's history"), ("这只股票过去每个同样长度的窗口，有多少次涨到过这个幅度。", "How often this stock rose this much in past windows of the same length."), ["PRC"]),
]


def feed_info(zh: bool, label: str) -> str:
    cards = "".join(
        f'<div class="tc-feedm"><div class="tc-row tc-between">{dymo(ch, small=True)}<span class="tc-row" style="gap:6px">{lamp("amber")}<b class="tc-tiny">STBY</b></span></div>'
        f'<div class="tc-row tc-between" style="gap:8px"><b class="tc-serif" style="font-size:18px">{esc(L(zh, *name))}</b>{chip(L(zh, *freq))}</div>'
        f'<span class="tc-src">{esc(src)}</span><span class="tc-serif" style="font-size:14px;line-height:1.55">{esc(L(zh, *use))}</span><span class="tc-to">→ {esc(to)}</span></div>'
        for ch, name, src, use, freq, to in FEEDS_INFO)
    return (unit_head(label, chip(L(zh, "数据全部免费 · → 表示进入哪种方法", "all data is free · → feeds which method")), "u-feed")
            + f'<div class="tc-feeds tc-feeds-wide">{cards}</div>')


def methods_info(zh: bool, label: str) -> str:
    cards = "".join(
        f'<div class="tc-cas"><div class="tc-cas-label">{STRIPES}<div class="tc-row tc-between"><b style="font-size:30px">{mid}</b><span class="tc-tiny" style="color:#4A4436">{code}</span></div>'
        f'<b class="tc-serif" style="font-size:18px;font-weight:900">{esc(L(zh, *name))}</b><span class="tc-serif" style="font-size:13px;line-height:1.55;color:#3A3428">{esc(L(zh, *text))}</span>'
        f'<div class="tc-intags">{"".join(f"<span class=tc-intag>{i}</span>" for i in inputs)}</div></div>'
        f'<div class="tc-caswin" aria-hidden="true"><span class="tc-reel tc-reel-l"><span class="tc-hub"></span></span><span class="tc-reel tc-reel-l"><span class="tc-hub"></span></span></div></div>'
        for mid, code, name, text, inputs in METHODS_INFO)
    note = f'<span class="tc-serif tc-unitnote">{esc(L(zh, "每盒磁带一种算法；黄标是它读取的数据。", "One algorithm per tape; the ochre tags are the data it reads."))}</span>'
    return unit_head(label, note, "u-mth") + f'<div class="tc-casgrid">{cards}</div>'


def footer(zh: bool) -> str:
    return (f'<div class="tc-row tc-between" style="flex-wrap:wrap;gap:12px 24px"><span class="tc-serif tc-footnote">'
            f'{esc(L(zh, "研究用途，不构成投资建议。概率为市场隐含或模型输出，不是保证。", "Research use only, not investment advice. Probabilities are market-implied or model output, not guarantees."))}</span>'
            f'<span class="tc-row" style="gap:14px"><span class="tc-stripes tc-stripes-v" aria-hidden="true"><span style="background:#C49A4A;width:22px"></span>'
            f'<span style="background:#A8623A;width:14px"></span><span style="background:#7E3B2A;width:8px"></span></span>{dymo("SYSEN 5151 · TEAM 12")}</span></div>')


# ------------------------------------------------------------------ start-up screen (the first page of a session)


EMBLEM_XL = (  # the "lit candle K-line" emblem drawn at 200 px; the flame lights, then the two K-line bars rise
    '<span class="tc-embx" aria-hidden="true"><span class="x-leaf"></span>'
    '<span class="x-patch" style="left:6px;top:106px"></span><span class="x-patch" style="left:106px;top:144px;background:#A58A55"></span>'
    '<span class="x-patch" style="left:56px;top:6px;background:#BBA16B"></span>'
    '<span class="x-art"><span class="x-rise-l"><span class="x-wick" style="left:40px;top:69px;height:81px"></span>'
    '<span class="x-yin" style="left:30px;top:88px;width:25px;height:41px"></span></span>'
    '<span class="x-rise-r"><span class="x-wick" style="left:153px;top:44px;height:94px"></span>'
    '<span class="x-yang" style="left:143px;top:59px;width:25px;height:50px"></span></span></span>'
    '<span class="x-mist" style="left:6px;top:138px;width:76px"></span><span class="x-mist" style="left:118px;top:150px;width:76px"></span>'
    '<span class="x-art"><span class="x-wick" style="left:97px;top:53px;height:24px"></span><span class="x-wax"></span>'
    '<span class="x-wick" style="left:97px;top:158px;height:24px"></span>'
    '<span class="x-ignite"><span class="x-flame"><span class="x-fl"></span><span class="x-core"></span></span></span></span></span>'
)


def boot_rows(zh: bool, *, version: str, mode: str, mode_label: str, sec_set: bool, ai_set: bool, tapes: int) -> list[tuple[str, str, str]]:
    """The self-test lines as (label, status, tone). Every status is read from the real configuration; nothing is probed."""
    ready = mode not in ("live", "record") or sec_set
    return [
        (L(zh, f"TC-8 自检 · TICKERCASE v{version}", f"TC-8 SELF-TEST · TICKERCASE v{version}"), L(zh, "开始", "START"), "am"),
        (L(zh, "数据源 08 路 · SEC YAHOO PMKT CNN", "DATA 08 CH · SEC YAHOO PMKT CNN"), L(zh, "就绪", "READY") if ready else L(zh, "受限", "LIMITED"), "am"),
        (L(zh, "方法 M1–M4 · 期权 波动率 同业 本股", "METHODS M1–M4 · OPT VOL PEER HIST"), L(zh, "就绪", "READY"), "am"),
        (L(zh, "SEC 联系邮箱", "SEC CONTACT EMAIL"), L(zh, "已设置", "SET") if sec_set else L(zh, "未设置", "NOT SET"), "am"),
        (L(zh, "CLAUDE KEY · AI 叙述（可选）", "CLAUDE KEY · AI NARRATIVE (OPTIONAL)"), L(zh, "已设置", "SET") if ai_set else L(zh, "未设置", "NOT SET"),
         "am" if ai_set else "dim"),
        (L(zh, "磁带库", "TAPE LIBRARY"), L(zh, f"{tapes} 盘", f"{tapes} TAPES"), "am"),
        (L(zh, "数据源模式", "DATA MODE"), mode_label, "am"),
    ]


def boot_top(zh: bool, *, link: str, warn: bool, clock: str) -> str:
    """Title label, the four lamps lighting in turn, and the clock."""
    lamps = [("amber", "PWR", ".1s"), (link, "LINK", ".9s"), ("green", "SYS", "1.3s"), ("red" if warn else "off", "WARN", "1.1s")]
    lamp_html = "".join(f'<span class="tc-lampcol">{lamp(k, "" if k == "off" else "tc-pop", "" if k == "off" else f"animation-delay:{d}")}{dymo(t, small=True)}</span>'
                        for k, t, d in lamps)
    return (f'<div class="tc-boottop">{dymo(L(zh, "TC-8 · 观点概率控制台 · 开机", "TC-8 · claim probability console · power on"))}'
            f'<span class="tc-grow"></span><span class="tc-lamps">{lamp_html}</span>{chip(clock)}</div>')


def boot_main(zh: bool, rows: Iterable[tuple[str, str, str]]) -> str:
    """The nameplate with the emblem, and the screen where the self-test types out (about 1.5 s in all)."""
    post = "".join(
        f'<div class="tc-post tc-type" style="animation-delay:{0.35 + i * 0.13:.2f}s"><span class="tc-dim lbl">{esc(label)}</span>'
        f'<span class="lead" aria-hidden="true">{"·" * 80}</span><span class="{"tc-am" if tone == "am" else "tc-dim"} st">{esc(status)}</span></div>'
        for i, (label, status, tone) in enumerate(rows))
    screws = "".join(f'<span class="tc-screw tc-screw-s" style="{pos};--r:{r}deg"></span>'
                     for pos, r in (("top:5px;left:5px", 30), ("top:5px;right:5px", 100), ("bottom:5px;left:5px", 160), ("bottom:5px;right:5px", 70)))
    plate = (f'<section class="tc-nameplate" aria-label="TickerCase">'
             f'<span class="tc-screw" style="top:8px;left:8px;--r:45deg"></span><span class="tc-screw" style="top:8px;right:8px;--r:135deg"></span>'
             f'<div class="tc-tile">{screws}{EMBLEM_XL}</div>'
             f'<div class="tc-col" style="align-items:center;gap:8px"><span class="tc-wm">TICKERCASE</span>'
             f'<span class="tc-wm-sub">{esc(L(zh, "灯明 · 观点概率控制台", "TC-8 · claim probability console"))}</span></div>'
             f'<span class="tc-stripes tc-stripes-v" aria-hidden="true"><span style="background:#C49A4A;width:46px"></span>'
             f'<span style="background:#A8623A;width:30px"></span><span style="background:#7E3B2A;width:18px"></span></span></section>')
    screen = (f'<div class="tc-bezel tc-bootscreen"><div class="tc-crt tc-boot" style="animation-delay:.35s;height:100%"><span class="tc-noise"></span>'
              f'<div class="tc-crt-in tc-ph" style="gap:2px">{post}'
              f'<div class="tc-pop tc-bootlead" style="animation-delay:1.3s">'
              f'<span class="tc-bootline">{esc(L(zh, "一句股价观点，算成一个概率。", "One sentence about a stock, turned into a probability."))}</span>'
              f'<span class="tc-am tc-line">{esc(L(zh, "每个数字都附来源与时间。", "Every number carries its source and time."))}</span>'
              f'<span class="tc-line">{esc(L(zh, "按 ENTER ▸ 进入控制台", "PRESS ENTER ▸ OPEN THE CONSOLE"))} <span class="tc-cur" aria-hidden="true"></span></span>'
              f'</div></div></div></div>')
    return f'<div class="tc-bootmain">{plate}{screen}</div>'


# ------------------------------------------------------------------ CSS


def page_css(nodes: Iterable[tuple[str, int]], *, flowing: bool = False) -> str:
    """Per-page rules: the junction number of each unit on the data bus, and whether pulses flow down it."""
    rules = [f'.st-key-{k}::after{{content:"{n}"}}' for k, n in nodes]
    if flowing:
        rules.append('[data-testid="stMainBlockContainer"]::before{background-image:repeating-linear-gradient(180deg,#FFB23E 0,#FFB23E 6px,transparent 6px,transparent 30px);'
                     'animation:tc-fdown .6s steps(5,end) infinite}')
    return "<style>" + "\n".join(rules) + "</style>"


# amber phosphor: the screens switch tube colour; highlights become pale amber so they still stand out
AMBER_CSS = """<style>
:root{--ph:#FFB23E;--ph-rgb:255,178,62;--am:#FFE4A8;--am-rgb:255,228,168;--dim:#C9933F;--z1:#1E160C;--z2:#291E0F;--z3:#342612;--z4:#3F2E15;
  --seg:#2E2210;--scr-text:#F1DFC0;--scr-line:#6B4E22;--scr-faint:#3A2A14}
</style>"""

# the start-up screen: the casing narrows to one centred panel without the data bus
BOOT_CSS = """<style>
[data-testid="stMainBlockContainer"]{max-width:1240px!important;margin:clamp(16px,6vh,72px) auto 40px!important;padding:22px 26px 26px!important}
[data-testid="stMainBlockContainer"]::before{display:none}
.tc-boottop{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px}
.tc-bootmain{display:flex;flex-wrap:wrap;gap:22px;align-items:stretch}
.tc-nameplate{position:relative;flex:1 1 330px;max-width:100%;box-sizing:border-box;padding:26px 24px 24px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;
  border:3px solid var(--ink);background-color:#3A3733;background-image:url(__GRAIN__);background-size:160px 160px;background-blend-mode:soft-light;color:#E3DAC0;
  box-shadow:inset 0 4px 0 #524E48,inset 0 -9px 0 #26241F,6px 6px 0 #0C0B09}
.tc-nameplate::before{content:"";position:absolute;top:9px;left:30px;width:48px;height:5px;background:rgba(255,250,235,.5);transform:skewX(-35deg);pointer-events:none}
.tc-screw{position:absolute;width:14px;height:14px;border-radius:50%;background:#A39B86;border:2px solid var(--ink);box-sizing:border-box;box-shadow:inset -2px -3px 0 #6F6858}
.tc-screw::after{content:"";position:absolute;left:50%;top:50%;width:8px;height:2px;background:var(--ink);transform:translate(-50%,-50%) rotate(var(--r,35deg))}
.tc-screw-s{width:12px;height:12px;background:#8E8672}
.tc-tile{position:relative;background:#141311;border:3px solid var(--ink);padding:24px;box-shadow:inset 0 3px 0 #26241F,inset 0 -5px 0 #0B0A09,6px 6px 0 #0C0B09}
.tc-embx{display:block;position:relative;width:200px;height:200px}
.tc-embx span{position:absolute}
.tc-embx .x-leaf{inset:0;border:3px solid var(--ink);box-sizing:border-box;background-color:#B09460;
  background-image:linear-gradient(90deg,rgba(70,52,22,.3) 2px,transparent 2px),linear-gradient(0deg,rgba(70,52,22,.3) 2px,transparent 2px);background-size:50px 50px;
  box-shadow:inset 0 5px 0 rgba(255,236,190,.35),inset 0 -7px 0 rgba(70,52,22,.35)}
.tc-embx .x-patch{width:46px;height:46px;background:#C6AD78}
.tc-embx .x-mist{height:16px;border-radius:8px;background:#DCD2B8;border:2px solid var(--ink);box-sizing:border-box;box-shadow:3px 3px 0 rgba(12,11,9,.45)}
.tc-embx .x-art{inset:0;filter:drop-shadow(4px 4px 0 rgba(12,11,9,.85))}
.tc-embx .x-wick{width:5px;background:#1B1917}
.tc-embx .x-yin{background:#1B1917}
.tc-embx .x-yang{background:#E9DFC6;border:4px solid #1B1917;box-sizing:border-box}
.tc-embx .x-wax{left:78px;top:75px;width:44px;height:84px;background:#B65A3E;box-shadow:inset -9px 0 0 #8A4330,inset 6px 0 0 #C9714F;clip-path:polygon(0 0,100% 0,84% 100%,16% 100%)}
.tc-embx .x-flame{left:84px;top:9px;width:32px;height:50px;transform-origin:50% 100%;animation:tc-flame 1s steps(1,end) infinite}
.tc-embx .x-fl{left:1px;top:9px;width:30px;height:30px;background:#C49A4A;border:3px solid var(--ink);box-sizing:border-box;border-radius:0 50% 50% 50%;transform:rotate(45deg)}
.tc-embx .x-core{left:10px;top:24px;width:12px;height:12px;background:#EFE7D2;border-radius:0 50% 50% 50%;transform:rotate(45deg)}
.tc-embx .x-ignite{inset:0;transform-origin:100px 59px;animation:tc-ignite .42s steps(5,end) .25s both}
.tc-embx .x-rise-l{inset:0;transform-origin:42px 150px;animation:tc-rise .34s steps(4,end) .5s both}
.tc-embx .x-rise-r{inset:0;transform-origin:155px 138px;animation:tc-rise .34s steps(4,end) .62s both}
.tc-wm{font-family:'IBM Plex Mono',monospace;font-size:clamp(24px,2.4vw,32px);font-weight:700;letter-spacing:.24em;padding-left:.24em;color:#F1EBDC;text-shadow:0 2px 0 var(--ink)}
.tc-wm-sub{font-family:'Noto Serif SC',serif;font-size:15px;font-weight:700;letter-spacing:.2em;color:#CFC4A6}
.tc-bootscreen{flex:999 1 560px;min-width:0}
.tc-post{display:flex;align-items:baseline;gap:12px;font-size:24px;line-height:32px;white-space:nowrap}
.tc-post .lbl{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.tc-post .lead{flex:1 1 40px;min-width:24px;overflow:hidden;color:#2E6B44!important;text-shadow:none!important}
.tc-post .st{flex:0 0 auto}
.tc-type{animation:tc-type .25s steps(3,end) both}
.tc-bootlead{margin-top:18px;padding-top:16px;border-top:2px dashed var(--scr-line);display:flex;flex-direction:column;gap:8px}
.tc-bootline{font-size:clamp(30px,3.6vw,48px);line-height:1.25}
.tc-cur{display:inline-block;width:12px;height:24px;background:currentColor;vertical-align:-4px;animation:tc-blink 1s steps(1,end) infinite}
p.tc-bootnote{max-width:560px;margin:0;color:var(--ink)!important;font-family:'Noto Serif SC',serif;font-size:14px;line-height:1.6;font-weight:700}
[class*="st-key-boot-"]{gap:26px!important}
.st-key-btn_boot_enter button{min-height:76px!important;min-width:280px}
.st-key-btn_boot_enter button p{font-size:20px!important}
.st-key-btn_boot_enter kbd{display:none}
@keyframes tc-ignite{from{transform:scale(0);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes tc-rise{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes tc-type{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@media (max-width:820px){
  [data-testid="stMainBlockContainer"]{padding:12px 12px 18px!important;margin:8px auto 30px!important}
  .tc-post{font-size:16px;line-height:24px;white-space:normal}.tc-post .lead{display:none}
  .st-key-btn_boot_enter button{min-width:0;width:100%}
}
@media (prefers-reduced-motion:reduce){.tc-type{clip-path:none!important}}
</style>""".replace("__GRAIN__", GRAIN)

CSS = """<style>
@font-face{font-family:'FusionPixel';src:url(__PIX__) format('woff2');font-display:swap}
@font-face{font-family:'VT323';src:url(__VT__) format('woff2');font-display:swap}
:root{--ph:#74FF9A;--ph-rgb:116,255,154;--am:#FFB23E;--am-rgb:255,178,62;--dim:#5FCB82;--ink:#15130F;
  --z1:#0E1F14;--z2:#11281A;--z3:#143220;--z4:#183D27;--seg:#12301C;--scr-text:#D6E8D6;--scr-line:#2E6B44;--scr-faint:#1E3A26}
/* ---------- page: desk, casing, data bus ---------- */
.stApp{background-color:#1C1A17!important;background-image:url(__GRAIN__);background-size:160px 160px;background-blend-mode:soft-light}
header[data-testid="stHeader"],[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none!important}
[data-testid="stMainBlockContainer"]{max-width:1360px!important;margin:20px auto 60px!important;padding:18px 18px 24px 96px!important;position:relative;box-sizing:border-box;
  border:3px solid var(--ink);background-color:#CFC4A6;background-image:url(__GRAIN__);background-size:160px 160px;background-blend-mode:soft-light;
  box-shadow:inset 0 5px 0 #E3DAC0,inset 0 -12px 0 #ADA184,10px 10px 0 #0C0B09;color:var(--ink)}
[data-testid="stMainBlockContainer"]::before{content:"";position:absolute;left:39px;top:0;bottom:0;width:14px;z-index:0;background-color:#1B1917;
  background-image:repeating-linear-gradient(180deg,#6E5A32 0,#6E5A32 6px,transparent 6px,transparent 30px);border-left:2px solid var(--ink);border-right:2px solid var(--ink);box-sizing:border-box}
[data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]{gap:18px;position:relative;z-index:1}
[data-testid="stMarkdownContainer"] p{line-height:1.6}
/* ---------- units (keyed containers) ---------- */
[class*="st-key-u-"]{position:relative;border:3px solid var(--ink);padding:18px 20px 22px!important;box-sizing:border-box;background-image:url(__GRAIN__);background-size:160px 160px;background-blend-mode:soft-light;gap:12px}
[class*="st-key-u-"]::before{content:"";position:absolute;top:9px;left:24px;width:48px;height:5px;background:rgba(255,250,235,.5);transform:skewX(-35deg);pointer-events:none}
[class*="st-key-u-"]::after{position:absolute;left:-73px;top:14px;width:46px;height:46px;border-radius:50%;border:3px solid var(--ink);box-sizing:border-box;background:#A98B54;
  box-shadow:inset 0 4px 0 #C6AD78,inset 0 -6px 0 #7F683C,3px 3px 0 #0C0B09;display:flex;align-items:center;justify-content:center;font-family:'VT323',monospace;font-size:30px;line-height:1;color:#2A2216;z-index:2}
[class*="st-key-u-c-"]{background-color:#3A3733;color:#E3DAC0;box-shadow:inset 0 4px 0 #524E48,inset 0 -9px 0 #26241F,6px 6px 0 #0C0B09}
[class*="st-key-u-o-"]{background-color:#5E5D3F;color:#EFE8D2;box-shadow:inset 0 4px 0 #77764F,inset 0 -9px 0 #44432D,6px 6px 0 #0C0B09}
[class*="st-key-u-b-"]{background-color:#CFC4A6;color:var(--ink);box-shadow:inset 0 4px 0 #E3DAC0,inset 0 -9px 0 #ADA184,6px 6px 0 #0C0B09}
[class*="st-key-u-r-"]{background-color:#A98B54;color:#2A2216;box-shadow:inset 0 4px 0 #C6AD78,inset 0 -9px 0 #7F683C,6px 6px 0 #0C0B09}
[class*="st-key-u-c-"] [data-testid="stMarkdownContainer"],[class*="st-key-u-o-"] [data-testid="stMarkdownContainer"],[class*="st-key-u-c-"] label,[class*="st-key-u-o-"] label{color:#E3DAC0}
[class*="st-key-u-b-"] [data-testid="stMarkdownContainer"],[class*="st-key-u-r-"] [data-testid="stMarkdownContainer"],[class*="st-key-u-b-"] label,[class*="st-key-u-r-"] label{color:var(--ink)}
[class*="st-key-u-b-"] [data-testid="stCaptionContainer"],[class*="st-key-u-b-"] [data-testid="stCaptionContainer"] p,
[class*="st-key-g-"] [data-testid="stCaptionContainer"],[class*="st-key-g-"] [data-testid="stCaptionContainer"] p{color:#4A4436!important;opacity:1}
[class*="st-key-u-c-"] [data-testid="stCaptionContainer"],[class*="st-key-u-c-"] [data-testid="stCaptionContainer"] p,
[class*="st-key-u-o-"] [data-testid="stCaptionContainer"],[class*="st-key-u-o-"] [data-testid="stCaptionContainer"] p{color:#DCD3BA!important;opacity:1}
[class*="st-key-g-"]{border:2px solid var(--ink);background:#C4B998;box-shadow:inset 0 3px 0 #D6CDB2,inset 0 -5px 0 #A4997C;padding:14px 14px 16px!important;gap:10px}
[class*="st-key-g-"] [data-testid="stMarkdownContainer"],[class*="st-key-g-"] label{color:var(--ink)}
/* side-by-side units stretch to one height */
[data-testid="stHorizontalBlock"]:has(>[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]>[class*="st-key-u-"]),
[data-testid="stHorizontalBlock"]:has(>[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]>[class*="st-key-g-"]){align-items:stretch}
[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]:has(>[class*="st-key-u-"]),
[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]:has(>[class*="st-key-g-"]){flex:1 1 auto;display:flex;flex-direction:column}
[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]>[class*="st-key-u-"],
[data-testid="stColumn"]>[data-testid="stVerticalBlock"]>[data-testid="stLayoutWrapper"]>[class*="st-key-g-"]{flex:1 1 auto}
.st-key-u-c-bridge [data-testid="stRadioOption"]{padding:3px 10px 6px!important;min-height:36px}
.st-key-u-c-bridge button[data-testid^="stBaseButton"]{min-height:36px;padding:2px 12px 6px!important}
/* the status bar stays on screen */
[data-testid="stLayoutWrapper"]:has(>.st-key-u-c-status-bar){position:sticky;top:0;z-index:60}
.st-key-u-c-status-bar{padding:10px 16px 12px!important;box-shadow:inset 0 3px 0 #524E48,inset 0 -6px 0 #26241F,6px 6px 0 #0C0B09}
.st-key-u-c-status-bar::before{display:none}
/* ---------- native widgets as hardware ---------- */
button[data-testid^="stBaseButton"]{min-height:46px;border-radius:0!important;border:3px solid var(--ink)!important;background:#D8CEB2!important;color:var(--ink)!important;
  font-weight:700!important;letter-spacing:.05em;box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -7px 0 #A99D7E,4px 4px 0 #0C0B09!important;padding:4px 14px 8px!important}
button[data-testid^="stBaseButton"] p{color:var(--ink)!important;font-weight:700!important}
button[data-testid^="stBaseButton"]:hover{background:#E2D9C0!important}
button[data-testid^="stBaseButton"]:active{transform:translate(2px,3px);box-shadow:inset 0 2px 0 #ECE4CF,inset 0 -3px 0 #A99D7E,2px 1px 0 #0C0B09!important}
button[data-testid="stBaseButton-primary"]{background:#C49A4A!important;box-shadow:inset 0 3px 0 #DDB86C,inset 0 -7px 0 #9A7634,4px 4px 0 #0C0B09!important}
button[data-testid="stBaseButton-primary"]:hover{background:#CDA455!important}
button[data-testid^="stBaseButton"]:disabled{background:#B9B09A!important;box-shadow:inset 0 2px 0 #C9C1AD,inset 0 -4px 0 #9C9482,2px 2px 0 #0C0B09!important;cursor:not-allowed;transform:none}
button[data-testid^="stBaseButton"]:disabled p{color:#5E584B!important}
button[data-testid^="stBaseButton"]:focus-visible,[data-testid="stRadioOption"]:focus-within,[data-testid="stTab"]:focus-visible{outline:3px dashed #FFB23E!important;outline-offset:3px}
[data-testid="stTextInputRootElement"],[data-testid="stTextAreaRootElement"],[data-testid="stDateInputField"]{background:#0A0D0A!important;border:3px solid var(--ink)!important;border-radius:6px!important;box-shadow:inset 0 0 0 2px #050605}
[data-testid="stDateInputField"] *{background:transparent!important}
[data-testid="stDateInputField"] [role="spinbutton"],[data-testid="stDateInputField"] [data-type="literal"]{color:var(--ph)!important;font-family:'FusionPixel','VT323',monospace!important;font-size:20px;
  text-shadow:0 0 5px rgba(var(--ph-rgb),.45)}
[data-testid="stTextInputRootElement"] input,[data-testid="stTextAreaRootElement"] textarea{background:#0A0D0A!important;color:var(--ph)!important;caret-color:var(--ph);
  font-family:'FusionPixel','VT323',monospace!important;font-size:22px!important;text-shadow:0 0 5px rgba(var(--ph-rgb),.45);-webkit-font-smoothing:none}
[data-testid="stTextAreaRootElement"] textarea{font-size:24px!important;line-height:32px!important}
[data-testid="stTextInputRootElement"] input::placeholder,[data-testid="stTextAreaRootElement"] textarea::placeholder{color:#357048!important;opacity:1;text-shadow:none}
[data-testid="stTextInputRootElement"]:focus-within,[data-testid="stTextAreaRootElement"]:focus-within{outline:3px dashed #FFB23E;outline-offset:2px}
label[data-testid="stWidgetLabel"] p{font-family:'Noto Serif SC',serif!important;font-weight:700!important;font-size:14px!important}
[data-testid="stRadioGroup"]{gap:8px;flex-wrap:wrap}
[data-testid="stRadioOption"]{margin:0!important;padding:6px 12px 9px!important;min-height:42px;box-sizing:border-box;border:3px solid var(--ink);background:#D8CEB2;cursor:pointer;
  box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -6px 0 #A99D7E,3px 3px 0 #0C0B09;align-items:center}
[data-testid="stRadioOption"]>div>div:not([data-testid="stMarkdownContainer"]){display:none!important}
[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p{color:var(--ink)!important;font-weight:700;font-size:13px;margin:0}
[data-testid="stRadioOption"][data-selected="true"]{background:#C49A4A;box-shadow:inset 0 3px 0 #DDB86C,inset 0 -6px 0 #9A7634,3px 3px 0 #0C0B09;transform:translate(1px,2px)}
.stSelectbox .react-aria-ComboBox>[role="group"]{background:#D8CEB2!important;border:3px solid var(--ink)!important;border-radius:0!important;box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -5px 0 #A99D7E,3px 3px 0 #0C0B09}
.stSelectbox .react-aria-ComboBox input{color:var(--ink)!important;font-weight:700!important;background:transparent!important}
.stSelectbox .react-aria-ComboBox button,.stSelectbox .react-aria-ComboBox svg{color:var(--ink)!important;fill:var(--ink)}
/* alerts as lamp rows: green done, amber needs attention, red error, dark lamp information */
[data-testid="stAlertContainer"]{background:#211F1C!important;border:2px solid var(--ink)!important;border-radius:6px!important;padding:10px 14px 10px 42px!important;position:relative;
  color:#E3DAC0!important;box-shadow:inset 0 2px 0 #34312C}
[data-testid="stAlertContainer"]::before{content:"";position:absolute;left:13px;top:12px;width:18px;height:18px;border-radius:50%;border:3px solid var(--ink);box-sizing:border-box;background:#FFB23E}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"])::before{background:#74FF9A}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"])::before{background:#D9392C}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"])::before{background:#3B352C}
[data-testid="stAlertContainer"] [data-testid="stMarkdownContainer"],[data-testid="stAlertContainer"] p{color:#E3DAC0!important;font-family:'Noto Serif SC',serif!important;font-size:14px}
[data-testid="stAlertContainer"] [data-testid="stIconMaterial"],[data-testid="stAlertContainer"] svg{display:none}
/* expanders, tabs as channel keys over one screen */
[data-testid="stExpander"] details{border:2px solid var(--ink)!important;border-radius:0!important;background:#2F2C29}
[data-testid="stExpander"] summary,[data-testid="stExpander"] summary p{color:#E3DAC0!important;font-weight:700}
[data-testid="stExpanderDetails"] [data-testid="stMarkdownContainer"],[data-testid="stExpanderDetails"] li{color:#E3DAC0}
[data-testid="stTabs"] [role="tablist"]{gap:10px;flex-wrap:wrap;border:0!important;box-shadow:none!important;padding-bottom:4px}
[data-testid="stTabs"] .react-aria-SelectionIndicator{display:none!important}
[data-testid="stTab"]{min-height:46px;padding:4px 16px 8px!important;border:3px solid var(--ink)!important;background:#D8CEB2!important;border-radius:0!important;
  box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -7px 0 #A99D7E,4px 4px 0 #0C0B09;display:flex;align-items:center}
[data-testid="stTab"] p{color:var(--ink)!important;font-weight:700!important;font-size:13px!important}
[data-testid="stTab"][aria-selected="true"]{background:#C49A4A!important;box-shadow:inset 0 3px 0 #DDB86C,inset 0 -7px 0 #9A7634,4px 4px 0 #0C0B09;transform:translate(1px,2px)}
[data-testid="stTabPanel"][role="tabpanel"]{position:relative;margin-top:10px;padding:18px 22px 24px!important;background:#0F120F;border:3px solid var(--ink);border-radius:18px;box-shadow:inset 0 0 0 4px #050605;overflow:hidden}
[data-testid="stTabPanel"][role="tabpanel"]::after{content:"";position:absolute;inset:0;pointer-events:none;z-index:5;background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0,rgba(0,0,0,.22) 1px,transparent 1px,transparent 3px)}
[data-testid="stTabPanel"][role="tabpanel"]::before{content:"";position:absolute;inset:-40px;pointer-events:none;z-index:6;background:#0A0D0A url(__SNOW__);background-size:200px 200px;animation:tc-snow .5s steps(1,end) both}
[data-testid="stTabPanel"] [data-testid="stMarkdownContainer"],[data-testid="stTabPanel"] li,[data-testid="stTabPanel"] label,[data-testid="stTabPanel"] [data-testid="stCaptionContainer"]{color:var(--scr-text)!important}
[data-testid="stTabPanel"] [data-testid="stCaptionContainer"]{opacity:.8}
[data-testid="stTabPanel"] strong{color:var(--ph)}
[data-testid="stTabPanel"] a{color:var(--am)!important}
[data-testid="stTabPanel"] [data-testid="stExpander"] details{background:transparent;border:2px dashed var(--scr-line)!important}
[data-testid="stTabPanel"] [data-testid="stExpander"] summary,[data-testid="stTabPanel"] [data-testid="stExpander"] summary p{color:var(--ph)!important}
/* ---------- report channels: parts drawn by the report renderers, on the channel screen ---------- */
.tc-h{font-family:'FusionPixel',monospace!important;font-size:24px;line-height:30px;color:var(--am);text-shadow:0 0 5px rgba(var(--am-rgb),.45);margin:22px 0 10px}
.tc-sub{color:var(--scr-text);opacity:.85;font-size:13px;line-height:1.6}
.tc-layer{font-family:'FusionPixel',monospace!important;font-size:12px;line-height:16px;color:var(--dim);margin:16px 0 4px;letter-spacing:.04em}
table.tc-table{width:100%;border-collapse:collapse;margin:4px 0 14px}
table.tc-table th{text-align:left;font-family:'FusionPixel',monospace!important;font-weight:400;font-size:12px;color:var(--dim);padding:6px 10px;border-bottom:2px dashed var(--scr-line)}
table.tc-table td{padding:8px 10px;border-bottom:1px dashed var(--scr-faint);vertical-align:top;line-height:1.55;color:var(--scr-text);font-family:'Noto Serif SC',serif;font-size:14px}
table.tc-table td.num{font-family:'VT323',monospace!important;font-size:22px;line-height:22px;color:var(--ph);white-space:nowrap;text-shadow:0 0 5px rgba(var(--ph-rgb),.4)}
table.tc-table th.on,table.tc-table td.on{background:rgba(var(--ph-rgb),.08)}
.tc-dotmark{display:inline-block;width:14px;height:14px;border-radius:50%;border:2px solid #050605;margin-top:4px;background:#3B352C}
.tc-dotmark.tc-good{background:var(--ph)}.tc-dotmark.tc-bad,.tc-dotmark.tc-warn{background:var(--am)}
.tc-good{color:var(--ph)}.tc-warn,.tc-bad{color:var(--am)}.tc-neutral{color:var(--dim)}
.tc-card{border:2px solid var(--scr-line);padding:12px 16px;margin:0 0 12px;color:var(--scr-text)}
.tc-card b{font-family:'FusionPixel',monospace;font-weight:400;font-size:14px}
.tc-list{margin:6px 0 0;padding-left:1.1rem;line-height:1.7;color:var(--scr-text);font-family:'Noto Serif SC',serif;font-size:14px}
.tc-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:8px 0 10px}
.tc-kpi{background:#0A0D0A;border:2px solid var(--scr-line);padding:8px 12px}
.tc-kpi .k{font-family:'FusionPixel',monospace;font-size:12px;line-height:16px;color:var(--dim)}
.tc-kpi .v{font-family:'VT323',monospace;font-size:34px;line-height:34px;color:var(--ph);text-shadow:0 0 5px rgba(var(--ph-rgb),.45)}
.tc-kpi.main .v{color:var(--am);text-shadow:0 0 5px rgba(var(--am-rgb),.45)}
.tc-gauge{position:relative;height:52px;margin:22px 4px 6px}
.tc-gauge .rail{position:absolute;top:14px;left:0;right:0;height:8px;background:var(--seg);border:1px solid #050605}
.tc-gauge .fill{position:absolute;top:14px;height:8px;background:var(--ph);opacity:.45}
.tc-gauge .mk{position:absolute;top:18px;width:12px;height:12px;transform:translate(-50%,-50%);border:2px solid #050605}
.tc-gauge .mk b{position:absolute;top:-22px;left:50%;transform:translateX(-50%);font-family:'FusionPixel',monospace;font-weight:400;font-size:12px;color:var(--scr-text);white-space:nowrap}
.tc-gauge .mk.below b{top:14px}
.tc-verdict{border-left:6px solid currentColor}.tc-verdict .lbl{font-family:'Noto Serif SC',serif;font-size:24px;font-weight:900}
/* ---------- HTML parts ---------- */
.tc-dymo{display:inline-block;background:#1B1917;color:#F1EBDC;font-weight:600;font-size:12px;letter-spacing:.16em;text-transform:uppercase;padding:4px 10px 5px;border-radius:2px;
  box-shadow:inset 0 1px 0 #34302B,2px 2px 0 #0C0B09;text-shadow:0 1px 0 #000;white-space:nowrap;font-family:'IBM Plex Mono','Noto Serif SC',monospace}
.tc-dymo-s{font-size:10px;padding:2px 6px 3px;letter-spacing:.12em}
.tc-lamp{display:inline-block;width:18px;height:18px;border-radius:50%;border:3px solid var(--ink);box-sizing:border-box;position:relative;flex:0 0 auto;vertical-align:middle}
.tc-lamp::after{content:"";position:absolute;top:2px;left:3px;width:5px;height:4px;border-radius:2px;background:rgba(255,255,255,.75)}
.tc-l-amber{background:#FFB23E}.tc-l-green{background:#74FF9A}.tc-l-red{background:#D9392C}.tc-l-off{background:#3B352C}.tc-l-off::after{background:rgba(255,255,255,.16)}
.tc-chip{display:inline-block;background:#0A0D0A;border:2px solid var(--ink);padding:3px 7px;font-family:'FusionPixel',monospace!important;font-size:12px;line-height:14px;color:#FFB23E;
  text-shadow:0 0 4px rgba(255,178,62,.4);overflow-wrap:anywhere}
.tc-lrow{display:flex;align-items:center;gap:10px;font-family:'Noto Serif SC',serif;font-size:14px;font-weight:700}
.tc-subhead{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;padding-bottom:6px;border-bottom:2px solid rgba(21,19,15,.45);margin-top:4px}
.tc-ph,.tc-ph span,.tc-ph div{font-family:'FusionPixel','VT323',monospace!important;color:var(--ph);text-shadow:0 0 5px rgba(var(--ph-rgb),.45);-webkit-font-smoothing:none}
.tc-ph .tc-am,.tc-am{color:var(--am)!important;text-shadow:0 0 5px rgba(var(--am-rgb),.45)}
.tc-ph .tc-dim,.tc-dim{color:var(--dim)!important;text-shadow:none}
.tc-ph .tc-vt,.tc-vt{font-family:'VT323','FusionPixel',monospace!important}
.tc-inv,.tc-ph .tc-inv{background:var(--ph);color:#0A0D0A!important;text-shadow:none!important;padding:1px 10px}
.tc-serif,.tc-serif *{font-family:'Noto Serif SC',serif!important}
.tc-row{display:flex;align-items:center}.tc-col{display:flex;flex-direction:column;gap:8px}.tc-between{justify-content:space-between}.tc-grow{flex:1 1 10px}
.tc-tiny{font-size:11px;font-weight:700;letter-spacing:.1em}.tc-small{font-size:12px;line-height:16px}
.tc-uhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}
.tc-anchor{position:relative;top:-96px;display:block;height:0;visibility:hidden}
.tc-note{font-size:12px;font-weight:600;letter-spacing:.06em;color:#4A4436;margin-top:12px}
.tc-unitnote{font-size:14px;font-weight:700;color:#4A4436}
.tc-para{font-family:'Noto Serif SC',serif;font-size:14px;line-height:1.65;margin:0}
.tc-engr{text-shadow:0 1px 0 rgba(255,245,215,.55)}
.tc-bridge{display:flex;flex-wrap:wrap;align-items:center;gap:14px 22px}
.tc-brand{display:flex;align-items:center;gap:16px}.tc-brand-t{display:flex;flex-direction:column;gap:4px}.tc-brand-t b{font-size:24px;letter-spacing:.22em;color:#F1EBDC}
.tc-brand-t span{font-family:'Noto Serif SC',serif!important;font-size:13px;font-weight:700;letter-spacing:.14em;color:#CFC4A6}
.tc-lamps{display:flex;gap:14px;align-items:flex-end}.tc-lampcol{display:flex;flex-direction:column;align-items:center;gap:5px}
.tc-status{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}
.tc-status>b{font-size:16px;color:#F1EBDC}.tc-warnlink{display:flex;align-items:center;gap:8px;text-decoration:none}
.tc-jumps{display:flex;flex-wrap:wrap;gap:8px}
.tc-jump{display:inline-flex;align-items:center;min-height:38px;padding:0 10px 3px;border:3px solid var(--ink);background:#D8CEB2;color:var(--ink)!important;font-size:12px;font-weight:700;
  letter-spacing:.06em;text-decoration:none!important;box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -6px 0 #A99D7E,3px 3px 0 #0C0B09;align-self:flex-start}
.tc-jump:hover{background:#E2D9C0}.tc-jump:active{transform:translate(2px,3px)}
.tc-rd{background:#0A0D0A;border:3px solid var(--ink);border-radius:8px;padding:6px 10px 8px;display:flex;flex-direction:column;gap:2px;position:relative;overflow:hidden;box-shadow:inset 0 0 0 2px #050605;min-width:0}
.tc-rd.tc-row{flex-direction:row;align-items:center;gap:10px;padding:4px 12px}
.tc-rd::before{content:"";position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.3) 0,rgba(0,0,0,.3) 1px,transparent 1px,transparent 3px)}
h1.tc-claim{font-family:'Noto Serif SC',serif!important;font-weight:900!important;font-size:clamp(30px,4vw,52px)!important;line-height:1.15!important;margin:0 0 14px!important;
  color:var(--ink)!important;text-shadow:0 1px 0 rgba(255,245,215,.55);padding:0!important;letter-spacing:0}
.tc-grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tc-grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.tc-bezel{background:#141311;border:3px solid var(--ink);padding:12px;box-shadow:inset 0 3px 0 #26241F}
.tc-crt{position:relative;background:#0A0D0A;border:3px solid var(--ink);border-radius:24px;overflow:hidden;box-shadow:inset 0 0 0 5px #050605}
.tc-crt::before{content:"";position:absolute;inset:0;z-index:3;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.3) 0,rgba(0,0,0,.3) 1px,transparent 1px,transparent 3px)}
.tc-crt::after{content:"";position:absolute;inset:0;z-index:4;pointer-events:none;background:radial-gradient(ellipse at 50% 48%,transparent 60%,rgba(0,0,0,.6) 100%)}
.tc-noise{position:absolute;inset:0;z-index:2;pointer-events:none;background-image:url(__SNOW__);background-size:200px 200px;opacity:.05;mix-blend-mode:screen}
.tc-crt-in{position:relative;z-index:1;padding:clamp(16px,2.4vw,30px);display:flex;flex-direction:column}
.tc-bigrow{display:flex;flex-wrap:wrap;gap:14px 30px;align-items:flex-end}
.tc-huge{font-size:clamp(96px,11vw,170px)!important;line-height:.82!important;letter-spacing:-.02em}
.tc-tier{font-size:24px!important;line-height:34px!important;align-self:flex-start}
.tc-nodata{font-size:30px!important;line-height:40px!important}
.tc-line{font-size:22px;line-height:28px}
.tc-cells{display:grid;grid-template-columns:repeat(50,minmax(0,1fr));gap:2px}
.tc-cell{display:block;height:28px;border:1px solid #050605}
.tc-mcells{display:grid;grid-template-columns:repeat(25,minmax(0,1fr));gap:1px}
.tc-mcell{display:block;height:12px;border:1px solid #050605}
.z1{background:var(--z1)}.z2{background:var(--z2)}.z3{background:var(--z3)}.z4{background:var(--z4)}
.tc-cell.lit,.tc-mcell.lit{background:var(--ph);box-shadow:0 0 6px rgba(var(--ph-rgb),.55)}
.tc-marks{position:relative;height:26px}
.tc-mark{position:absolute;top:0;transform:translateX(-50%);font-size:22px;line-height:26px;white-space:nowrap}
.tc-tick{position:absolute;top:4px;transform:translateX(-50%);font-size:12px;line-height:16px;white-space:nowrap}
.tc-zones{display:grid;grid-template-columns:22.4fr 22.3fr 26fr 29.3fr;font-size:12px;line-height:16px;border-top:2px dashed var(--scr-line);padding-top:6px;gap:4px}
.tc-prb{display:flex;flex-direction:column;gap:26px}
.tc-prb-screen{min-width:0}.tc-prb-methods{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;align-items:stretch}
.tc-strip{position:relative;display:flex;flex-direction:column;gap:8px;padding:10px 12px 12px;background:#2B2926;border:3px solid var(--ink);box-shadow:inset 0 3px 0 #45413C,inset 0 -6px 0 #1C1A18,4px 4px 0 #0C0B09}
.tc-strip.tc-off{opacity:.6}
.tc-stub{position:absolute;left:50%;top:-28px;margin-left:-7px;width:14px;height:26px;background:#1B1917;border:2px solid var(--ink);box-sizing:border-box;overflow:hidden}
.tc-stub::after{content:"";position:absolute;inset:1px;background-image:repeating-linear-gradient(0deg,#FFB23E 0,#FFB23E 5px,transparent 5px,transparent 12px);animation:tc-fup .5s steps(4,end) infinite}
.tc-tag{font-size:10px;font-weight:700;letter-spacing:.1em;color:#B5AC93}
.tc-basis{font-family:'Noto Serif SC',serif!important;font-size:13px;line-height:1.5;color:#D6CFB8}
.tc-minicas{width:74px;height:46px;background:#141311;border:2px solid var(--ink);display:inline-flex;align-items:center;justify-content:space-around;flex:0 0 auto}
.tc-reel{width:26px;height:26px;border-radius:50%;background:#59493F;border:2px solid var(--ink);display:flex;align-items:center;justify-content:center;box-sizing:border-box}
.tc-reel-s{width:20px;height:20px}.tc-reel-l{width:36px;height:36px}
.tc-hub{width:60%;height:60%;border-radius:50%;background:repeating-conic-gradient(#D8CEB2 0 18deg,#1B1917 18deg 60deg);border:2px solid var(--ink);box-sizing:border-box}
.tc-feeds{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.tc-feeds-wide{grid-template-columns:repeat(auto-fill,minmax(270px,1fr))}
.tc-feedm{display:flex;flex-direction:column;gap:6px;padding:12px 12px 14px;background:#2F2C29;border:2px solid var(--ink);box-shadow:inset 0 2px 0 #46423C,inset 0 -5px 0 #201E1B;min-width:0;color:#E3DAC0}
.tc-src{font-size:11px;letter-spacing:.04em;color:#CFC4A6;overflow-wrap:anywhere}.tc-to{font-size:12px;font-weight:700;letter-spacing:.06em;color:#F1EBDC}
.tc-sigrow{display:grid;grid-template-columns:auto minmax(0,1fr);gap:12px;align-items:start;padding:10px 0;border-top:2px solid #26241F;font-family:'Noto Serif SC',serif!important;font-size:15px;line-height:1.55;color:#E3DAC0}
.tc-sigrow *{font-family:'Noto Serif SC',serif!important}.tc-sigrow b{letter-spacing:.06em}
.tc-fndname{font-size:34px;font-weight:900;line-height:1.2;color:#1E1912}.tc-fndtext{font-size:14px;line-height:1.6;margin:8px 0 12px;color:#221C14!important}
.tc-slot{height:20px;background:#1B1917;border:3px solid var(--ink);box-shadow:inset 0 -6px 0 #0E0D0C}
.tc-paperwrap{overflow:hidden;margin:-4px 28px 0}
.tc-paper{position:relative;background-color:#EDE3CC;background-image:repeating-linear-gradient(180deg,#EDE3CC 0,#EDE3CC 64px,#DFE4D0 64px,#DFE4D0 128px);padding:18px 60px 30px;color:#2A2622}
.tc-holes{position:absolute;top:0;bottom:0;width:34px;background-image:radial-gradient(circle,#1C1A17 0,#1C1A17 5px,transparent 6px);background-size:34px 28px;background-position:center 8px}
.tc-ptitle{font-family:'FusionPixel',monospace!important;font-size:12px;line-height:16px;color:#5A5244;padding:14px 0 4px;border-bottom:2px dashed #A99F86}
.tc-prow{display:flex;gap:14px;align-items:flex-start;padding:12px 0;border-bottom:1px dashed #B9AF95}
.tc-ptext{font-family:'FusionPixel',monospace!important;font-size:24px;line-height:30px;color:#2A2622}
.tc-pfacts{font-family:'FusionPixel',monospace!important;font-size:12px;line-height:16px;color:#6B6252}
.tc-seal{display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:38px;padding:0 4px;box-sizing:border-box;border:2px solid #8E3B2B;color:#8E3B2B;
  font-family:'Noto Serif SC',serif!important;font-weight:900;font-size:17px;transform:rotate(-6deg);background:rgba(142,59,43,.06);flex:0 0 auto}
.tc-seal-bad{border-color:#D9392C;color:#D9392C}
.tc-segs{display:grid;gap:3px}
.tc-seg{display:block;height:14px;border:1px solid #050605;background:var(--seg)}
.tc-seg.on{background:var(--ph);box-shadow:0 0 6px rgba(var(--ph-rgb),.55)}.tc-seg.run{background:var(--am)}.tc-seg.bad{background:#D9392C}
.tc-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 18px}
.tc-prow2{display:flex;align-items:center;gap:10px;font-size:12px;line-height:16px}
.tc-blink{animation:tc-blink .5s steps(1,end) infinite}
.tc-ks-wrap{display:flex;align-items:center;gap:12px}
.tc-ks-lbl{font-size:11px;font-weight:700;letter-spacing:.1em;line-height:1.3;text-align:center;color:var(--ink)}
.tc-ks{position:relative;width:88px;height:88px;flex:0 0 auto}
.tc-ks-plate{position:absolute;inset:0;border-radius:50%;background:#A98B54;border:3px solid var(--ink);box-sizing:border-box;box-shadow:inset 0 5px 0 #C6AD78,inset 0 -8px 0 #7F683C,4px 4px 0 #0C0B09}
.tc-ks-ring{position:absolute;inset:14px;border-radius:50%;background:#2E2B28;border:3px solid var(--ink);box-sizing:border-box;box-shadow:inset -5px -5px 0 #1E1C19,inset 4px 4px 0 #46423C}
.tc-ks-key{position:absolute;left:50%;top:50%;width:0;height:0}
.tc-ks-blade{position:absolute;left:-5px;top:-26px;width:10px;height:38px;background:#D8D2C2;border:3px solid var(--ink);box-sizing:border-box;box-shadow:inset -3px 0 0 #9C968A}
.tc-ks-bow{position:absolute;left:-19px;top:-50px;width:38px;height:26px;border-radius:50% 50% 40% 40%;background:#CFC4A6;border:3px solid var(--ink);box-sizing:border-box;box-shadow:inset 0 3px 0 #E3DAC0,inset 0 -5px 0 #ADA184}
.tc-tape{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px 16px;align-items:center;padding:10px 14px 12px;background:#2B2926;border:3px solid var(--ink);
  box-shadow:inset 0 3px 0 #45413C,inset 0 -6px 0 #1C1A18,4px 4px 0 #0C0B09;color:#E3DAC0}
.tc-tape-label{display:flex;flex-direction:column;gap:4px;min-width:0;background:#EDE3CC;color:var(--ink);border:2px solid var(--ink);padding:6px 10px 8px}
.tc-tape-label b{font-size:15px;line-height:1.35;overflow-wrap:anywhere}.tc-tape-label span{font-size:11px;font-weight:600;letter-spacing:.06em;color:#4A4436}
.tc-casgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}
.tc-cas{background:#2B2926;border:3px solid var(--ink);padding:12px 12px 14px;box-shadow:inset 0 3px 0 #45413C,inset 0 -7px 0 #1C1A18,6px 6px 0 #0C0B09;display:flex;flex-direction:column;gap:10px}
.tc-cas-label{background:#EDE3CC;border:2px solid var(--ink);padding:0 12px 12px;display:flex;flex-direction:column;gap:6px;color:var(--ink);flex:1 1 auto}
.tc-stripes{display:flex;flex-direction:column;gap:2px;margin:0 -12px 6px}.tc-stripes span{height:5px;display:block}
.tc-stripes-v{flex-direction:row;gap:3px;margin:0}.tc-stripes-v span{height:18px;border:2px solid var(--ink)}
.tc-intags{display:flex;flex-wrap:wrap;gap:6px;padding-top:6px;border-top:1px dashed #8C826A;margin-top:auto}
.tc-intag{display:inline-block;padding:2px 7px;border:2px solid var(--ink);background:#C49A4A;color:var(--ink);font-size:11px;font-weight:700;letter-spacing:.06em;box-shadow:2px 2px 0 #0C0B09}
.tc-caswin{height:48px;background:#141311;border:2px solid var(--ink);display:flex;align-items:center;justify-content:space-between;padding:0 16%}
.tc-hero{display:flex;flex-direction:column;gap:6px;margin:2px 0 4px}
.tc-hero-1{font-family:'Noto Serif SC',serif;font-weight:900;font-size:clamp(26px,3.2vw,40px);line-height:1.2;color:#F1EBDC}
.tc-hero-2{font-family:'Noto Serif SC',serif;font-weight:700;font-size:15px;color:#CFC4A6;letter-spacing:.06em}
.tc-fsrc{font-size:11px;line-height:1.45;color:#4A4436;margin-top:-8px;overflow-wrap:anywhere}
.tc-mk{display:inline-block;padding:0 5px;border:2px solid var(--ink);font-weight:700;font-size:10px;letter-spacing:.04em;margin-right:2px}
.tc-mk-claim{background:#C49A4A;color:var(--ink)}.tc-mk-data{background:#77764F;color:#F1EBDC}.tc-mk-def{background:#A8623A;color:#F1EBDC}.tc-mk-user{background:#D8CEB2;color:var(--ink)}
.tc-ghead{display:flex;flex-direction:column;gap:2px;margin-bottom:2px}.tc-ghead b{font-size:17px;color:var(--ink)}.tc-ghead span{font-size:12px;color:#4A4436;font-family:'Noto Serif SC',serif}
.tc-footnote{font-size:14px;font-weight:700;color:#EFE8D2}
.tc-emb{position:relative;width:64px;height:64px;flex:0 0 auto}
.tc-emb-leaf{position:absolute;inset:0;border:2px solid var(--ink);background-color:#B09460;background-image:linear-gradient(90deg,rgba(70,52,22,.35) 1px,transparent 1px),linear-gradient(0deg,rgba(70,52,22,.35) 1px,transparent 1px);
  background-size:16px 16px;box-shadow:4px 4px 0 #0C0B09}
.tc-emb-patch{position:absolute;width:15px;height:15px;background:#C6AD78}
.tc-emb-mist{position:absolute;height:6px;border-radius:3px;background:#DCD2B8;box-shadow:2px 2px 0 rgba(12,11,9,.5)}
.tc-emb-art{position:absolute;inset:0;filter:drop-shadow(2px 2px 0 rgba(12,11,9,.85))}
.tc-wick{position:absolute;width:2px;background:#1B1917}.tc-yin{position:absolute;background:#1B1917}.tc-yang{position:absolute;background:#E9DFC6;border:2px solid #1B1917;box-sizing:border-box}
.tc-cbody{position:absolute;left:25px;top:24px;width:14px;height:27px;background:#B65A3E;box-shadow:inset -3px 0 0 #8A4330;clip-path:polygon(0 0,100% 0,84% 100%,16% 100%)}
.tc-flame{position:absolute;left:27px;top:3px;width:10px;height:16px;transform-origin:50% 100%;animation:tc-flame 1s steps(1,end) infinite}
.tc-fl{position:absolute;left:0;top:3px;width:10px;height:10px;background:#C49A4A;border-radius:0 50% 50% 50%;transform:rotate(45deg)}
.tc-core{position:absolute;left:3px;top:8px;width:4px;height:4px;background:#EFE7D2;border-radius:0 50% 50% 50%;transform:rotate(45deg)}
/* ---------- motion: stepped, first view of a new report only ---------- */
.tc-boot{animation:tc-boot 1s steps(1,end) both}
.tc-pop{animation:tc-lit .1s steps(1,end) both}
.tc-spin .tc-hub{animation:tc-spin .5s steps(6,end) 2}
.tc-feed{animation:tc-feed .8s steps(10,end) .3s both}
@keyframes tc-boot{0%{opacity:0}8.33%{opacity:1}16.67%{opacity:.15}25%{opacity:1}100%{opacity:1}}
@keyframes tc-lit{from{opacity:.12}to{opacity:1}}
@keyframes tc-spin{to{transform:rotate(360deg)}}
@keyframes tc-feed{from{transform:translateY(-60%)}to{transform:translateY(0)}}
@keyframes tc-fdown{from{background-position:0 0}to{background-position:0 30px}}
@keyframes tc-fup{from{background-position:0 0}to{background-position:0 -12px}}
@keyframes tc-blink{0%{opacity:1}50%{opacity:.25}100%{opacity:.25}}
@keyframes tc-flame{0%{transform:scale(1,1)}25%{transform:scale(.9,1.06)}50%{transform:scale(1.05,.94)}75%{transform:scale(.94,1.03)}100%{transform:scale(1,1)}}
@keyframes tc-snow{0%{background-position:0 0;opacity:1}16.7%{background-position:-60px 30px}33.3%{background-position:40px -50px}50%{background-position:-90px -20px}66.7%{background-position:70px 60px}83.3%{background-position:-30px 90px}100%{background-position:0 0;opacity:0}}
/* ---------- narrow screens: the bus folds away, everything stacks ---------- */
@media (max-width:820px){
  [data-testid="stMainBlockContainer"]{padding:12px 12px 18px!important;margin:8px auto 30px!important}
  [data-testid="stMainBlockContainer"]::before,[class*="st-key-u-"]::after,.tc-stub{display:none}
  [class*="st-key-u-"]{padding:14px 14px 18px!important}
  .tc-steps{grid-template-columns:1fr}.tc-paper{padding:14px 40px 24px}.tc-paperwrap{margin:-4px 8px 0}.tc-holes{width:26px}
  .tc-ptext{font-size:18px;line-height:24px}.tc-huge{font-size:84px!important}.tc-mark{font-size:16px}
  .tc-tape{grid-template-columns:minmax(0,1fr) auto}.tc-tape .tc-minicas{display:none}
  .tc-zones{font-size:10px}
  [data-testid="stLayoutWrapper"]:has(>.st-key-u-c-status-bar){position:static}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}[data-testid="stTabPanel"][role="tabpanel"]::before{display:none}}
</style>""".replace("__PIX__", PIX).replace("__VT__", VT).replace("__GRAIN__", GRAIN).replace("__SNOW__", SNOW)
