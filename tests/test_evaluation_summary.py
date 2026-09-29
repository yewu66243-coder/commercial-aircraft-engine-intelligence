from __future__ import annotations

import math

from gpt_researcher.evaluation.evaluation_summary import (
    ENTITY_THRESHOLD,
    PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
    PUBLIC_LINK_SUPPORT_THRESHOLD,
    build_evaluation_summary,
)


def _strict_entity(f1: float | None = 0.9) -> dict:
    return {
        "mode": "strict",
        "status": "auto_evaluated",
        "ground_truth_path": "C:/private/truth.json",
        "metrics": {
            "overall": {
                "true_positive": 9,
                "false_positive": 1,
                "false_negative": 0,
                "precision": 0.9,
                "recall": 1.0,
                "f1": f1,
            },
            "categories": {"organization": {"f1": 1.0}},
            "matches": [{"entity": "A"}],
            "correct_entities": [{"entity": "A"}],
            "wrong_entities": [{"entity": "B"}],
            "missed_entities": [{"entity": "C"}],
        },
    }


def _url_check(rate: float | None = 0.98) -> dict:
    return {
        "total_urls": 100,
        "checked_urls": 100,
        "accessible_urls": 98,
        "failed_urls": 2,
        "accessibility_rate": rate,
        "results": [{"url": "https://example.test", "accessible": True}],
    }


def _source_eval(accuracy: float | None = 0.9) -> dict:
    return {
        "relationship_count": 10,
        "supported_count": 9,
        "partially_supported_count": 1,
        "unsupported_count": 0,
        "unchecked_count": 0,
        "support_accuracy": accuracy,
        "relationships": [{"claim": "A", "url": "https://example.test"}],
    }


def test_strict_summary_exposes_metrics_details_and_dual_link_evaluations():
    summary = build_evaluation_summary(
        _strict_entity(),
        _url_check(),
        _source_eval(),
        {"removed_count": 2, "removed_items": ["secret finding"]},
        {
            "markdown": "/outputs/evaluations/report.md",
            "word": "outputs%2Fevaluations%2Freport.docx",
            "pdf": "C:/private/report.pdf",
            "other": "/outputs/evaluations/ignored.txt",
        },
    )

    assert summary["status"] == "completed"
    assert summary["entity"]["ground_truth_path"] == "truth.json"
    assert summary["entity"]["overall"] == {
        "true_positive": 9,
        "false_positive": 1,
        "false_negative": 0,
        "precision": 0.9,
        "recall": 1.0,
        "f1": 0.9,
        "requirement_met": True,
    }
    assert summary["entity"]["categories"] == _strict_entity()["metrics"]["categories"]
    assert summary["entity"]["matched"] == [{"entity": "A"}]
    assert summary["entity"]["false_positives"] == [{"entity": "B"}]
    assert summary["entity"]["false_negatives"] == [{"entity": "C"}]
    assert summary["public_links"]["accessibility"]["requirement_met"] is True
    assert summary["public_links"]["claim_support"]["requirement_met"] is True
    assert summary["public_links"]["details"] == {
        "accessibility": _url_check()["results"],
        "claim_support": _source_eval()["relationships"],
    }
    assert summary["style_cleanup"] == {"removed_count": 2}
    assert "secret finding" not in repr(summary)
    assert summary["evaluation_report_paths"] == {
        "markdown": "/outputs/evaluations/report.md",
        "word": "/outputs/evaluations/report.docx",
        "pdf": "",
    }


def test_proxy_never_fabricates_strict_metrics_or_errors():
    summary = build_evaluation_summary(
        {
            "mode": "proxy",
            "auto_evidence_eval": {"auto_evidence_accuracy": 0.75},
            "matches": ["only a detail"],
        },
        {"total_urls": 0},
        {},
    )

    assert summary["status"] == "completed"
    assert summary["entity"]["overall"] is None
    assert summary["entity"]["proxy_evidence_support_rate"] == 0.75
    assert summary["entity"]["matched"] == ["only a detail"]
    assert summary["public_links"]["accessibility"]["status"] == "no_public_urls"
    assert summary["public_links"]["claim_support"]["status"] == "no_public_relationships"
    assert summary["errors"] == []


def test_invalid_ground_truth_and_entity_failure_are_safe_and_partial_when_links_work():
    invalid = build_evaluation_summary(
        {
            "mode": "invalid",
            "ground_truth_path": "C:/secret/private/broken.json",
            "ground_truth_error_code": "invalid_json",
            "ground_truth_message": "cannot parse token=supersecret",
        },
        _url_check(1.0),
        _source_eval(1.0),
    )
    failed = build_evaluation_summary({"mode": "strict", "metrics": None}, _url_check(), _source_eval())

    assert invalid["status"] == "partial"
    assert invalid["entity"]["status"] == "invalid_ground_truth"
    assert "C:/secret" not in repr(invalid)
    assert "supersecret" not in repr(invalid)
    assert failed["status"] == "partial"
    assert failed["entity"]["status"] == "evaluation_failed"


