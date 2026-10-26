"""Market structure (v0.11): how much of a recent move came with the market, with similar stocks, and from the stock.

Factor model over daily log returns of adjusted closes, on the dates the stock and SPY share:
    r_t = a + b_m * m_t + b_p * u_t + e_t,      u_t = p_t - c * m_t - d
m is SPY, p the equal-weighted mean of the peers kept in the basket, and u the basket's move beyond its
own market exposure. u is uncorrelated with m inside the estimation window, so b_m is the stock's plain
market beta and b_p its exposure to what moves the peers but not the market (sector news, flows in and
out of a theme).

Windows: the betas come from the 120 trading days before the explained window (the last 20 trading
days), so the days being explained do not set their own betas. Over the explained window
    market = b_m * sum m_t,   peers = b_p * sum (p_t - c * m_t),   own = total - market - peers,
without intercepts: 20 days are too short for a drift estimate to mean anything. The log parts are
scaled by (e^L - 1) / L, so the three parts add up to the window's simple return.

Peers stay in the basket when their daily returns correlated at least 0.3 with the stock over the
estimation window; the others are listed with their correlation and left out. A day is flagged when its
own part exceeds twice the residual standard deviation of the estimation window; SEC filings dated
since the previous close are listed with it.

This splits the price move statistically; it does not name a cause. Peers have news of their own, and a
stock-specific day can still belong to a theme.

shifted_range() recomputes the headline methods at another price ("if the market and peer parts
returned"), a scenario that the page labels as such. probability_change() explains how the probability
moved since the last run of the same claim, with a Shapley split over price parts, volatility, time and
rate that does not depend on the order of the steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Callable, Optional, Sequence

from .models import (
    CaseResult,
    NewsItem,
    Reaction,
    ChangePart,
    DataMode,
    FilingRecord,
    MarketStructure,
    MethodChange,
    OracleSummary,
    PeerInfo,
    ProbabilityChange,
    RegimeReading,
    ShiftedRange,
    StructureDay,
    Text,
    ValidatedClaim,
)
from .oracle import MIN_INDEPENDENT_WINDOWS, build_oracle, independent_windows, p_end_above, p_touch

ESTIMATION_DAYS = 120
WINDOW_DAYS = 20
MIN_PEER_CORRELATION = 0.3
FLAG_SD = 2.0
LOW_R_SQUARED = 0.2
SAME_DEADLINE_DAYS = 92  # runs of the same claim may move the deadline a little ("in 3 years" from another day)
MARKET = "SPY"
REGIME = (("QQQ", Text(en="Nasdaq-100 (QQQ)", zh="纳斯达克 100（QQQ）")), ("IWM", Text(en="Russell 2000 (IWM)", zh="罗素 2000（IWM）")),
          ("^VIX", Text(en="VIX", zh="VIX 波动率指数")), ("^VIX3M", Text(en="3-month VIX", zh="3 个月 VIX")))

Series = Sequence[tuple[date, float]]


def T(en: str, zh: str) -> Text:
    return Text(en=en, zh=zh)


@dataclass
class FactorFit:
    """The aligned daily data and the fitted betas; kept in memory for the change attribution, not stored."""

    dates: list[date]  # len(r) + 1 closes
    r: list[float]
    m: list[float]
    p: Optional[list[float]]
    beta_market: float
    beta_peers: float
    peers_market_beta: float
    alpha: float
    peers_alpha: float
    r_squared: float
    residual_sd: float
    used_peers: list[str] = field(default_factory=list)
    inputs: Optional["StructureInputs"] = None  # the full series, for reactions to events before the window (v0.12)

    def parts(self, i0: int, i1: int) -> tuple[float, float]:
        """(market, peers) log parts of the returns after close i0 up to close i1."""
        mk = self.beta_market * sum(self.m[i0:i1])
        pp = self.beta_peers * sum(self.p[t] - self.peers_market_beta * self.m[t] for t in range(i0, i1)) if self.p is not None else 0.0
        return mk, pp


@dataclass
class StructureInputs:
    symbol: str
    stock: Series
    market: Series
    peers: dict[str, Series] = field(default_factory=dict)
    peer_notes: dict[str, Text] = field(default_factory=dict)  # peers that could not be fetched
    regime: dict[str, Series] = field(default_factory=dict)
    peer_source: Optional[str] = None
    source_urls: list[str] = field(default_factory=list)
    retrieved_at: Optional[datetime] = None
    data_mode: DataMode = DataMode.LIVE


def _mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def _cov(a: Sequence[float], b: Sequence[float]) -> float:
    ma, mb = _mean(a), _mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)


def _corr(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    va, vb = _cov(a, a), _cov(b, b)
    if va <= 0 or vb <= 0:
        return None
    return _cov(a, b) / math.sqrt(va * vb)


def _log_returns(closes: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def carino(log_total: float) -> float:
    """Scale that turns additive log parts into parts of the simple return: (e^L - 1) / L."""
    return math.expm1(log_total) / log_total if abs(log_total) > 1e-12 else 1.0


def _value_on(series: Series, day: date) -> Optional[tuple[date, float]]:
    """The last close on or before ``day``."""
    best = None
    for d, v in series:
        if d > day:
            break
        best = (d, v)
    return best


def fit_factors(inp: StructureInputs, *, estimation: int = ESTIMATION_DAYS, window: int = WINDOW_DAYS) -> tuple[FactorFit, list[PeerInfo], list[Text]]:
    stock, market = dict(inp.stock), dict(inp.market)
    common = sorted(set(stock) & set(market))
    need = estimation + window + 1
    if len(common) < need:
        raise ValueError(f"{inp.symbol} and {MARKET} share {len(common)} trading days; the split needs {need}")
    dates = common[-need:]
    r = _log_returns([stock[d] for d in dates])
    m = _log_returns([market[d] for d in dates])
    est = slice(0, estimation)
    notes: list[Text] = []

    peer_infos: list[PeerInfo] = []
    kept: list[list[float]] = []
    used_syms: list[str] = []
    for sym, series in inp.peers.items():
        prices = dict(series)
        if not all(d in prices for d in dates):
            peer_infos.append(PeerInfo(symbol=sym, note=T("not enough daily history for the estimation window", "日线历史不够估计窗口")))
            continue
        pr = _log_returns([prices[d] for d in dates])
        rho = _corr(r[est], pr[est])
        used = rho is not None and rho >= MIN_PEER_CORRELATION
        peer_infos.append(PeerInfo(
            symbol=sym, correlation=rho, window_return=prices[dates[-1]] / prices[dates[estimation]] - 1, used=used,
            note=None if used else T(f"daily correlation with the stock below {MIN_PEER_CORRELATION:g}; left out of the basket",
                                     f"与本股的日相关系数低于 {MIN_PEER_CORRELATION:g}，不放进篮子")))
        if used:
            kept.append(pr)
            used_syms.append(sym)
    for sym, why in inp.peer_notes.items():
        peer_infos.append(PeerInfo(symbol=sym, note=why))

    var_m = _cov(m[est], m[est])
    if var_m <= 0:
        raise ValueError(f"{MARKET} did not move in the estimation window")
    b_m = _cov(r[est], m[est]) / var_m
    p = [_mean(day) for day in zip(*kept)] if kept else None
    c = d0 = b_p = 0.0
    u = [0.0] * len(r)
    if p is not None:
        c = _cov(p[est], m[est]) / var_m
        d0 = _mean(p[est]) - c * _mean(m[est])
        u = [p[t] - c * m[t] - d0 for t in range(len(r))]
        var_u = _cov(u[est], u[est])
        b_p = _cov(r[est], u[est]) / var_u if var_u > 0 else 0.0
    elif not inp.peers:
        notes.append(T("No peer basket was given, so the sector part is not separated: it stays in the stock's own part.",
                       "没有给出同类篮子，所以分不出板块部分：它留在个股部分里。"))
    else:
        notes.append(T("No peer moved closely enough with the stock, so the sector part is not separated: it stays in the stock's own part.",
                       "没有一只同类股票与本股走势足够接近，所以分不出板块部分：它留在个股部分里。"))
    a = _mean(r[est]) - b_m * _mean(m[est]) - b_p * _mean(u[est])
    res = [r[t] - a - b_m * m[t] - b_p * u[t] for t in range(estimation)]
    sst = sum((x - _mean(r[est])) ** 2 for x in r[est])
    ssr = sum(x * x for x in res)
    k = 3 if p is not None else 2
    fit = FactorFit(dates=dates, r=r, m=m, p=p, beta_market=b_m, beta_peers=b_p, peers_market_beta=c, alpha=a, peers_alpha=d0,
                    r_squared=1 - ssr / sst if sst > 0 else 0.0, residual_sd=math.sqrt(ssr / max(estimation - k, 1)),
                    used_peers=used_syms, inputs=inp)
    if fit.r_squared < LOW_R_SQUARED:
        notes.append(T(f"The market and the peers explain only {fit.r_squared * 100:.0f}% of this stock's daily moves, so most of any move lands in its own part.",
                       f"大盘和同类只能解释这只股票日波动的 {fit.r_squared * 100:.0f}%，所以大部分涨跌都会落在个股部分。"))
    return fit, peer_infos, notes


def _news_day(n: NewsItem) -> date:
    """The trading day a headline can move: its US Eastern date (UTC minus 4 hours, close enough for daily bars)."""
    return (n.published - timedelta(hours=4)).date()


def reaction(fit: FactorFit, day: date, *, days: int = 2) -> Optional[Reaction]:
    """The stock's move from the last close before ``day`` to the close of the second trading day from it, and its own part.

    Two days, because news that comes out after the close moves the next session. The own part uses the betas of
    the fit: market and peers where every series has both closes, market only where a peer is missing, none before
    SPY's history. Returns None when no trading day on or after ``day`` is in the data yet.
    """
    inp = fit.inputs
    if inp is None:
        return None
    stock = dict(inp.stock)
    dates = sorted(stock)
    before = [d for d in dates if d < day]
    after = [d for d in dates if d >= day]
    if not before or not after:
        return None
    d0, d1 = before[-1], after[min(days, len(after)) - 1]
    total = math.log(stock[d1] / stock[d0])
    market = dict(inp.market)
    if d0 not in market or d1 not in market:
        return Reaction(start=d0, end=d1, days=min(days, len(after)), stock=math.expm1(total))
    mk = math.log(market[d1] / market[d0])
    own = total - fit.beta_market * mk
    basis = "market"
    peer_moves = [math.log(dict(inp.peers[s])[d1] / dict(inp.peers[s])[d0]) for s in fit.used_peers
                  if d0 in dict(inp.peers[s]) and d1 in dict(inp.peers[s])]
    if fit.used_peers and len(peer_moves) == len(fit.used_peers):
        own -= fit.beta_peers * (sum(peer_moves) / len(peer_moves) - fit.peers_market_beta * mk)
        basis = "market+peers"
    return Reaction(start=d0, end=d1, days=min(days, len(after)), stock=math.expm1(total), own=math.expm1(own), basis=basis)


def _filing_label(f: FilingRecord) -> str:
    items = ", ".join(x.strip() for x in (f.items or "").split(",") if x.strip() and x.strip() != "9.01")
    return f"{f.form_type} {f.filing_date}" + (f" ({items})" if items else "")


def build_structure(inp: StructureInputs, filings: Sequence[FilingRecord] = (), *, news: Sequence[NewsItem] = (),
                    estimation: int = ESTIMATION_DAYS, window: int = WINDOW_DAYS) -> tuple[MarketStructure, FactorFit]:
    fit, peers, notes = fit_factors(inp, estimation=estimation, window=window)
    dates, r, m = fit.dates, fit.r, fit.m
    w0 = estimation
    log_total = sum(r[w0:])
    log_market, log_peers = fit.parts(w0, len(r))
    k = carino(log_total)
    days = []
    for t in range(w0, len(r)):
        mk, pp = fit.parts(t, t + 1)
        own = r[t] - mk - pp
        prev, day = dates[t], dates[t + 1]
        near = [_filing_label(f) for f in filings if prev < f.filing_date <= day]
        flagged = abs(own) > FLAG_SD * fit.residual_sd
        heads = [f"{n.publisher}: {n.title}" if n.publisher else n.title for n in news if flagged and prev < _news_day(n) <= day][:4]
        days.append(StructureDay(day=day, stock=math.expm1(r[t]), market=mk, peers=pp, own=own, headlines=heads,
                                 flagged=flagged, filings=near))
    regime = [RegimeReading(symbol=MARKET, label=T("S&P 500 (SPY)", "标普 500（SPY）"), last=dict(inp.market)[dates[-1]],
                            change=math.expm1(sum(m[w0:])))]
    for sym, label in REGIME:
        series = inp.regime.get(sym)
        end = _value_on(series, dates[-1]) if series else None
        start = _value_on(series, dates[w0]) if series else None
        if end is None:
            continue
        vix = sym.startswith("^")
        change = None if start is None else (end[1] - start[1] if vix else end[1] / start[1] - 1)
        regime.append(RegimeReading(symbol=sym, label=label, last=end[1], change=change))
    structure = MarketStructure(
        symbol=inp.symbol, window_start=dates[w0], window_end=dates[-1], window_days=window,
        estimation_start=dates[0], estimation_end=dates[w0], estimation_days=estimation,
        beta_market=fit.beta_market, beta_peers=fit.beta_peers if fit.p is not None else None,
        peers_market_beta=fit.peers_market_beta if fit.p is not None else None, r_squared=fit.r_squared, residual_sd=fit.residual_sd,
        total=math.expm1(log_total), market_part=log_market * k, peer_part=log_peers * k, own_part=(log_total - log_market - log_peers) * k,
        log_total=log_total, log_market=log_market, log_peers=log_peers, peers=peers, peer_source=inp.peer_source, days=days,
        regime=regime, notes=notes, source_urls=inp.source_urls, retrieved_at=inp.retrieved_at or datetime.now(), data_mode=inp.data_mode)
    return structure, fit


# ---------------------------------------------------------------- the headline at another price


def counted(oracle: OracleSummary, method_id: str, price_rate) -> bool:
    if method_id == "M4" and independent_windows(price_rate) < MIN_INDEPENDENT_WINDOWS:
        return False
    return not (method_id == "M3" and oracle.condition == "touch")


def shifted_range(claim: ValidatedClaim, oracle: OracleSummary, spot_to: float, *, market, options, base_rate, price_rate,
                  closes: Optional[Sequence[tuple[date, float]]], risk_free, today: date) -> ShiftedRange:
    """The same methods with the price moved to ``spot_to``; M3 does not depend on the price, M4 is recounted from monthly closes."""
    from .providers.market import price_base_rate, price_touch_hits

    moved_rate = price_rate
    if price_rate is not None and closes:
        required = claim.target_price / Decimal(str(spot_to)) - 1
        windows, hits, _, _ = price_base_rate(list(closes), price_rate.window_months, required)
        moved_rate = price_rate.model_copy(update={"hits": hits, "rate": hits / windows if windows else None,
                                                   "touch_hits": price_touch_hits(list(closes), price_rate.window_months, required)})
    moved = market.model_copy(update={"last_close": Decimal(str(round(spot_to, 6)))}) if market is not None else None
    o = build_oracle(claim, market=moved, options=options, base_rate=base_rate, price_rate=moved_rate, risk_free=risk_free, today=today)
    touch = o.condition == "touch"
    values = {m.id: (m.touch_probability if touch else m.probability) for m in o.methods if m.status == "ok"}
    values = {k: v for k, v in values.items() if v is not None and counted(o, k, moved_rate)}
    return ShiftedRange(spot_from=float(oracle.spot or 0), spot_to=spot_to, low=o.low, high=o.high, methods=values)


# ---------------------------------------------------------------- why the probability moved since the last run


def find_previous(cases: Sequence[CaseResult], claim: ValidatedClaim, oracle: OracleSummary, *, market_date: date,
                  exclude: str) -> Optional[CaseResult]:
    """The latest earlier run of the same claim (ticker, target, condition, deadline within about three months) on older data."""
    for c in sorted(cases, key=lambda x: x.created_at, reverse=True):
        if c.case_id == exclude or c.oracle is None or c.confirmed_claim is None or c.market is None or c.oracle.spot is None:
            continue
        v = c.confirmed_claim.values
        if v.ticker != claim.ticker or v.target_price != claim.target_price or c.oracle.condition != oracle.condition:
            continue
        if abs((c.oracle.target_date - oracle.target_date).days) > SAME_DEADLINE_DAYS or c.market.last_date >= market_date:
            continue
        return c
    return None


def shapley(players: Sequence[str], value: Callable[[frozenset], float]) -> dict[str, float]:
    n = len(players)
    out = {}
    for p in players:
        others = [q for q in players if q != p]
        total = 0.0
        for size in range(n):
            weight = math.factorial(size) * math.factorial(n - size - 1) / math.factorial(n)
            for subset in combinations(others, size):
                s = frozenset(subset)
                total += weight * (value(s | {p}) - value(s))
        out[p] = total
    return out


def _years_left(target_date: date, today: date) -> float:
    return max((target_date - today).days / 365.25, 1 / 365.25)


def _pp(x: float) -> str:
    return f"{x * 100:+.1f}"


def probability_change(previous: CaseResult, *, oracle: OracleSummary, fit: Optional[FactorFit], today: date) -> ProbabilityChange:
    prev = previous.oracle
    touch = oracle.condition == "touch"
    prob = p_touch if touch else p_end_above
    s0, s1 = float(prev.spot), float(oracle.spot)
    y0, y1 = _years_left(prev.target_date, previous.created_at.date()), _years_left(oracle.target_date, today)
    r0 = float(prev.risk_free_rate) if prev.risk_free_rate is not None else 0.0
    r1 = float(oracle.risk_free_rate) if oracle.risk_free_rate is not None else 0.0
    target = float(oracle.target_price)
    d0, d1 = previous.market.last_date, (fit.dates[-1] if fit is not None else None)

    price_parts: dict[str, float] = {}
    if fit is not None and d0 >= fit.dates[0]:
        i0 = max(i for i, d in enumerate(fit.dates) if d <= d0)
        mk, pp = fit.parts(i0, len(fit.r))
        price_parts = {"market": mk, "peers": pp, "own": math.log(s1 / s0) - mk - pp}
    if not price_parts:
        price_parts = {"price": math.log(s1 / s0)}

    methods: list[MethodChange] = []
    for mid, vol0, vol1 in (("M1", prev.implied_volatility, oracle.implied_volatility), ("M2", prev.base_volatility, oracle.base_volatility)):
        ok0 = any(m.id == mid and m.status == "ok" for m in prev.methods)
        ok1 = any(m.id == mid and m.status == "ok" for m in oracle.methods)
        if not (ok0 and ok1 and vol0 and vol1):
            continue

        def f(s: frozenset, vol0=vol0, vol1=vol1) -> float:
            spot = s0 * math.exp(sum(v for k, v in price_parts.items() if k in s))
            return prob(spot, target, vol1 if "volatility" in s else vol0, y1 if "time" in s else y0, r1 if "rate" in s else r0)

        players = list(price_parts) + ["volatility", "time", "rate"]
        phi = shapley(players, f)
        lab = {"M1": T("implied volatility", "隐含波动率"), "M2": T("historical volatility", "历史波动率")}[mid]
        details = {
            "market": T(f"the market part of the price move ({math.expm1(price_parts.get('market', 0)) * 100:+.1f}%)",
                        f"股价变动中跟随大盘的部分（{math.expm1(price_parts.get('market', 0)) * 100:+.1f}%）"),
            "peers": T(f"the part that moved with the peer basket ({math.expm1(price_parts.get('peers', 0)) * 100:+.1f}%)",
                       f"跟随同类篮子的部分（{math.expm1(price_parts.get('peers', 0)) * 100:+.1f}%）"),
            "own": T(f"the stock's own part ({math.expm1(price_parts.get('own', 0)) * 100:+.1f}%)",
                     f"个股自身的部分（{math.expm1(price_parts.get('own', 0)) * 100:+.1f}%）"),
            "price": T(f"price {s0:,.2f} → {s1:,.2f} (not split: the daily data does not reach back to {d0})",
                       f"股价 {s0:,.2f} → {s1:,.2f}（日线数据不够早，无法拆分到 {d0}）"),
            "volatility": T(f"{lab.en} {vol0 * 100:.0f}% → {vol1 * 100:.0f}%", f"{lab.zh} {vol0 * 100:.0f}% → {vol1 * 100:.0f}%"),
            "time": T(f"time left {y0:.2f} → {y1:.2f} years", f"剩余时间 {y0:.2f} → {y1:.2f} 年"),
            "rate": T(f"risk-free rate {r0 * 100:.2f}% → {r1 * 100:.2f}%", f"无风险利率 {r0 * 100:.2f}% → {r1 * 100:.2f}%"),
        }
        methods.append(MethodChange(method=mid, before=f(frozenset()), after=f(frozenset(players)),
                                    parts=[ChangePart(key=k, delta=phi[k], detail=details[k]) for k in players]))
    note = None
    if not methods:
        note = T("M1 and M2 could not be compared with the previous run (a method was missing in one of them).",
                 "无法与上一次比较 M1 和 M2（其中一次缺少这个方法）。")
    return ProbabilityChange(
        previous_case_id=previous.case_id, previous_created_at=previous.created_at, previous_market_date=d0,
        market_date=d1 or today, condition=oracle.condition, previous_low=prev.low, previous_high=prev.high, low=oracle.low, high=oracle.high,
        spot_before=s0, spot_after=s1, price_split="price" not in price_parts, methods=methods, note=note)

