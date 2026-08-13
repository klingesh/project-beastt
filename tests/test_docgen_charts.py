"""Charts must be sourced or labelled -- never presented as fact without being one.

`attach_real_data`'s docstring states the rule the whole data subsystem exists to
enforce:

    Either the numbers are sourced or they are labelled -- never presented as
    fact without being one.

`_chart_caption` is the other half: a real source is printed, and when there is
nothing to cite the admission IS printed, "because an unlabelled invented chart is
the thing being fixed."

These tests cover the validation that keeps a broken chart out of the renderer,
the caption honesty rule, and one genuine regression: the citation does not
survive a revision, so a chart that was sourced when the deck was built renders
with no caption at all after the user asks for an edit.
"""

from __future__ import annotations

import pytest

from beastt import docgen, layouts


@pytest.fixture
def sourced_chart():
    """A chart as it looks after `attach_real_data` has found a real series."""
    return {
        "type": "line",
        "categories": ["2023-01-01", "2024-01-01", "2025-01-01"],
        "series": [{"name": "CPI", "values": [3.1, 2.9, 2.4]}],
        "source": "Source: FRED: CPIAUCSL (retrieved 2026-08-13)",
        "units": "Percent change from year ago",
        "illustrative": False,
    }


# --- the caption, which is the honesty rule --------------------------------
class TestChartCaption:
    def test_a_real_source_is_printed(self, sourced_chart):
        caption = layouts._chart_caption(sourced_chart)
        assert "FRED: CPIAUCSL" in caption
        assert "retrieved 2026-08-13" in caption

    def test_the_units_accompany_the_source(self, sourced_chart):
        assert "Percent change from year ago" in layouts._chart_caption(sourced_chart)

    def test_a_source_wins_over_a_stray_illustrative_flag(self, sourced_chart):
        """Ordering matters: a real source is stated even if something also set
        the flag."""
        sourced_chart["illustrative"] = True
        assert "FRED" in layouts._chart_caption(sourced_chart)

    def test_invented_figures_are_admitted(self):
        assert layouts._chart_caption({"illustrative": True}) == (
            "Illustrative figures — not from a cited source.")

    def test_no_source_and_no_flag_gives_no_caption(self):
        """The dangerous state: a chart with numbers and no provenance draws no
        caption at all. Nothing in the pipeline should produce it."""
        assert layouts._chart_caption({}) == ""
        assert layouts._chart_caption(None) == ""

    def test_a_blank_source_falls_back_to_the_admission(self):
        assert layouts._chart_caption({"source": "   ", "illustrative": True}) == (
            "Illustrative figures — not from a cited source.")


class TestAttachRealData:
    def test_a_chart_with_no_query_is_marked_illustrative(self, config):
        spec = {"slides": [{"chart": {"type": "bar", "categories": ["a", "b"],
                                      "series": [{"name": "s", "values": [1, 2]}]}}]}

        docgen.attach_real_data(spec, config)

        assert spec["slides"][0]["chart"]["illustrative"] is True

    def test_data_disabled_marks_everything_illustrative(self, config):
        """Rather than leaving the numbers looking authoritative."""
        spec = {"slides": [{"chart": {"data_query": "us inflation",
                                      "type": "line"}}]}

        docgen.attach_real_data(spec, config)

        assert spec["slides"][0]["chart"]["illustrative"] is True

    def test_a_failed_lookup_is_non_fatal_and_labelled(self, config,
                                                       monkeypatch):
        """"A deck that renders with an honest 'illustrative figures' note beats
        no deck at all.\""""
        from dataclasses import replace

        import beastt.data as data

        monkeypatch.setattr(data, "lookup",
                            lambda *_a, **_k: (_ for _ in ()).throw(
                                RuntimeError("source down")))

        spec = {"slides": [{"chart": {"data_query": "us inflation",
                                      "type": "line"}}]}
        docgen.attach_real_data(spec, replace(config, data_enabled=True))

        assert spec["slides"][0]["chart"]["illustrative"] is True

    def test_a_deck_with_no_charts_is_untouched(self, config):
        spec = {"slides": [{"title": "No chart here", "bullets": ["a"]}]}
        assert docgen.attach_real_data(spec, config) == spec

    def test_the_query_is_consumed(self, config):
        """`data_query` is popped, not read -- it is an instruction to the
        pipeline, not content for the renderer."""
        spec = {"slides": [{"chart": {"data_query": "us inflation",
                                      "type": "line"}}]}

        docgen.attach_real_data(spec, config)

        assert "data_query" not in spec["slides"][0]["chart"]