def test_accessibility_completed_empty_failed_and_threshold_boundary():
    at_boundary = build_evaluation_summary(_strict_entity(), _url_check(0.98), _source_eval())
    empty = build_evaluation_summary(_strict_entity(), {"total_urls": 0}, _source_eval())
    failed = build_evaluation_summary(_strict_entity(), {"evaluation_error": "leak this"}, _source_eval())

    assert at_boundary["public_links"]["accessibility"]["rate"] == 0.98
    assert at_boundary["public_links"]["accessibility"]["requirement_met"] is True
    assert empty["public_links"]["accessibility"]["status"] == "no_public_urls"
    assert empty["public_links"]["accessibility"]["rate"] is None
    assert failed["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert "leak this" not in repr(failed)


def test_claim_support_completed_empty_failed_and_threshold_boundary():
    at_boundary = build_evaluation_summary(_strict_entity(), _url_check(), _source_eval(0.9))
    empty = build_evaluation_summary(_strict_entity(), _url_check(), {})
    failed = build_evaluation_summary(_strict_entity(), _url_check(), {"evaluation_error": "leak this"})

    assert at_boundary["public_links"]["claim_support"]["accuracy"] == 0.9
    assert at_boundary["public_links"]["claim_support"]["requirement_met"] is True
    assert empty["public_links"]["claim_support"]["status"] == "no_public_relationships"
    assert empty["public_links"]["claim_support"]["accuracy"] is None
    assert failed["public_links"]["claim_support"]["status"] == "evaluation_failed"
    assert "leak this" not in repr(failed)


def test_legacy_two_argument_call_marks_claim_support_not_evaluated():
    summary = build_evaluation_summary(_strict_entity(), _url_check())

    claim_support = summary["public_links"]["claim_support"]
    assert summary["status"] == "completed"
    assert claim_support["status"] == "not_evaluated"
    assert claim_support["relationship_count"] == 0
    assert claim_support["accuracy"] is None


def test_all_three_failed_is_failed_and_one_success_is_partial():
    all_failed = build_evaluation_summary(
        {"mode": "strict", "metrics": None},
        {"evaluation_error": "x"},
        {"evaluation_error": "x"},
    )
    partial = build_evaluation_summary(
        {"mode": "strict", "metrics": None}, _url_check(), {"evaluation_error": "x"}
    )

    assert all_failed["status"] == "failed"
    assert partial["status"] == "partial"


def test_bad_types_and_non_finite_values_never_raise_or_leak_exception_details():
    summary = build_evaluation_summary(
        "entity boom / C:/secret",
        {"total_urls": True, "accessibility_rate": math.nan, "results": "not-list"},
        {"relationship_count": -1, "support_accuracy": math.inf, "relationships": "not-list"},
        {"removed_count": True, "removed_items": ["private"]},
        "C:/secret/paths",
    )

    assert summary["entity"]["status"] == "evaluation_failed"
    assert summary["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert summary["public_links"]["claim_support"]["status"] == "evaluation_failed"
    assert summary["style_cleanup"] == {"removed_count": 0}
    assert summary["evaluation_report_paths"] == {"markdown": "", "word": "", "pdf": ""}
    assert "C:/secret" not in repr(summary)


def test_published_threshold_constants_are_stable():
    assert ENTITY_THRESHOLD == 0.90
    assert PUBLIC_LINK_ACCESSIBILITY_THRESHOLD == 0.98
    assert PUBLIC_LINK_SUPPORT_THRESHOLD == 0.90


def test_entity_detail_aliases_and_explicit_evaluation_failure_are_stable():
    aliased = _strict_entity()
    aliased.update(
        {
            "matches": None,
            "false_positives": ["wrong"],
            "false_negatives": ["missed"],
        }
    )
    failed = build_evaluation_summary(
        {"mode": "proxy", "status": "evaluation_failed"}, _url_check(), _source_eval()
    )

    summary = build_evaluation_summary(aliased, _url_check(), _source_eval())
    assert summary["entity"]["false_positives"] == ["wrong"]
    assert summary["entity"]["false_negatives"] == ["missed"]
    assert failed["entity"]["status"] == "evaluation_failed"


def test_zero_denominator_link_evaluations_remain_not_applicable_with_bad_rates():
    summary = build_evaluation_summary(
        _strict_entity(),
        {"total_urls": 0, "accessibility_rate": math.nan, "results": "irrelevant"},
        {"relationship_count": 0, "support_accuracy": math.inf, "relationships": "irrelevant"},
    )

    assert summary["public_links"]["accessibility"]["status"] == "no_public_urls"
    assert summary["public_links"]["claim_support"]["status"] == "no_public_relationships"


def test_report_paths_reject_queries_fragments_and_parent_traversal():
    summary = build_evaluation_summary(
        _strict_entity(),
        _url_check(),
        _source_eval(),
        evaluation_report_paths={
            "markdown": "/outputs/evaluations/report.md?secret=1",
            "word": "/outputs/evaluations/report.docx#page=2",
            "pdf": "/outputs/evaluations/../private.pdf",
        },
    )

    assert summary["evaluation_report_paths"] == {"markdown": "", "word": "", "pdf": ""}


def test_unhashable_entity_mode_is_safely_normalized():
    summary = build_evaluation_summary({"mode": []}, {"total_urls": 0}, {})

    assert summary["entity"]["mode"] == "proxy"
    assert summary["status"] == "completed"


def test_overflowing_numeric_input_degrades_without_raising():
    entity = _strict_entity(10**10000)
    summary = build_evaluation_summary(entity, _url_check(), _source_eval())

    assert summary["entity"]["status"] == "evaluation_failed"
