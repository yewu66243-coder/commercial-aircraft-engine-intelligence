from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "frontend/scripts.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/styles.css").read_text(encoding="utf-8")


def tag_of(identifier: str) -> str:
    """返回包含该 id 的整段标签，便于断言同一元素上的属性组合。"""
    anchor = HTML.index(f'id="{identifier}"')
    start = HTML.rfind("<", 0, anchor)
    end = HTML.find(">", anchor)
    return HTML[start:end + 1]


def test_markup_and_runtime_wiring_exist():
    for identifier in (
        "evaluationPanel",
        "evaluationEntityPrecision",
        "evaluationEntityRecallF1",
        "evaluationLinkAccessibility",
        "evaluationClaimSupport",
        "evaluationEntityPrecisionStatus",
        "evaluationEntityRecallF1Status",
        "evaluationLinkAccessibilityStatus",
        "evaluationClaimSupportStatus",
        "evaluationCategoryBody",
    ):
        assert f'id="{identifier}"' in HTML, identifier

    assert HTML.index('id="evaluationPanel"') < HTML.index('id="reportContainer"')
    assert "/site/evaluation_panel.js?v=evaluation-dashboard-20260921" in HTML
    assert "/site/styles.css?v=evaluation-dashboard-20260921" in HTML
    assert "/site/scripts.js?v=evaluation-dashboard-20260921" in HTML
    assert "window.EvaluationPanel?.reset()" in SCRIPT
    assert "window.EvaluationPanel?.render(" in SCRIPT


def test_action_controls_start_disabled_and_expose_status():
    input_tag = tag_of("evaluationGroundTruthInput")
    assert 'type="file"' in input_tag
    assert "hidden" in input_tag
    assert 'accept=".json,.xlsx"' in input_tag

    assert "disabled" in tag_of("evaluationRerunButton")
    assert 'aria-disabled="true"' in tag_of("evaluationDownloadWord")
    assert 'aria-disabled="true"' in tag_of("evaluationDownloadPdf")

    status_tag = tag_of("evaluationActionStatus")
    assert 'role="status"' in status_tag
    assert 'aria-live="polite"' in status_tag


def test_scripts_bind_run_id_task_and_form_upload():
    assert "data.run_statistics" in SCRIPT
    assert "runStatistics.run_id" in SCRIPT
    assert "runStatistics.task" in SCRIPT
    assert "currentEvaluationRunId = " in SCRIPT
    assert "currentEvaluationTask = " in SCRIPT

    upload_index = SCRIPT.index("const uploadEvaluationGroundTruth")
    upload_block = SCRIPT[upload_index:upload_index + 900]
    assert "new FormData()" in upload_block
    assert "formData.append('task'" in upload_block
    assert "formData.append('file'" in upload_block
    assert "/api/evaluation-ground-truth" in upload_block
    assert "setEvaluationBusy(true)" in upload_block
    assert "evaluationRequestSeq" in upload_block


def test_concurrent_actions_are_serialised_and_stale_results_dropped():
    reset_index = SCRIPT.index("const resetEvaluationControls")
    reset_block = SCRIPT[reset_index:reset_index + 600]
    assert "evaluationRequestSeq += 1" in reset_block
    assert "evaluationBusy = false" in reset_block

    rerun_index = SCRIPT.index("const rerunEvaluation")
    rerun_block = SCRIPT[rerun_index:rerun_index + 1400]
    assert "if (evaluationBusy) return" in rerun_block
    assert "evaluationRequestSeq" in rerun_block
    assert "setEvaluationBusy(true)" in rerun_block


def test_new_task_and_history_load_clear_evaluation_state():
    start_index = SCRIPT.index("const startResearch")
    cleanup_index = SCRIPT.index("// 1. 清理上一轮的输出痕迹", start_index)
    start_head = SCRIPT[cleanup_index:cleanup_index + 200]
    assert "resetEvaluationControls();" in start_head
    assert "lastEvaluationSummary = null;" in start_head

    history_anchor = SCRIPT.index("// Clear current research/report areas")
    history_head = SCRIPT[history_anchor:history_anchor + 400]
    assert "resetEvaluationControls();" in history_head
    assert "lastEvaluationSummary = null;" in history_head


def test_action_failures_never_touch_the_current_report():
    start = SCRIPT.index("const uploadEvaluationGroundTruth")
    end = SCRIPT.index("const initEvaluationControls")
    block = SCRIPT[start:end]

    assert "startResearch" not in block
    assert "writeReport" not in block
    assert "document.getElementById('output')" not in block
    assert "lastEvaluationSummary = null" not in block


def test_four_cards_follow_the_confirmed_order():
    positions = [
        HTML.index('id="evaluationEntityPrecision"'),
        HTML.index('id="evaluationEntityRecallF1"'),
        HTML.index('id="evaluationLinkAccessibility"'),
        HTML.index('id="evaluationClaimSupport"'),
    ]

    assert positions == sorted(positions)
    assert "实体 F1 90% · 链接可访问率 98% · 结论支撑准确率 90%" in HTML


def test_ground_truth_upload_and_rerun_controls_exist():
    assert 'id="evaluationGroundTruthInput"' in HTML
    assert 'accept=".json,.xlsx"' in HTML
    assert 'id="evaluationUploadButton"' in HTML
    assert 'id="evaluationRerunButton"' in HTML
    assert "重新测评" in HTML
    assert 'id="evaluationDownloadWord"' in HTML
    assert 'id="evaluationDownloadPdf"' in HTML
    assert 'id="evaluationGroundTruthStatus"' in HTML
    assert 'id="evaluationActionStatus"' in HTML
    assert 'aria-live="polite"' in HTML


def test_scripts_call_evaluation_endpoints_without_rerunning_research():
    assert "/api/evaluation-ground-truth" in SCRIPT
    assert "/api/report-evaluation/" in SCRIPT
    assert "currentEvaluationRunId" in SCRIPT
    assert "currentEvaluationTask" in SCRIPT
    rerun_index = SCRIPT.index("/api/report-evaluation/")
    rerun_block = SCRIPT[rerun_index - 400 : rerun_index + 600]

    assert "startResearch" not in rerun_block


def test_panel_styles_cover_cards_states_table_actions_and_mobile_layout():
    css = CSS

    for selector in (
        ".evaluation-panel",
        ".evaluation-metrics",
        '.evaluation-card[data-state="pass"]',
        '.evaluation-card[data-state="fail"]',
        ".evaluation-table-wrap",
        ".evaluation-actions",
        ".evaluation-action-btn",
    ):
        assert selector in css
    assert "repeat(4, minmax(0, 1fr))" in css
    assert "@media (max-width: 760px)" in css
