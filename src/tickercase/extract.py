"""Rule-based extraction of ticker, target price and horizon from a claim sentence (OA.2-OA.3 without AI).

Supports Chinese and English phrasing such as
  "TSLA 在2030年股价达到1000一股", "$NVDA will hit $300 in 3 years",
  "特斯拉五年后翻倍", "Apple to $250 by end of 2027".
Every extracted value carries the text it came from. Nothing here is final:
the page shows the values for review, and the user confirms them (OA.4-OA.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from .models import ClaimExtraction

# common company names -> ticker (Chinese and English); explicit tickers in the text take precedence
ALIASES: dict[str, str] = {
    "特斯拉": "TSLA", "tesla": "TSLA", "苹果": "AAPL", "apple": "AAPL", "英伟达": "NVDA", "辉达": "NVDA", "nvidia": "NVDA",
    "微软": "MSFT", "microsoft": "MSFT", "谷歌": "GOOGL", "google": "GOOGL", "alphabet": "GOOGL", "亚马逊": "AMZN", "amazon": "AMZN",
    "脸书": "META", "facebook": "META", "奈飞": "NFLX", "网飞": "NFLX", "netflix": "NFLX", "台积电": "TSM", "tsmc": "TSM",
    "阿里巴巴": "BABA", "阿里": "BABA", "alibaba": "BABA", "京东": "JD", "拼多多": "PDD", "百度": "BIDU", "baidu": "BIDU",
    "蔚来": "NIO", "小鹏": "XPEV", "理想汽车": "LI", "超威": "AMD", "英特尔": "INTC", "intel": "INTC", "博通": "AVGO", "broadcom": "AVGO",
    "甲骨文": "ORCL", "oracle": "ORCL", "高通": "QCOM", "qualcomm": "QCOM", "美光": "MU", "micron": "MU", "伯克希尔": "BRK-B",
    "berkshire": "BRK-B", "摩根大通": "JPM", "可口可乐": "KO", "coca-cola": "KO", "麦当劳": "MCD", "星巴克": "SBUX", "starbucks": "SBUX",
    "迪士尼": "DIS", "disney": "DIS", "波音": "BA", "boeing": "BA", "耐克": "NKE", "nike": "NKE", "沃尔玛": "WMT", "walmart": "WMT",
    "好市多": "COST", "costco": "COST", "礼来": "LLY", "辉瑞": "PFE", "pfizer": "PFE", "palantir": "PLTR", "火箭实验室": "RKLB",
    "rocket lab": "RKLB", "优步": "UBER", "uber": "UBER", "爱彼迎": "ABNB", "airbnb": "ABNB", "赛富时": "CRM", "salesforce": "CRM",
    "adobe": "ADBE", "shopify": "SHOP", "coinbase": "COIN", "微策略": "MSTR", "strategy": "MSTR", "罗宾汉": "HOOD", "robinhood": "HOOD",
    "超微电脑": "SMCI", "snowflake": "SNOW", "synthetic example": "SYNT",
}
NOT_TICKERS = {
    "USD", "US", "USA", "PE", "PS", "EPS", "AI", "CEO", "CFO", "IPO", "ETF", "GDP", "YOY", "QOQ", "TTM", "FY", "Q", "EV",
    "THE", "AND", "OR", "TO", "BY", "IN", "AT", "OF", "ON", "IT", "IS", "BE", "I", "A", "K", "M", "B", "T", "X", "HK", "HKD",
    "CNY", "RMB", "SEC", "NYSE", "NASDAQ", "IMO", "LOL", "ATH",
}
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
EN_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "a": 1, "an": 1, "half a": 0.5}

NUM = r"\d[\d,]*(?:\.\d+)?"
CN_NUM = r"[零〇一二两三四五六七八九十百千万]+"
TICKER_RE = re.compile(r"(?<![A-Za-z])\$?([A-Z]{1,5}(?:[.\-][A-Z])?)(?![A-Za-z])")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20|21)\d{2})(?!\d)\s*(?:年)?\s*(底|末|年底|年末|上半年|年中|初|年初)?")


def cn_to_number(text: str) -> Optional[Decimal]:
    """一千 -> 1000, 两千五百 -> 2500, 十 -> 10, 三 -> 3."""
    if not text or any(ch not in CN_DIGITS and ch not in CN_UNITS for ch in text):
        return None
    total, section, digit = 0, 0, None
    for ch in text:
        if ch in CN_DIGITS:
            digit = CN_DIGITS[ch]
        else:
            unit = CN_UNITS[ch]
            if unit == 10000:
                total += (section + (digit or 0)) * unit
                section, digit = 0, None
            else:
                section += (digit if digit is not None else 1) * unit
                digit = None
    return Decimal(total + section + (digit or 0))


def parse_number(text: str, suffix: str = "") -> Optional[Decimal]:
    text = text.strip()
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        value = cn_to_number(text)
        if value is None:
            return None
    s = (suffix or "").strip().lower()
    if s in ("k", "千"):
        value *= 1000
    elif s == "万":
        value *= 10000
    return value


@dataclass
class _Found:
    value: object
    text: str
    start: int
    priority: int = 0
    extra: dict = field(default_factory=dict)


def _strip_years(text: str) -> str:
    """Blank out year numbers so they are not read as prices."""
    return YEAR_RE.sub(lambda m: " " * len(m.group(0)), text)


def find_ticker(text: str, is_known: Optional[Callable[[str], bool]]) -> Optional[_Found]:
    candidates: list[_Found] = []
    for m in TICKER_RE.finditer(text):
        sym = m.group(1).upper()
        cashtag = m.group(0).startswith("$")
        if sym in NOT_TICKERS and not cashtag:
            continue
        if len(sym) == 1 and not cashtag:
            continue
        known = is_known(sym) if is_known else None
        if known is False:
            continue
        candidates.append(_Found(sym, m.group(0), m.start(), priority=3 if cashtag else 2 if known else 1))
    lowered = text.lower()
    for name, sym in ALIASES.items():
        idx = lowered.find(name)
        if idx < 0:
            continue
        if name.isascii():  # whole-word match for English names
            before = lowered[idx - 1] if idx > 0 else " "
            after = lowered[idx + len(name)] if idx + len(name) < len(lowered) else " "
            if before.isalnum() or after.isalnum():
                continue
        candidates.append(_Found(sym, text[idx: idx + len(name)], idx, priority=1, extra={"alias": True}))
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (-f.priority, f.start))[0]


TARGET_PATTERNS = [
    # $1,000 / $1.5k / US$300
    (re.compile(rf"(?:US)?\$\s?({NUM})\s*([kK])?(?![\d])"), 4),
    # 1000 美元 / 1000美金 / 1000 dollars / 1000 USD / 1000刀
    (re.compile(rf"({NUM}|{CN_NUM})\s*([kK千万])?\s*(?:美元|美金|刀|块钱|块|元|dollars?|bucks|usd)", re.I), 4),
    # 达到1000 / 涨到 1000 / 目标价 1000 / reach 1000 / hit 1000 / to 1000
    (re.compile(rf"(?:达到|涨到|涨至|升到|升至|突破|冲到|冲上|站上|站稳|收在|收于|守住|脉冲到|到达|到|上|看|目标价|目标|价格)\s*({NUM}|{CN_NUM})\s*([kK千万])?"), 3),
    (re.compile(rf"(?:reach(?:es|ing)?|hit(?:s|ting)?|to|at|above|target(?: price)?(?: of)?|be worth|trade at|worth)\s+\$?\s*({NUM})\s*([kK])?(?![\d])", re.I), 3),
    # 1000一股 / 1000 每股 / 1000 per share
    (re.compile(rf"({NUM}|{CN_NUM})\s*([kK千万])?\s*(?:一股|每股|/股|per share|a share)", re.I), 4),
]
MULTIPLE_PATTERNS = [
    (re.compile(r"翻倍|翻一倍|double[sd]?|doubling", re.I), lambda m: Decimal(2)),
    (re.compile(r"翻(\d+|[一二两三四五六七八九十]+)番"), lambda m: Decimal(2) ** int(parse_number(m.group(1)))),
    (re.compile(r"(?:涨|升)?\s*(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*倍(?!数)"), lambda m: parse_number(m.group(1))),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[xX](?![A-Za-z])"), lambda m: parse_number(m.group(1))),
    (re.compile(r"ten[- ]?bagger|十倍股", re.I), lambda m: Decimal(10)),
    (re.compile(r"(?:涨|上涨|rise|gain|up)\s*(\d+(?:\.\d+)?)\s*%", re.I), lambda m: 1 + parse_number(m.group(1)) / 100),
]


# reaching the price at any moment ("spike to", "touch") versus being there on the date
TOUCH_WORDS = re.compile(r"冲到|冲上|冲高|冲至|冲击|触及|触碰|摸到|摸高|碰到|脉冲|一度|曾经|盘中|"
                         r"\b(?:touch(?:es|ed|ing)?|spike[sd]?|spiking|pop(?:s|ped)? to|tag(?:s|ged)?|hit(?:s|ting)?|intraday|at some point|at any point)\b", re.I)
END_WORDS = re.compile(r"站稳|守住|收在|收于|稳定在|维持在|年底|年末|\b(?:close[sd]? (?:above|at)|stay(?:s)? above|hold(?:s)? above|end (?:the year )?above)\b", re.I)
WITHIN_WORDS = re.compile(r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)?\s*(?:年|个月|月)\s*(?:内|以内|之内)|\b(?:within|before|in the next)\b", re.I)


def find_condition(text: str) -> Optional[_Found]:
    """touch / end, with the words it came from; None when the sentence does not say."""
    m = END_WORDS.search(text)
    if m:
        return _Found("end", m.group(0), m.start())
    m = TOUCH_WORDS.search(text)
    if m:
        return _Found("touch", m.group(0), m.start())
    m = WITHIN_WORDS.search(text)
    if m:
        return _Found("touch", m.group(0).strip(), m.start(), extra={"inferred": True})
    return None


def find_target(text: str) -> Optional[_Found]:
    clean = _strip_years(text)
    best: Optional[_Found] = None
    for pattern, priority in TARGET_PATTERNS:
        for m in pattern.finditer(clean):
            value = parse_number(m.group(1), m.group(2) if m.lastindex and m.lastindex >= 2 else "")
            if value is None or value <= 0:
                continue
            found = _Found(value, text[m.start(): m.end()].strip(), m.start(), priority)
            if best is None or (found.priority, -found.start) > (best.priority, -best.start):
                best = found
    if best is not None:
        return best
    for pattern, fn in MULTIPLE_PATTERNS:
        m = pattern.search(clean)
        if m:
            factor = fn(m)
            if factor is not None and factor > 0:
                return _Found(None, m.group(0), m.start(), extra={"multiple": factor})
    return None


def _year_end(year: int, qualifier: Optional[str]) -> date:
    if qualifier in ("上半年", "年中"):
        return date(year, 6, 30)
    if qualifier in ("初", "年初"):
        return date(year, 3, 31)
    return date(year, 12, 31)


def find_horizon(text: str, today: date) -> Optional[_Found]:
    lowered = text.lower()
    # durations: 5年后 / 五年内 / in 5 years / within 18 months / 一年半
    dur = re.search(rf"({NUM}|{CN_NUM})\s*(?:个)?\s*(年|月)\s*(半)?\s*(?:后|内|以后|之后|之内|以内)?", text)
    m_en = re.search(r"(?:in|within|over|after|next)\s+(\d+(?:\.\d+)?|" + "|".join(EN_NUMBERS) + r")\s+(years?|yrs?|months?)", lowered)
    if m_en:
        raw = m_en.group(1)
        n = Decimal(str(EN_NUMBERS[raw])) if raw in EN_NUMBERS else Decimal(raw)
        years = n / 12 if m_en.group(2).startswith("month") else n
        return _Found(years, text[m_en.start(): m_en.end()], m_en.start(), extra={"kind": "duration"})
    if dur and not YEAR_RE.match(dur.group(0).strip()):
        n = parse_number(dur.group(1))
        if n is not None and n > 0 and not (dur.group(2) == "年" and n >= 1900):
            years = n / 12 if dur.group(2) == "月" else n
            if dur.group(3):
                years += Decimal("0.5")
            return _Found(years, dur.group(0).strip(), dur.start(), extra={"kind": "duration"})
    if "半年" in text:
        i = text.index("半年")
        return _Found(Decimal("0.5"), "半年", i, extra={"kind": "duration"})
    # calendar year: 2030年 / by 2030 / 2027年底
    m = YEAR_RE.search(text)
    if m:
        year = int(m.group(1))
        end = _year_end(year, m.group(2))
        return _Found(None, m.group(0).strip(), m.start(), extra={"kind": "date", "date": end})
    for words, offset in ((("明年", "next year"), 1), (("后年",), 2), (("今年底", "今年", "年底", "this year", "year end", "year-end", "end of the year"), 0)):
        for w in words:
            idx = lowered.find(w)
            if idx >= 0:
                return _Found(None, text[idx: idx + len(w)], idx, extra={"kind": "date", "date": date(today.year + offset, 12, 31)})
    return None


def extract_claim(text: str, *, today: date, is_known_ticker: Optional[Callable[[str], bool]] = None) -> ClaimExtraction:
    """Extract candidate claim fields; missing values stay None and are listed in notes."""
    out = ClaimExtraction(text=text or "")
    if not text or not text.strip():
        out.notes_en.append("empty claim text")
        out.notes_zh.append("观点原文为空")
        return out
    t = find_ticker(text, is_known_ticker)
    if t:
        out.ticker, out.ticker_text = t.value, t.text
    else:
        out.notes_en.append("no ticker or known company name found; enter the ticker")
        out.notes_zh.append("没有识别到股票代码或常见公司名，请手动填写代码")
    tgt = find_target(text)
    if tgt and tgt.value is not None:
        out.target_price, out.target_text = tgt.value, tgt.text
    elif tgt:
        out.target_multiple, out.target_text = tgt.extra["multiple"], tgt.text
    else:
        out.notes_en.append("no target price found; enter it")
        out.notes_zh.append("没有识别到目标价，请手动填写")
    hz = find_horizon(text, today)
    if hz:
        out.horizon_text = hz.text
        if hz.extra.get("kind") == "date":
            end: date = hz.extra["date"]
            out.target_date = end
            years = Decimal((end - today).days) / Decimal("365.25")
            if years <= 0:
                out.notes_en.append(f"the date in the claim ({end}) is already past; TickerCase checks claims about the future")
                out.notes_zh.append(f"观点中的时间（{end}）已经过去；TickerCase 用来核验关于未来的观点")
            else:
                out.horizon_years = years.quantize(Decimal("0.01"))
        else:
            out.horizon_years = Decimal(hz.value).quantize(Decimal("0.01"))
    else:
        out.notes_en.append("no time frame found; enter the horizon in years")
        out.notes_zh.append("没有识别到时间范围，请手动填写年数")
    cond = find_condition(text)
    if cond is not None:
        out.condition, out.condition_text = cond.value, cond.text
        if cond.extra.get("inferred"):
            out.notes_en.append(f"“{cond.text}” is read as reaching the target at any time before the date; switch to “on the date” if you meant that")
            out.notes_zh.append(f"「{cond.text}」理解为期间任意时点达到目标价；如果指的是到期时站在上面，请切换")
    if re.search(r"港元|港币|hk\$|hkd", text, re.I):
        out.currency = "HKD"
    elif re.search(r"美元|美金|\$|usd|dollar", text, re.I):
        out.currency = "USD"
    return out