# --- validation ------------------------------------------------------------
class TestNormaliseChart:
    """"Anything that can't be made consistent is rejected here so the renderer
    never has to cope.\""""

    def test_a_well_formed_chart_survives(self):
        out = docgen._normalise_chart({
            "type": "line", "categories": ["2023", "2024"],
            "series": [{"name": "CPI", "values": [3.1, 2.9]}]})

        assert out["type"] == "line"
        assert out["categories"] == ["2023", "2024"]
        assert out["series"] == [{"name": "CPI", "values": [3.1, 2.9]}]

    @pytest.mark.parametrize("raw", [
        "not a dict",
        None,
        {},
        {"categories": ["only one"], "series": [{"name": "s", "values": [1, 2]}]},
        {"categories": ["a", "b"], "series": []},
        {"categories": ["a", "b"], "series": [{"name": "s", "values": "nope"}]},
        {"categories": ["a", "b"], "series": [{"name": "s", "values": ["x", "y"]}]},
    ])
    def test_unusable_charts_are_rejected(self, raw):
        assert docgen._normalise_chart(raw) is None

    def test_an_unknown_type_falls_back_to_bar(self):
        out = docgen._normalise_chart({
            "type": "radar", "categories": ["a", "b"],
            "series": [{"name": "s", "values": [1, 2]}]})
        assert out["type"] == "bar"

    def test_series_are_trimmed_to_match_the_categories(self):
        """python-pptx requires the lengths to match exactly."""
        out = docgen._normalise_chart({
            "categories": ["a", "b", "c"],
            "series": [{"name": "s", "values": [1, 2]}]})

        assert len(out["categories"]) == len(out["series"][0]["values"]) == 2

    def test_at_most_four_series(self):
        out = docgen._normalise_chart({
            "categories": ["a", "b"],
            "series": [{"name": f"s{n}", "values": [1, 2]} for n in range(9)]})
        assert len(out["series"]) == 4

    def test_a_data_query_is_carried_through(self):
        out = docgen._normalise_chart({
            "categories": ["a", "b"], "data_query": "us inflation",
            "series": [{"name": "s", "values": [1, 2]}]})
        assert out["data_query"] == "us inflation"


class TestNumberCoercion:
    """Models return numbers with units attached, thousands separators, and
    currency symbols."""

    @pytest.mark.parametrize("value,expected", [
        ("1,200", 1200.0), ("45%", 45.0), ("$3.2", 3.2), ("-7.5", -7.5),
        (5, 5.0), (2.5, 2.5), ("  8  ", 8.0),
    ])
    def test_values_are_coerced(self, value, expected):
        assert docgen._number(value) == expected

    @pytest.mark.parametrize("value", ["", "-", ".", "abc", None, True, False])
    def test_junk_is_rejected_rather_than_guessed(self, value):
        assert docgen._number(value) is None

    def test_a_boolean_is_not_a_number(self):
        """`True` would otherwise coerce to 1.0 and be plotted."""
        assert docgen._number(True) is None


class TestThinness:
    """A reply with almost no content is rejected so the previous version can be
    kept instead."""

    @pytest.mark.parametrize("kind,spec,thin", [
        ("presentation", {"slides": []}, True),
        ("presentation", {"slides": [{"title": "one"}]}, True),
        ("presentation", {"slides": [{"title": "a"}, {"title": "b"}]}, False),
        ("document", {"sections": [{"heading": "one"}]}, True),
        ("document", {"sections": [{"heading": "a"}, {"heading": "b"}]}, False),
        ("spreadsheet", {"sheets": []}, True),
        ("spreadsheet", {"sheets": [{"rows": []}]}, True),
        ("spreadsheet", {"sheets": [{"rows": [[1, 2]]}]}, False),
    ])
    def test_thinness(self, kind, spec, thin):
        assert docgen._is_thin(kind, spec) is thin


