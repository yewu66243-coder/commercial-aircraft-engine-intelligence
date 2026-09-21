from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_markup_and_runtime_wiring_exist():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend/scripts.js").read_text(encoding="utf-8")

    assert 'id="evaluationPanel"' in html
    assert 'id="evaluationEntityF1"' in html
    assert 'id="evaluationEntityRecall"' in html
    assert 'id="evaluationLinkAccessibility"' in html
    assert 'id="evaluationCategoryBody"' in html
    assert html.index('id="evaluationPanel"') < html.index('id="reportContainer"')
    assert "/site/evaluation_panel.js?v=evaluation-dashboard-20260921" in html
    assert "/site/styles.css?v=evaluation-dashboard-20260921" in html
    assert "/site/scripts.js?v=evaluation-dashboard-20260921" in html
    assert "window.EvaluationPanel?.reset()" in script
    assert "window.EvaluationPanel?.render(data.run_statistics?.evaluation_summary)" in script


def test_panel_styles_cover_cards_states_table_and_mobile_layout():
    css = (ROOT / "frontend/styles.css").read_text(encoding="utf-8")

    for selector in (
        ".evaluation-panel",
        ".evaluation-metrics",
        '.evaluation-card[data-state="pass"]',
        '.evaluation-card[data-state="fail"]',
        ".evaluation-table-wrap",
    ):
        assert selector in css
    assert "@media (max-width: 760px)" in css
