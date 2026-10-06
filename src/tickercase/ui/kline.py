"""K-line (candlestick) chart for the result page, drawn on a CRT screen.

``kline_html`` returns a self-contained HTML document for ``st.iframe``:
daily / weekly / monthly candles with a timeframe switch, a hover or keyboard
cursor that reads one bar, the claim's target line and a future zone up to the
deadline that shows only the computed probabilities (no forecast path).

Up and down bars differ by shape (hollow / solid), not colour, so red stays
reserved for warning lamps. A snapshot without OHLC (closes only) is drawn as a
close line in the same frame; nothing is filled in.
"""

from __future__ import annotations

import html
import json
from datetime import date
from typing import Optional

from ..models import MarketSnapshot

# relative to the page, so the chart also works under a server base path (the iframe inherits the page's base URL)
FONT_PIXEL = "app/static/fonts/fusion-pixel-12px-monospaced-zh_hans.otf.woff2"
FONT_VT323 = "app/static/fonts/VT323-Regular.woff2"
SNOW = "app/static/textures/snow.png"
AMBER = ":root{--ph:#FFB23E;--ph-rgb:255,178,62;--am:#FFE4A8;--dim:#C9933F}"


def _series(market: MarketSnapshot, last_day: Optional[date]) -> dict:
    """Bars as compact rows; bars after ``last_day`` (later than the case's reference close) are dropped."""
    def keep(d: date) -> bool:
        return last_day is None or d <= last_day

    if market.kline is not None and market.kline.daily:
        pack = {}
        for key, bars in (("D", market.kline.daily), ("W", market.kline.weekly), ("M", market.kline.monthly)):
            rows = [[b.day.isoformat(), b.open, b.high, b.low, b.close, b.volume or 0] for b in bars if keep(b.day)]
            if rows:
                pack[key] = rows
        if pack:
            return {"mode": "candles", "data": pack}
    line = [[p.day.isoformat(), float(p.close)] for p in market.price_series if keep(p.day)]
    return {"mode": "line", "data": {"L": line}}


def _week52(market: MarketSnapshot, last_day: Optional[date]) -> tuple[Optional[float], Optional[float]]:
    if market.kline is None or not market.kline.daily:
        return None, None
    bars = [b for b in market.kline.daily if last_day is None or b.day <= last_day][-252:]
    if not bars:
        return None, None
    return max(b.high for b in bars), min(b.low for b in bars)


def kline_html(market: MarketSnapshot, *, target: float, deadline: date, zh: bool, ticker: str,
               last_day: Optional[date] = None, touch_text: str = "", end_text: str = "", anim: bool = True,
               phosphor: str = "grn") -> str:
    series = _series(market, last_day)
    hi52, lo52 = _week52(market, last_day)
    t = (lambda z, e: z) if zh else (lambda z, e: e)
    labels = {
        "tf": {"D": t("日K", "Daily"), "W": t("周K", "Weekly"), "M": t("月K", "Monthly"), "L": t("收盘线", "Close line")},
        "open": t("开", "O"), "high": t("高", "H"), "low": t("低", "L"), "close": t("收", "C"), "vol": t("量", "Vol"),
        "volUnit": t("亿股", "M sh"), "log": t("对数刻度", "log scale"), "lin": t("线性刻度", "linear scale"),
        "to": t("至", "to"), "w52": t("52 周 高 {h} / 低 {l}", "52-wk high {h} / low {l}"),
        "target": t("目标", "Target"), "now": t("NOW", "NOW"),
        "futM": t("{d} 目标日 · 按比例", "{d} deadline · to scale"), "futX": t("→ {d} · 未来不按比例", "→ {d} · future not to scale"),
        "legend": t("空心 = 阳线（收 ≥ 开）· 实心 = 阴线 · 虚线 = 目标价 · 悬停或用 ◀ ▶ 查看每根 K 线",
                    "Hollow = up bar (close ≥ open) · solid = down bar · dashed = target · hover or use ◀ ▶ to read a bar"),
        "lineNote": t("这份数据只有收盘价，画收盘线，不补造 K 线。", "This source has closes only, so a close line is drawn; no bars are made up."),
        "prev": t("上一根", "Previous bar"), "next": t("下一根", "Next bar"),
        "aria": t("{tk} {tf}：{a} 至 {b}，最新收盘 {c}；目标价 {t}。", "{tk} {tf}: {a} to {b}, latest close {c}; target {t}."),
    }
    cfg = {
        "series": series, "target": target, "deadline": deadline.isoformat(), "ticker": ticker,
        "hi52": hi52, "lo52": lo52, "touch": touch_text, "end": end_text, "L": labels, "zh": zh,
        "years": max(0.0, (deadline - (last_day or deadline)).days / 365.25),
    }
    data = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    boot = "animation:boot 1s steps(1,end) both;" if anim else ""
    page = _TEMPLATE
    for key, value in (("__TUBE__", AMBER if phosphor == "amb" else ""), ("__BOOT__", boot), ("__PIX__", FONT_PIXEL), ("__VT__", FONT_VT323),
                       ("__SNOW__", SNOW), ("__LANG__", "zh-CN" if zh else "en"), ("__PREV__", html.escape(labels["prev"])),
                       ("__NEXT__", html.escape(labels["next"]))):
        page = page.replace(key, value)
    return page.replace("__CFG__", data)  # data last, so nothing in it is taken for a placeholder


