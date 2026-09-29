from __future__ import annotations

import math

import pytest

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
            "categories": {
                "organization": {
                    "label": "机构",
                    "true_positive": 1,
                    "false_positive": 0,
                    "false_negative": 0,
                    "precision": 1.0,
                    "recall": 1.0,
                    "f1": 1.0,
                }
            },
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
        "results": [{"url": "https://example.test", "accessible": True} for _ in range(100)],
    }


def _source_eval(accuracy: float | None = 0.9) -> dict:
    return {
        "relationship_count": 10,
        "supported_count": 9,
        "partially_supported_count": 1,
        "unsupported_count": 0,
        "unchecked_count": 0,
        "support_accuracy": accuracy,
        "relationships": [{"claim": "A", "url": "https://example.test"} for _ in range(10)],
    }


def test_strict_summary_exposes_metrics_details_and_dual_link_evaluations():
    summary = build_evaluation_summary(
        _strict_entity(),
        _url_check(),
        _source_eval(),
        {"removed_count": 2, "removed_items": ["secret finding"]},
        {
            "markdown": "/outputs/evaluations/report.md",
            "word": "/outputs/evaluations/report.docx",
            "pdf": "/outputs/evaluations/report.pdf",
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
        "f1": 0.9474,
        "requirement_met": True,
    }
    assert summary["entity"]["categories"]["organization"]["label"] == "机构"
    assert summary["entity"]["categories"]["organization"]["requirement_met"] is True
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
        "pdf": "/outputs/evaluations/report.pdf",
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

    assert summary["entity"]["status"] == "completed"


def test_omitted_claim_support_does_not_make_two_failed_legacy_parts_partial():
    summary = build_evaluation_summary(
        {"mode": "strict", "metrics": None},
        {"evaluation_error": "private failure detail"},
    )

    assert summary["public_links"]["claim_support"]["status"] == "not_evaluated"
    assert summary["status"] == "failed"


def test_omitted_claim_support_preserves_partial_when_one_evaluated_part_succeeds():
    summary = build_evaluation_summary(
        {"mode": "strict", "metrics": None},
        _url_check(),
    )

    assert summary["public_links"]["claim_support"]["status"] == "not_evaluated"
    assert summary["status"] == "partial"


def test_all_not_applicable_or_not_evaluated_parts_are_completed_without_errors():
    summary = build_evaluation_summary({"mode": "proxy"}, {"total_urls": 0})

    assert summary["entity"]["status"] == "proxy"
    assert summary["public_links"]["accessibility"]["status"] == "no_public_urls"
    assert summary["public_links"]["claim_support"]["status"] == "not_evaluated"
    assert summary["status"] == "completed"
    assert summary["errors"] == []


@pytest.mark.parametrize(
    "bad_category",
    [
        {"true_positive": -1},
        {"f1": math.nan},
        {"precision": True},
        {"false_negative": "not-a-count"},
    ],
)
def test_invalid_strict_category_metrics_fail_entity_safely(bad_category):
    entity = _strict_entity()
    entity["metrics"]["categories"] = {"organization": bad_category}

    summary = build_evaluation_summary(entity, _url_check(), _source_eval())

    assert summary["entity"]["status"] == "evaluation_failed"
    assert summary["entity"]["overall"] is None
    assert summary["errors"][0] == {
        "scope": "entity",
        "code": "evaluation_failed",
        "message": "实体抽取测评未完成。",
    }


def test_valid_category_label_is_safely_normalized_without_losing_metrics():
    entity = _strict_entity()
    entity["metrics"]["categories"] = {
        "organization": {
            "label": 7, "true_positive": 1, "false_positive": 0, "false_negative": 0,
            "precision": 1.0, "recall": 1.0, "f1": 1.0,
        }
    }

    summary = build_evaluation_summary(entity, _url_check(), _source_eval())

    assert summary["entity"]["status"] == "completed"
    assert summary["entity"]["categories"]["organization"]["label"] == "7"
    assert summary["entity"]["categories"]["organization"]["true_positive"] == 1


def test_overflowing_category_label_is_redacted_without_raising():
    entity = _strict_entity()
    entity["metrics"]["categories"] = {
        "organization": {
            "label": 10**10000, "true_positive": 0, "false_positive": 0, "false_negative": 0,
            "precision": None, "recall": None, "f1": None,
        }
    }

    summary = build_evaluation_summary(entity, _url_check(), _source_eval())

    assert summary["entity"]["status"] == "completed"
    assert summary["entity"]["categories"]["organization"]["label"] == "organization"


def test_public_links_keep_deprecated_accessibility_aliases_for_existing_panel():
    summary = build_evaluation_summary(_strict_entity(), _url_check(), _source_eval())

    links = summary["public_links"]
    assert links["status"] == links["accessibility"]["status"]
    assert links["accessibility_rate"] == links["accessibility"]["rate"]
    assert links["requirement_met"] == links["accessibility"]["requirement_met"]


def test_completed_summary_whitelists_and_detaches_sensitive_details():
    entity = _strict_entity()
    entity["metrics"]["overall"]["debug"] = "private overall"
    entity["metrics"]["categories"]["organization"] = {
        "label": "机构",
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "path": "C:/secret/category.json",
    }
    entity["matches"] = [{"entity": "A", "debug": "private", "path": "C:/secret/a"}]
    urls = _url_check()
    urls["results"] = [{
        "url": "https://example.test", "checked_url": "https://example.test", "accessible": True,
        "raw_error": "secret", "local_path": "C:/secret/url", "warning": "none",
    }] * 100
    source = _source_eval()
    source["relationships"] = [{
        "relationship_id": "r1", "ref": "URL1", "url": "https://example.test", "claim": "claim",
        "status": "supported", "debug": "secret", "local_path": "C:/secret/relationship",
    }] * 10

    summary = build_evaluation_summary(entity, urls, source)
    entity["matches"][0]["entity"] = "mutated"
    urls["results"][0]["url"] = "https://mutated.test"
    source["relationships"][0]["claim"] = "mutated"

    assert summary["entity"]["overall"].keys() == {
        "true_positive", "false_positive", "false_negative", "precision", "recall", "f1", "requirement_met"
    }
    assert summary["entity"]["categories"]["organization"].keys() == {
        "label", "true_positive", "false_positive", "false_negative", "precision", "recall", "f1", "requirement_met"
    }
    assert summary["entity"]["matched"] == [{"entity": "A"}]
    assert summary["public_links"]["details"]["accessibility"][0] == {
        "url": "https://example.test", "checked_url": "https://example.test", "accessible": True
    }
    assert summary["public_links"]["details"]["claim_support"][0] == {
        "relationship_id": "r1", "ref": "URL1", "url": "https://example.test", "claim": "claim",
        "status": "supported", "reason": "来源充分支撑该断言。"
    }
    assert "secret" not in repr(summary)
    assert "mutated" not in repr(summary)


@pytest.mark.parametrize(
    "paths",
    [
        {"markdown": "/outputs/evaluations/%252e%252e%252Fsecret.md"},
        {"markdown": "/outputs/evaluations/report.md%0d%0aX-Injected: yes"},
        {"markdown": "/outputs/evaluations/report.md%00secret"},
        {"markdown": "/outputs/evaluations/report.pdf"},
    ],
)
def test_report_paths_reject_encoded_dangerous_values_and_wrong_extensions(paths):
    summary = build_evaluation_summary(_strict_entity(), _url_check(), _source_eval(), evaluation_report_paths=paths)

    assert summary["evaluation_report_paths"]["markdown"] == ""


def test_missing_or_inconsistent_metrics_fail_the_affected_component():
    missing = _strict_entity()
    del missing["metrics"]["overall"]["f1"]
    bad_links = _url_check()
    bad_links.update({"total_urls": 2, "checked_urls": 2, "accessible_urls": 2, "failed_urls": 1})
    bad_support = _source_eval()
    bad_support["relationship_count"] = 11

    entity_summary = build_evaluation_summary(missing, _url_check(), _source_eval())
    link_summary = build_evaluation_summary(_strict_entity(), bad_links, _source_eval())
    support_summary = build_evaluation_summary(_strict_entity(), _url_check(), bad_support)

    assert entity_summary["entity"]["status"] == "evaluation_failed"
    assert entity_summary["entity"]["overall"] is None
    assert link_summary["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert link_summary["public_links"]["details"]["accessibility"] == []
    assert support_summary["public_links"]["claim_support"]["status"] == "evaluation_failed"
    assert support_summary["public_links"]["details"]["claim_support"] == []


def test_malicious_numeric_subclass_never_escapes_summary_builder():
    class ExplodingFloat(float):
        def __float__(self):
            raise RuntimeError("private number failure")

    entity = _strict_entity(ExplodingFloat(0.9))
    summary = build_evaluation_summary(entity, _url_check(), _source_eval())

    assert summary["entity"]["status"] == "completed"
    assert "private number failure" not in repr(summary)


def test_zero_totals_reject_nonzero_component_counts():
    url = {"total_urls": 0, "checked_urls": 1, "accessible_urls": 1, "failed_urls": 0}
    support = {"relationship_count": 0, "supported_count": 1, "partially_supported_count": 0,
               "unsupported_count": 0, "unchecked_count": 0}

    summary = build_evaluation_summary(_strict_entity(), url, support)

    assert summary["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert summary["public_links"]["claim_support"]["status"] == "evaluation_failed"

    malformed = build_evaluation_summary(
        _strict_entity(),
        {"total_urls": 0, "checked_urls": "not-a-count"},
        {"relationship_count": 0, "supported_count": "not-a-count"},
    )
    assert malformed["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert malformed["public_links"]["claim_support"]["status"] == "evaluation_failed"


def test_rates_are_derived_from_counts_not_untrusted_raw_ratios():
    entity = _strict_entity()
    entity["metrics"]["overall"].update({"true_positive": 0, "false_positive": 1, "false_negative": 1,
                                            "precision": 1.0, "recall": 1.0, "f1": 1.0})
    entity["metrics"]["categories"] = {
        "organization": {"label": "机构", "true_positive": 0, "false_positive": 1, "false_negative": 1,
                           "precision": 1.0, "recall": 1.0, "f1": 1.0}
    }
    url = {"total_urls": 2, "checked_urls": 2, "accessible_urls": 0, "failed_urls": 2,
           "skipped_urls": 0, "accessibility_rate": 1.0,
           "results": [{"accessible": False}, {"accessible": False}]}
    support = {"relationship_count": 2, "supported_count": 0, "partially_supported_count": 1,
               "unsupported_count": 1, "unchecked_count": 0, "support_accuracy": 1.0,
               "relationships": [{}, {}]}

    summary = build_evaluation_summary(entity, url, support)

    assert summary["entity"]["overall"]["precision"] == 0.0
    assert summary["entity"]["overall"]["recall"] == 0.0
    assert summary["entity"]["overall"]["f1"] == 0.0
    assert summary["public_links"]["accessibility"]["rate"] == 0.0
    assert summary["public_links"]["claim_support"]["accuracy"] == 0.0


def test_diagnostic_text_is_normalized_and_detail_lists_are_audited():
    url = _url_check()
    url["results"] = [{"accessible": False, "failure_reason": "C:/secret/token", "warning": "private"}] * 100
    source = _source_eval()
    source["relationships"] = [{"status": "unsupported", "reason": "C:/secret/token"}] * 10

    summary = build_evaluation_summary(_strict_entity(), url, source)

    assert summary["public_links"]["details"]["accessibility"][0]["failure_reason"] == "network_or_unknown"
    assert "warning" not in summary["public_links"]["details"]["accessibility"][0]
    assert summary["public_links"]["details"]["claim_support"][0]["reason"] == "未找到充分来源支撑。"
    assert "secret" not in repr(summary)

    mismatched = _url_check()
    mismatched["results"] = []
    bad = build_evaluation_summary(_strict_entity(), mismatched, _source_eval())
    assert bad["public_links"]["accessibility"]["status"] == "evaluation_failed"


def test_evil_string_values_do_not_escape_whitelisting():
    class EvilString(str):
        def __str__(self):
            raise RuntimeError("private string failure")

    url = _url_check()
    url["results"] = [{"accessible": True, "method": EvilString("GET")}] * 100
    summary = build_evaluation_summary(_strict_entity(), url, _source_eval())

    assert summary["public_links"]["accessibility"]["status"] == "completed"
    assert "private string failure" not in repr(summary)