# --- the regression -------------------------------------------------------
class TestCitationSurvivesRevision:
    """A chart that was sourced when the deck was built must still say so after
    the user asks for an edit.

    `Workshop.revise` sends the current spec to the model, and the reply goes
    through `_normalise` -> `_normalise_chart`, which rebuilds the chart dict from
    scratch with only `type`, `categories`, `series` and `data_query`. `source`,
    `units` and `illustrative` are all dropped. `_chart_caption` then returns "",
    so the chart renders with no provenance line of any kind -- which is exactly
    the "unlabelled invented chart" the subsystem was built to prevent.

    It cannot be recovered afterwards either: `attach_real_data` *pops*
    `data_query`, and `Workshop.revise` never calls it again.
    """

    def test_normalise_chart_drops_the_provenance(self, sourced_chart):
        """The mechanism, asserted plainly so the cause is unambiguous."""
        out = docgen._normalise_chart(sourced_chart)

        assert out is not None
        assert "source" not in out
        assert "units" not in out
        assert "illustrative" not in out

    def test_the_caption_is_lost_as_a_result(self, sourced_chart):
        """The consequence: provenance before, silence after."""
        assert layouts._chart_caption(sourced_chart) != ""
        assert layouts._chart_caption(docgen._normalise_chart(sourced_chart)) == ""

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "_normalise_chart rebuilds the chart dict and does not carry over "
        "`source`, `units` or `illustrative`, so a revised deck renders a "
        "previously-sourced chart with no caption at all. Fix: preserve those "
        "three keys in the dict `_normalise_chart` returns."))
    def test_a_sourced_chart_should_keep_its_citation(self, sourced_chart):
        out = docgen._normalise_chart(sourced_chart)

        assert out["source"] == sourced_chart["source"]
        assert out["units"] == sourced_chart["units"]
        assert layouts._chart_caption(out) == layouts._chart_caption(sourced_chart)

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "Same cause: the `illustrative` flag is dropped too, so a chart that "
        "correctly admitted its figures were invented stops admitting it after "
        "an edit -- the worse direction of the same bug."))
    def test_an_illustrative_chart_should_keep_admitting_it(self):
        chart = {"type": "bar", "categories": ["a", "b"],
                 "series": [{"name": "s", "values": [1, 2]}],
                 "illustrative": True}

        out = docgen._normalise_chart(chart)

        assert out["illustrative"] is True
        assert layouts._chart_caption(out) != ""


class TestLayoutSelection:
    """A chart that cannot be drawn must not leave an empty frame on the slide."""

    def test_a_chart_slide_with_unusable_data_becomes_bullets(self):
        spec = {"title": "Deck", "slides": [{
            "layout": "chart", "title": "Growth",
            "bullets": ["still worth saying"],
            "chart": {"categories": ["only one"], "series": []}}]}

        out = docgen._normalise("presentation", spec, "Growth")

        assert out["slides"][0]["layout"] == "bullets"
        assert "chart" not in out["slides"][0]

    def test_aliases_resolve_to_the_catalogue(self):
        for alias, expected in [("graph", "chart"), ("photo", "image"),
                                ("vs", "comparison"), ("kpi", "stat"),
                                ("roadmap", "timeline"), ("divider", "section")]:
            assert layouts.normalise(alias) == expected

    def test_an_unknown_layout_falls_back_to_bullets(self):
        assert layouts.normalise("interpretive-dance") == "bullets"

    def test_spacing_and_case_are_tolerated(self):
        assert layouts.normalise("Two Column") == "comparison"

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "'closing' is in layouts.CATALOGUE -- so it is offered to the model and "
        "listed by describe() -- but has no entry in RENDERERS, so a slide "
        "asking for it silently renders as bullets. Either add a renderer or "
        "remove it from the catalogue."))
    def test_every_advertised_layout_has_a_renderer(self):
        assert set(layouts.CATALOGUE) <= set(layouts.RENDERERS)
