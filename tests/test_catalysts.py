"""Catalyst register (v0.12): sources, the two Claude calls (faked), the checks, reactions, confirmation and the page parts."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tickercase.catalysts import CATALYSTS_SCHEMA, check, expected_day, merge, request_params, source_table, upcoming
from tickercase.models import Catalyst, CatalystEvent, CatalystProposal, CatalystSource, NewsItem
from tickercase.providers.filing_text import html_text, key_sentences
from tickercase.providers.news import company_query_name, google_query, parse_google_rss, parse_yahoo_news
from tickercase.service import CaseService
from tickercase.storage import CaseStore, CatalystStore
from tickercase.ui import cockpit as ck
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft

MODEL = "claude-opus-5-5"


def _sources(user_text: str) -> list[dict]:
    return [json.loads(line) for line in user_text.splitlines() if line.startswith('{"id"')]


def _find(sources: list[dict], needle: str) -> dict:
    return next(s for s in sources if needle in s["text"])


class FakeClaude:
    """Answers the two calls: themes first, then catalysts built from the source ids it finds in the request."""

    def __init__(self, build=None):
        self.calls: list[dict] = []
        self._build = build or self.default_build
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    @staticmethod
    def default_build(sources: list[dict]) -> dict:
        slip = _find(sources, "delays Orbiter X first delivery")
        filing = _find(sources, "first half of 2027")
        unveil = _find(sources, "unveils Orbiter X")
        return {"catalysts": [{
            "updates": "", "name_en": "Orbiter X first delivery", "name_zh": "Orbiter X 首次交付", "kind": "product", "status": "delayed",
            "expected": "2027-H1", "direction": "up", "summary_en": "First deliveries of Orbiter X are now expected in the first half of 2027.",
            "summary_zh": "Orbiter X 首次交付现预计在 2027 年上半年。", "source_ids": [filing["id"], slip["id"]], "search_terms": ["Orbiter X"],
            "events": [
                {"date": unveil["date"], "change_en": "Orbiter X announced with first deliveries planned for 2026.",
                 "change_zh": "公布 Orbiter X，计划 2026 年首次交付。", "source_ids": [unveil["id"]]},
                {"date": slip["date"], "change_en": "First delivery moved to the first half of 2027.", "change_zh": "首次交付推迟到 2027 年上半年。",
                 "source_ids": [slip["id"], filing["id"]]}]}]}

    def _create(self, **params):
        self.calls.append(params)
        user = params["messages"][0]["content"]
        if "themes" in params["output_config"]["format"]["schema"]["properties"]:
            body = {"themes": [{"name": "Orbiter X", "search_terms": ["Orbiter X"]}]}
        else:
            body = self._build(_sources(user))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))], stop_reason="end_turn", model=MODEL,
                               usage=SimpleNamespace(input_tokens=4000, output_tokens=900))


def _svc(tmp_path):
    return CaseService(make_settings(tmp_path), store=CaseStore(tmp_path / "cases"), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)


def _run(svc, **kw):
    draft = ps_draft(peer_tickers="SYNA, SYNB", **kw)
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


# ---------------------------------------------------------------- sources and parsing


def test_news_parsing_and_queries():
    rss = ('<?xml version="1.0"?><rss><channel><item><title>Rocket Lab delays Neutron - SpaceNews</title><link>https://x/1</link>'
           '<pubDate>Wed, 14 Oct 2026 21:00:00 GMT</pubDate><source url="https://spacenews.com">SpaceNews</source></item></channel></rss>')
    items = parse_google_rss(rss, "u", "q")
    assert items[0].title == "Rocket Lab delays Neutron" and items[0].publisher == "SpaceNews" and items[0].published.tzinfo is not None
    y = parse_yahoo_news({"news": [{"title": "T", "publisher": "P", "link": "L", "providerPublishTime": 1792000000}, {"bad": 1}]}, "u")
    assert len(y) == 1 and y[0].origin == "yahoo"
    assert company_query_name("Rocket Lab USA, Inc.", "RKLB") == "Rocket Lab" and company_query_name(None, "RKLB") == "RKLB"
    assert google_query("Rocket Lab", "Neutron", days=730) == '"Rocket Lab" Neutron when:730d'


def test_press_release_sentences_skip_the_boilerplate():
    text = html_text("<p>Exhibit 99.1</p><p>Acme Updates Widget Timing.</p><p>LONG BEACH, Oct. 14, 2026 – Acme Corp today said the first Widget "
                     "launch is now expected to occur in 2027.</p><p>About Acme Acme builds widgets that are expected to change everything in 2030.</p>"
                     "<p>Forward-Looking Statements We expect things in 2031.</p>")
    sents = key_sentences(text, "Acme")
    assert any("2027" in x for x in sents) and not any("2030" in x or "2031" in x for x in sents)


def test_source_table_numbers_filings_then_unique_headlines():
    from tickercase.models import FilingRecord, DataMode

    rec = FilingRecord(provider_id="sec", ticker="T", cik="1", accession_number="0000000001-26-000001", form_type="8-K", is_amendment=False,
                       filing_date=date(2026, 10, 14), items="7.01,8.01,9.01", filing_index_url="u", source_response_url="u",
                       retrieved_at=FIXED_NOW, data_mode=DataMode.SYNTHETIC)
    n = [NewsItem(published=datetime(2026, 10, 15, 1, tzinfo=timezone.utc), title="Same story", url="a", origin="google_news"),
         NewsItem(published=datetime(2026, 10, 14, 20, tzinfo=timezone.utc), title="Same  story!", url="b", origin="yahoo")]
    src = source_table([(rec, ["One.", "Two."], "doc")], n)
    assert [s.id for s in src] == ["F01", "F02", "N001"]
    assert src[0].publisher == "SEC 8-K (7.01, 8.01)" and src[2].day == date(2026, 10, 14)  # 01:00 UTC is the evening before in New York


def test_expected_day_takes_the_middle_of_the_span():
    assert expected_day("2027") == date(2027, 7, 1) and expected_day("2027-H2") == date(2027, 10, 1)
    assert expected_day("2027-Q1") == date(2027, 2, 15) and expected_day("2027-03") == date(2027, 3, 15)
    assert expected_day("2027-03-02") == date(2027, 3, 2) and expected_day("soon") is None and expected_day(None) is None


def test_check_flags_unknown_ids_wrong_dates_and_numbers():
    s1 = CatalystSource(id="N001", kind="news", day=date(2026, 10, 14), text="Rocket Lab delays Neutron to 2027", publisher="X")
    by_id = {"N001": s1}
    good = Catalyst(id="P01", name_en="Neutron", name_zh="中子", expected="2027", summary_en="First launch now in 2027.", source_ids=["N001"],
                    events=[CatalystEvent(day=date(2026, 10, 14), change_en="Moved to 2027.", change_zh="推迟到 2027 年。", source_ids=["N001"])])
    assert check(good, by_id) == []
    bad = good.model_copy(update={"expected": "2028", "summary_en": "Costs rose 45%.", "source_ids": ["N001", "N999"],
                                  "events": [good.events[0].model_copy(update={"day": date(2026, 9, 1)})]})
    problems = " | ".join(check(bad, by_id))
    assert "N999" in problems and "2028" in problems and "45%" in problems and "2026-09-01" in problems


def test_request_params_use_structured_output_and_fallbacks():
    params = request_params(model=MODEL, effort="medium", system="s", user="u", schema=CATALYSTS_SCHEMA)
    assert params["output_config"]["format"]["type"] == "json_schema" and params["output_config"]["effort"] == "medium"
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]


# ---------------------------------------------------------------- the pipeline


def test_run_reads_headlines_and_the_earnings_date(tmp_path):
    r = _run(_svc(tmp_path))
    assert r.data_steps["news"] == "ok" and r.data_steps["earnings_date"] == "ok"
    assert r.earnings.day == date(2026, 11, 5) and not r.earnings.estimate
    assert r.news.items and all(n.publisher.endswith("(fictional)") for n in r.news.items)
    assert r.catalysts is None  # nothing confirmed yet


def test_propose_check_confirm_and_reuse(tmp_path):
    svc = _svc(tmp_path)
    r = _run(svc)
    fake = FakeClaude()
    r = svc.propose_catalysts(r, mode="synthetic", client=fake)
    prop = r.catalyst_proposal
    assert prop.status == "ok", prop.error
    assert len(fake.calls) == 2 and prop.themes == ["Orbiter X"]
    assert {s.kind for s in prop.sources} == {"filing", "news"}
    c = prop.catalysts[0]
    assert c.problems == [], c.problems
    assert c.expected_day == date(2027, 4, 1)
    slip = next(e for e in c.events if e.day == date(2026, 8, 5))
    assert slip.reaction is not None and slip.reaction.basis == "market+peers" and slip.reaction.days == 2
    assert prop.usage["input_tokens"] == 8000
    # confirm: into the register next to the cases
    r = svc.confirm_catalysts(r, ["P01"])
    reg = CatalystStore(tmp_path / "catalysts").get("SYNT")
    assert reg is not None and [x.id for x in reg.catalysts] == ["C01"] and r.catalysts.catalysts[0].confirmed_at == FIXED_NOW
    assert r.catalyst_proposal.decided_at == FIXED_NOW and r.catalyst_proposal.accepted == ["P01"]
    # a later run reads the register, and the scenario sees the dated catalyst and the earnings date
    r2 = _run(svc)
    assert r2.catalysts.catalysts[0].name_en == "Orbiter X first delivery"
    assert [x.id for x in upcoming(r2.catalysts, today=FIXED_TODAY, until=date(2031, 1, 1))] == ["C01"]
    rows = ck.upcoming_rows(r2, False)
    assert len(rows) == 2 and "Earnings" in rows[0][0] and rows[1][1]["day"] == date(2027, 4, 1)
    # the narrative sees the confirmed catalyst
    from tickercase.narrative import build_facts

    labels = [f.label_en for f in build_facts(r2)]
    assert "catalyst C01: Orbiter X first delivery" in labels and "next earnings date" in labels


def test_rejected_names_are_remembered_and_updates_merge(tmp_path):
    svc = _svc(tmp_path)
    r = svc.propose_catalysts(_run(svc), mode="synthetic", client=FakeClaude())
    r = svc.confirm_catalysts(r, [])
    reg = CatalystStore(tmp_path / "catalysts").get("SYNT")
    assert reg.catalysts == [] and reg.rejected == ["Orbiter X first delivery"]
    # accept it on a second proposal, then merge an update into it
    reg.rejected = []
    CatalystStore(tmp_path / "catalysts").save(reg)
    r = svc.confirm_catalysts(svc.propose_catalysts(r, mode="synthetic", client=FakeClaude()), ["P01"])
    new_event = CatalystEvent(day=date(2026, 9, 30), change_en="Qualification testing finished.", change_zh="完成鉴定测试。")
    upd = Catalyst(id="P01", name_en="Orbiter X first delivery", name_zh="Orbiter X 首次交付", status="in_progress", updates="C01", events=[new_event])
    proposal = CatalystProposal(status="ok", model=MODEL, created_at=FIXED_NOW, catalysts=[upd])
    merged = merge(r.catalysts, proposal, ["P01"], ticker="SYNT", company="Synthetic Example", now=FIXED_NOW + timedelta(days=1))
    c01 = merged.catalysts[0]
    assert len(merged.catalysts) == 1 and c01.status == "in_progress" and c01.events[-1].day == date(2026, 9, 30) and len(c01.events) == 3
    assert c01.expected == "2027-H1"  # an update without a timing keeps the confirmed one


def test_refusal_and_missing_key_leave_the_case_usable(tmp_path):
    svc = _svc(tmp_path)
    r = _run(svc)

    class Refusing(FakeClaude):
        def _create(self, **params):
            return SimpleNamespace(content=[], stop_reason="refusal", model=MODEL, usage=SimpleNamespace(input_tokens=10, output_tokens=0))

    r = svc.propose_catalysts(r, mode="synthetic", client=Refusing())
    assert r.catalyst_proposal.status == "refused" and r.oracle is not None
    html = ck.catalyst_card(Catalyst(id="C01", name_en="X", name_zh="X", summary_en="s"), False)
    assert "no timing in the sources" in html


def test_cards_render_without_chinese_in_english(tmp_path):
    svc = _svc(tmp_path)
    r = svc.propose_catalysts(_run(svc), mode="synthetic", client=FakeClaude())
    en = ck.catalyst_card(r.catalyst_proposal.catalysts[0], False, proposed=True) + ck.headlines_html(r.news, False)
    assert "checks pass" in en and "reaction" in en and "Example Wire (fictional)" in en
    import re

    visible = re.sub(r"<[^>]+>", " ", en)
    assert not [ch for ch in visible if "一" <= ch <= "鿿"]
    zh = ck.catalyst_card(r.catalyst_proposal.catalysts[0], True, proposed=True)
    assert "首次交付" in zh and "核对通过" in zh


def test_theme_headlines_come_first_and_keep_their_early_history():
    old = [NewsItem(published=datetime(2025, 11, 1, 12, tzinfo=timezone.utc) + timedelta(days=i), title=f"Neutron story {i}", url="u", origin="google_news")
           for i in range(100)]
    broad = [NewsItem(published=datetime(2026, 10, 1, 12, tzinfo=timezone.utc) + timedelta(hours=i), title=f"Other {i}", url="u", origin="google_news")
             for i in range(300)]
    src = source_table([], broad, themes=[old], max_news=60, per_theme=20)
    themed = [s for s in src if s.text.startswith("Neutron")]
    assert len(src) == 60 and len(themed) == 20
    assert min(s.day for s in themed) == date(2025, 11, 1) and max(s.day for s in themed) == date(2026, 2, 8)  # first and last kept
