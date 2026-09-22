from __future__ import annotations

from gpt_researcher.evaluation.evaluation_summary import build_evaluation_summary


def test_strict_summary_publishes_f1_categories_and_thresholds():
    metrics = {
        "overall": {
            "true_positive": 9,
            "false_positive": 1,
            "false_negative": 0,
            "precision": 0.9,
            "recall": 1.0,
            "f1": 0.9474,
        },
        "categories": {
            "organization": {
                "label": "机构",
                "true_positive": 2,
                "false_positive": 0,
                "false_negative": 0,
                "precision": 1.0,
                "recall": 1.0,
                "f1": 1.0,
            }
        },
    }

    summary = build_evaluation_summary(
        {
            "mode": "strict",
            "status": "auto_evaluated",
            "metrics": metrics,
            "ground_truth_path": "truth.json",
            "auto_evidence_eval": {},
        },
        {
            "total_urls": 100,
            "checked_urls": 100,
            "accessible_urls": 98,
            "failed_urls": 2,
            "accessibility_rate": 0.98,
            "results": [],
        },
    )

    assert summary["status"] == "completed"
    assert summary["entity"]["overall"]["requirement_met"] is True
    assert summary["entity"]["threshold"] == 0.9
    assert summary["entity"]["categories"] == metrics["categories"]
    assert summary["public_links"]["requirement_met"] is True
    assert summary["public_links"]["threshold"] == 0.98


def test_proxy_summary_never_labels_support_rate_as_accuracy():
    summary = build_evaluation_summary(
        {
            "mode": "proxy",
            "status": "auto_evidence_checked",
            "metrics": None,
            "auto_evidence_eval": {"auto_evidence_accuracy": 0.75},
        },
        {
            "total_urls": 0,
            "checked_urls": 0,
            "accessible_urls": 0,
            "failed_urls": 0,
            "accessibility_rate": None,
            "results": [],
        },
    )

    assert summary["status"] == "completed"
    assert summary["entity"]["overall"] is None
    assert summary["entity"]["categories"] == {}
    assert summary["entity"]["proxy_evidence_support_rate"] == 0.75
    assert "accuracy" not in summary["entity"]
    assert summary["public_links"]["status"] == "no_public_urls"
    assert summary["public_links"]["requirement_met"] is None


def test_invalid_ground_truth_and_link_error_produce_failed_summary_and_safe_errors():
    summary = build_evaluation_summary(
        {
            "mode": "invalid",
            "status": "invalid_ground_truth",
            "metrics": None,
            "ground_truth_path": "C:/secret/private/broken.json",
            "ground_truth_error_code": "invalid_json",
            "ground_truth_message": "无法解析",
            "auto_evidence_eval": {},
        },
        {"evaluation_error": "TimeoutError: secret-token", "results": []},
    )

    assert summary["status"] == "failed"
    assert summary["entity"]["status"] == "invalid_ground_truth"
    assert summary["public_links"]["status"] == "evaluation_failed"
    assert len(summary["errors"]) == 2
    assert "broken.json" in summary["errors"][0]["message"]
    assert "C:/secret" not in repr(summary)
    assert "secret-token" not in repr(summary)


def test_single_component_failure_is_partial():
    summary = build_evaluation_summary(
        {
            "mode": "strict",
            "status": "auto_evaluated",
            "metrics": {
                "overall": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                "categories": {},
            },
        },
        {"evaluation_error": "network details"},
    )

    assert summary["status"] == "partial"
    assert summary["entity"]["status"] == "completed"
    assert summary["public_links"]["status"] == "evaluation_failed"


def test_missing_strict_metrics_is_an_entity_evaluation_failure():
    summary = build_evaluation_summary(
        {"mode": "strict", "status": "auto_evaluated", "metrics": None},
        {"total_urls": 1, "checked_urls": 1, "accessible_urls": 1, "failed_urls": 0, "accessibility_rate": 1.0},
    )

    assert summary["status"] == "partial"
    assert summary["entity"]["status"] == "evaluation_failed"
    assert summary["entity"]["overall"] is None
    assert summary["errors"][0]["scope"] == "entity"


def test_failed_links_do_not_reuse_partial_counts_or_rate():
    summary = build_evaluation_summary(
        {"mode": "proxy", "status": "auto_evidence_checked", "auto_evidence_eval": {}},
        {
            "evaluation_error": "boom",
            "total_urls": 4,
            "checked_urls": 1,
            "accessible_urls": 1,
            "failed_urls": 0,
            "accessibility_rate": 1.0,
        },
    )

    assert summary["public_links"]["status"] == "evaluation_failed"
    assert summary["public_links"]["accessibility_rate"] is None
    assert summary["public_links"]["requirement_met"] is None


def test_empty_strict_metrics_keep_requirement_neutral():
    summary = build_evaluation_summary(
        {
            "mode": "strict",
            "status": "auto_evaluated",
            "metrics": {
                "overall": {"precision": None, "recall": None, "f1": None},
                "categories": {},
            },
        },
        {"total_urls": 0, "accessibility_rate": None},
    )

    assert summary["entity"]["status"] == "completed"
    assert summary["entity"]["overall"]["requirement_met"] is None


def test_malformed_link_counts_fail_only_link_summary():
    summary = build_evaluation_summary(
        {
            "mode": "strict",
            "status": "auto_evaluated",
            "metrics": {
                "overall": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                "categories": {},
            },
        },
        {"total_urls": {"malformed": True}},
    )

    assert summary["status"] == "partial"
    assert summary["entity"]["status"] == "completed"
    assert summary["public_links"]["status"] == "evaluation_failed"
    assert summary["errors"][-1]["scope"] == "public_links"