_TEMPLATE = r"""<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8">
<style>
@font-face{font-family:'FusionPixel';src:url(__PIX__) format('woff2');font-display:swap}
@font-face{font-family:'VT323';src:url(__VT__) format('woff2');font-display:swap}
:root{--ph:#74FF9A;--ph-rgb:116,255,154;--am:#FFB23E;--dim:#5FCB82}
__TUBE__
html,body{margin:0;height:100%;background:transparent;overflow:hidden}
body{font-family:'FusionPixel','VT323',monospace;color:var(--ph);-webkit-font-smoothing:none;display:flex;flex-direction:column}
.bezel{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;background:#141311;border:3px solid #15130F;padding:12px;box-shadow:inset 0 3px 0 #26241F}
.crt{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;position:relative;background:#0A0D0A;border:3px solid #15130F;border-radius:22px;overflow:hidden;box-shadow:inset 0 0 0 5px #050605;__BOOT__}
.crt::before{content:"";position:absolute;inset:0;z-index:5;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.3) 0,rgba(0,0,0,.3) 1px,transparent 1px,transparent 3px)}
.crt::after{content:"";position:absolute;inset:0;z-index:6;pointer-events:none;background:radial-gradient(ellipse at 50% 48%,transparent 60%,rgba(0,0,0,.6) 100%)}
.in{flex:1 1 auto;min-height:0;position:relative;z-index:1;padding:14px 16px 10px;display:flex;flex-direction:column;gap:8px}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 18px;font-size:12px;line-height:16px;text-shadow:0 0 5px rgba(var(--ph-rgb),.45)}
.dim{color:var(--dim);text-shadow:none}.am{color:var(--am);text-shadow:0 0 5px rgba(255,178,62,.45)}
.chart{position:relative;flex:1 1 auto;min-height:150px}
.grid{position:absolute;left:0;right:60px;height:0;border-top:1px dashed rgba(var(--ph-rgb),.22)}
.gl{position:absolute;right:0;transform:translateY(-50%);font-size:12px;line-height:14px;color:var(--dim);padding:0 3px;background:#0A0D0A}
.fut{position:absolute;top:0;bottom:0;right:60px;border-left:2px dashed rgba(var(--ph-rgb),.5);background:repeating-linear-gradient(135deg,rgba(var(--ph-rgb),.06) 0,rgba(var(--ph-rgb),.06) 2px,transparent 2px,transparent 10px)}
.fut span{position:absolute;left:8px;right:6px;font-size:12px;line-height:14px}
.tgt{position:absolute;left:0;right:60px;height:0;border-top:2px dashed var(--am);z-index:1}
.tag{position:absolute;right:0;transform:translateY(-50%);font-size:12px;line-height:16px;padding:1px 5px;z-index:2;background:#0A0D0A;border:1px solid currentColor}
.tag.inv{background:var(--ph);color:#0A0D0A;border-color:var(--ph)}
.bars{position:absolute;left:0;top:0;bottom:0;display:flex;filter:drop-shadow(0 0 3px rgba(var(--ph-rgb),.4))}
.slot{position:relative;flex:1 1 0;min-width:0;height:100%;cursor:crosshair}
.slot.hov::before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(var(--ph-rgb),.5)}
.w{position:absolute;left:calc(50% - 1px);width:2px;background:var(--ph)}
.b{position:absolute;left:18%;right:18%;box-sizing:border-box}
.b.up{border:2px solid var(--ph);background:#0A0D0A}.b.dn{background:var(--ph)}
.line{position:absolute;left:calc(50% - 2px);width:4px;height:4px;background:var(--ph)}
.vol{flex:0 0 auto;display:flex;align-items:flex-end;height:50px;border-top:1px dashed rgba(var(--ph-rgb),.22)}
.vs{flex:1 1 0;min-width:0;height:100%;display:flex;align-items:flex-end;justify-content:center}
.vb{display:block;width:64%;background:rgba(var(--ph-rgb),.4)}
.axis{flex:0 0 auto;position:relative;height:16px}
.axis span{position:absolute;top:0;transform:translateX(-50%);font-size:12px;line-height:14px;white-space:nowrap;color:var(--dim)}
.snow{position:absolute;inset:-40px;z-index:7;pointer-events:none;background:#0A0D0A url(__SNOW__);background-size:200px 200px;animation:snow .5s steps(1,end) both}
.ctl{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;padding:12px 4px 6px;font-family:'IBM Plex Mono','Noto Serif SC',monospace}
.key{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 12px;box-sizing:border-box;border:3px solid #15130F;background:#D8CEB2;color:#15130F;font:700 12px 'IBM Plex Mono','Noto Serif SC',monospace;letter-spacing:.06em;cursor:pointer;box-shadow:inset 0 3px 0 #ECE4CF,inset 0 -7px 0 #A99D7E,4px 4px 0 #0C0B09}
.key:active{transform:translate(2px,3px);box-shadow:inset 0 2px 0 #ECE4CF,inset 0 -3px 0 #A99D7E,2px 1px 0 #0C0B09}
.key.on{background:#C49A4A;box-shadow:inset 0 3px 0 #DDB86C,inset 0 -7px 0 #9A7634,4px 4px 0 #0C0B09}
.key:focus-visible{outline:3px dashed #FFB23E;outline-offset:3px}
.lamp{display:inline-block;width:14px;height:14px;border-radius:50%;border:2px solid #15130F;box-sizing:border-box;background:#3B352C}
.key.on .lamp{background:#FFB23E}
.legend{font-family:'Noto Serif SC',serif;font-size:13px;line-height:1.5;color:#D6CFB8}
@keyframes boot{0%{opacity:0}8.33%{opacity:1}16.67%{opacity:.15}25%{opacity:1}100%{opacity:1}}
@keyframes snow{0%{background-position:0 0}16.7%{background-position:-60px 30px}33.3%{background-position:40px -50px}50%{background-position:-90px -20px}66.7%{background-position:70px 60px}83.3%{background-position:-30px 90px}100%{background-position:0 0;opacity:0}}
@media (max-width:560px){.fut .note{display:none}.in{padding:10px 10px 8px}}
@media (prefers-reduced-motion: reduce){.crt,.snow{animation:none!important}.snow{display:none}}
</style></head><body>
<div class="bezel"><div class="crt" id="crt"><div class="in">
  <div class="top"><span id="read"></span><span class="dim" id="meta"></span></div>
  <div class="chart" id="chart" role="img"></div>
  <div class="vol" id="vol" aria-hidden="true"></div>
  <div class="axis" id="axis" aria-hidden="true"></div>
</div></div></div>
<div class="ctl">
  <span id="tfs" role="group"></span>
  <button type="button" class="key" id="prev" aria-label="__PREV__">◀</button>
  <button type="button" class="key" id="next" aria-label="__NEXT__">▶</button>
  <span class="legend" id="legend"></span>
</div>
<script>
const C = __CFG__;
const L = C.L;
const fmt = (s, o) => s.replace(/\{(\w+)\}/g, (_, k) => o[k]);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const keys = C.series.mode === 'line' ? ['L'] : ['D', 'W', 'M'].filter((k) => C.series.data[k]);
let tf = keys.includes('M') ? 'M' : keys[keys.length - 1];
let hov = null;
const el = (id) => document.getElementById(id);
function render(snow) {
  const ser = C.series.data[tf];
  const n = ser.length;
  const line = tf === 'L';
  const isLog = tf === 'M' || tf === 'W' || (line && ser.length > 300);
  const f = isLog ? Math.log : (x) => x;
  const lows = ser.map((r) => line ? r[1] : r[3]), highs = ser.map((r) => line ? r[1] : r[2]);
  const lo = Math.min(...lows), hi = Math.max(C.target, ...highs);
  const span = f(hi) - f(lo) || 1;
  const fmin = f(lo) - span * 0.05, fmax = f(hi) + span * 0.1;
  const Y = (p) => (1 - (f(p) - fmin) / (fmax - fmin)) * 100;
  const fut = tf === 'M' ? Math.min(0.45, (C.years * 12) / (C.years * 12 + n)) : 0.16;
  const right = 'calc(60px + (100% - 60px) * ' + fut.toFixed(4) + ')';
  const h = hov == null || hov >= n ? n - 1 : hov;
  const vmax = line ? 1 : Math.max(1, ...ser.map((r) => r[5]));
  let bars = '', vols = '';
  ser.forEach((r, i) => {
    const cls = 'slot' + (i === h ? ' hov' : '');
    if (line) {
      bars += '<span class="' + cls + '" data-i="' + i + '"><span class="line" style="top:calc(' + Y(r[1]).toFixed(2) + '% - 2px)"></span></span>';
      vols += '<span class="vs"></span>';
      return;
    }
    const [, o, hh, l, c, v] = r;
    const bt = Y(Math.max(o, c)), bb = Y(Math.min(o, c));
    bars += '<span class="' + cls + '" data-i="' + i + '"><span class="w" style="top:' + Y(hh).toFixed(2) + '%;height:' + Math.max(0.5, Y(l) - Y(hh)).toFixed(2) + '%"></span>'
      + '<span class="b ' + (c >= o ? 'up' : 'dn') + '" style="top:' + bt.toFixed(2) + '%;height:' + Math.max(0.7, bb - bt).toFixed(2) + '%"></span></span>';
    vols += '<span class="vs"><span class="vb" style="height:' + Math.max(2, (v / vmax) * 100).toFixed(1) + '%"></span></span>';
  });
  const levels = isLog ? [1, 2, 5, 10, 20, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000] : null;
  let grid = '';
  const lv = levels ? levels : (() => { const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4))) * 2; const a = []; for (let p = Math.ceil(lo / step) * step; p < hi; p += step) a.push(+p.toFixed(6)); return a; })();
  lv.filter((p) => p > lo && p < hi && Math.abs(p - C.target) / C.target > 0.03).forEach((p) => {
    grid += '<span class="grid" style="top:' + Y(p).toFixed(2) + '%"></span><span class="gl" style="top:' + Y(p).toFixed(2) + '%">' + p + '</span>';
  });
  const last = line ? ser[n - 1][1] : ser[n - 1][4];
  const tTop = Y(C.target).toFixed(2), lTop = Y(last).toFixed(2);
  const futLabel = fmt(tf === 'M' ? L.futM : L.futX, { d: C.deadline });
  const notes = (C.touch ? '<span class="am note" style="top:' + tTop + '%;transform:translateY(-120%)">' + esc(C.touch) + '</span>' : '')
    + (C.end ? '<span class="dim note" style="top:' + tTop + '%;transform:translateY(35%)">' + esc(C.end) + '</span>' : '');
  el('chart').innerHTML = grid
    + '<div class="fut" style="width:calc((100% - 60px) * ' + fut.toFixed(4) + ')">' + notes + '<span class="dim" style="bottom:6px;text-align:right">' + esc(futLabel) + '</span></div>'
    + '<span class="tgt" style="top:' + tTop + '%"></span><span class="tag am" style="top:' + tTop + '%">' + esc(L.target + ' ' + C.target) + '</span>'
    + '<span class="tag inv" style="top:' + lTop + '%">' + last.toFixed(2) + '</span>'
    + '<div class="bars" style="right:' + right + '">' + bars + '</div>';
  el('vol').style.marginRight = right;
  el('vol').innerHTML = vols;
  el('axis').style.marginRight = right;
  let ticks = '';
  ser.forEach((r, i) => {
    const d = r[0];
    const show = tf === 'M' ? d.slice(5, 7) === '01' : tf === 'W' ? i % 13 === 0 : tf === 'D' ? i % 20 === 0 : i % Math.max(1, Math.round(n / 6)) === 0;
    if (show) ticks += '<span style="left:' + (((i + 0.5) / n) * 100).toFixed(2) + '%">' + (tf === 'M' ? d.slice(0, 4) : tf === 'D' ? d.slice(5) : d.slice(0, 7)) + '</span>';
  });
  el('axis').innerHTML = ticks;
  const r = ser[h];
  if (line) {
    el('read').textContent = r[0] + ' · ' + L.close + ' ' + r[1].toFixed(2);
  } else {
    const chg = ((r[4] - r[1]) / r[1]) * 100;
    const vol = C.zh ? (r[5] / 1e8).toFixed(2) + ' ' + L.volUnit : (r[5] / 1e6).toFixed(1) + ' ' + L.volUnit;
    el('read').textContent = r[0] + ' · ' + L.open + ' ' + r[1].toFixed(2) + ' ' + L.high + ' ' + r[2].toFixed(2) + ' ' + L.low + ' ' + r[3].toFixed(2) + ' ' + L.close + ' ' + r[4].toFixed(2)
      + ' · ' + (chg >= 0 ? '+' : '') + chg.toFixed(1) + '% · ' + L.vol + ' ' + vol;
  }
  const w52 = C.hi52 != null ? ' · ' + fmt(L.w52, { h: C.hi52.toFixed(2), l: C.lo52.toFixed(2) }) : '';
  el('meta').textContent = L.tf[tf] + ' · ' + ser[0][0] + ' ' + L.to + ' ' + ser[n - 1][0] + ' · ' + (isLog ? L.log : L.lin) + w52;
  el('chart').setAttribute('aria-label', fmt(L.aria, { tk: C.ticker, tf: L.tf[tf], a: ser[0][0], b: ser[n - 1][0], c: last.toFixed(2), t: C.target }));
  el('legend').textContent = line ? L.lineNote : L.legend;
  el('tfs').innerHTML = keys.map((k) => '<button type="button" class="key' + (k === tf ? ' on' : '') + '" data-tf="' + k + '" aria-pressed="' + (k === tf) + '"><span class="lamp"></span>' + esc(L.tf[k]) + '</button>').join(' ');
  if (snow) { const s = document.createElement('div'); s.className = 'snow'; el('crt').appendChild(s); setTimeout(() => s.remove(), 520); }
}
el('chart').addEventListener('mouseover', (e) => { const s = e.target.closest('.slot'); if (s && +s.dataset.i !== hov) { hov = +s.dataset.i; render(false); } });
el('chart').addEventListener('mouseleave', () => { if (hov !== null) { hov = null; render(false); } });
el('tfs').addEventListener('click', (e) => { const b = e.target.closest('[data-tf]'); if (b && b.dataset.tf !== tf) { tf = b.dataset.tf; hov = null; render(true); } });
el('prev').addEventListener('click', () => { const n = C.series.data[tf].length; hov = Math.max(0, (hov == null ? n - 1 : hov) - 1); render(false); });
el('next').addEventListener('click', () => { const n = C.series.data[tf].length; hov = Math.min(n - 1, (hov == null ? n - 1 : hov) + 1); render(false); });
render(false);
</script></body></html>"""
