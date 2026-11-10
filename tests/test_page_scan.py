"""Every report page of several cases, rendered in Chinese and scanned for the kinds of slips reviewers kept finding one
place at a time: raw English keys, "-0.0%", "0%–0%", Python's None or nan. Data here is SYNTHETIC (DEMO)."""

import re

import pytest
from streamlit.testing.v1 import AppTest

from test_app import APP, REPORT_PAGES, app_env, click, confirm_and_run, read, run, synthetic  # noqa: F401  (app_env is a fixture)

CLAIMS = {
    "five-year P/S example": None,  # the built-in example
    "one-month touch": "SYNT 一个月内触及 60",
    "half a year, close above": "SYNT 半年后收在 55 以上",
    "P/E in the sentence": "SYNT 2 年内涨到 100，按 40 倍市盈率",
    "target below the price": "SYNT 一年后收在 40 以上",
}
SEEN = {"fund": "公式", "data": "案例编号", "methods": "M4"}  # the scan reads tables and folds too, not only the page's top

URL = re.compile(r"https?://\S+")
TAG = re.compile(r"<[^>]+>")
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")  # latest_reported_annual_revenue, market_log, claim_inputs
SLIPS = [
    (re.compile(r"-0\.0+%"), "minus zero"),
    (re.compile(r"(?<![\d.])0(?:\.0)?%\s*[–-]\s*0(?:\.0)?%"), "a range of zeros"),
    (re.compile(r"\b(?:None|nan|NaN)\b"), "Python value"),
    (re.compile(r"\b(?:live|synthetic|unavailable|user input|rules-v1|FY ending|deterministic)\b"), "English mode or label"),
]


def page_text(at) -> str:
    parts = [str(e.value) for kind in ("markdown", "caption", "error", "warning", "info", "success") for e in getattr(at, kind)]
    parts += [str(e.proto.body) for e in at.get("html")]
    parts += [str(e.label) for e in at.expander] + [str(b.label) for b in at.button]
    for df in at.dataframe:
        frame = df.value
        parts += [str(c) for c in frame.columns] + [str(x) for x in frame.astype(str).to_numpy().ravel()]
    text = re.sub(r"<style.*?</style>|<script.*?</script>", " ", "\n".join(parts), flags=re.S)  # page CSS and scripts are not text
    return URL.sub(" ", TAG.sub(" ", text))


@pytest.mark.parametrize("name", list(CLAIMS))
def test_report_pages_in_chinese_have_none_of_the_known_slips(app_env, name, monkeypatch):  # noqa: F811
    # the synthetic data ends on 2026-09-30; a fixed evaluation day keeps the short deadlines in the future on any day
    monkeypatch.setenv("TICKERCASE_TODAY", "2026-10-01")
    at = run(AppTest.from_file(APP))
    if CLAIMS[name] is None:
        click(at, "btn_example_ps")
    else:
        synthetic(at)
        read(at, CLAIMS[name])
    confirm_and_run(at)
    assert at.session_state["result"] is not None and at.session_state["result"].oracle is not None
    found = []
    for page in REPORT_PAGES:
        click(at, f"nav_{page}")
        text = page_text(at)
        assert SEEN.get(page, "") in text, f"{page}: the scan did not see {SEEN[page]!r}"
        found += [f"{page}: snake_case '{m}'" for m in sorted(set(SNAKE.findall(text)))]
        for pattern, kind in SLIPS:
            found += [f"{page}: {kind} '{m.group(0)}' in …{text[max(0, m.start() - 40): m.end() + 20]}…" for m in pattern.finditer(text)]
    assert not found, "\n".join(found)
