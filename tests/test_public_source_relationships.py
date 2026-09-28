from __future__ import annotations

from unittest.mock import patch

import pytest

from gpt_researcher.evaluation import source_evaluator


def _check(claims: list[str], _source_text: str) -> dict:
    assert len(claims) == 1
    status = "supported" if "甲型" in claims[0] else "partially_supported"
    return {
        "status": status,
        "confidence": 1.0 if status == "supported" else 0.4,
        "reason": status,
        "matched_terms": [],
        "matched_numbers": [],
    }


def test_repeated_url_is_fetched_once_but_each_claim_is_scored() -> None:
    report = """甲型发动机已经交付。[URL1]
乙型发动机仍在试验。[URL1]

## 证据来源
[URL1] https://example.com/engine
"""
    with patch.object(source_evaluator, "_read_url_text", return_value="source") as reader, patch.object(
        source_evaluator, "_check_claims_against_source", side_effect=_check
    ) as checker:
        result = source_evaluator.evaluate_public_url_sources(report)

    reader.assert_called_once_with("https://example.com/engine")
    assert checker.call_count == 2
    assert result["method"] == "claim_url_relationship_source_text_matching"
    assert result["unique_url_count"] == 1
    assert result["relationship_count"] == 2
    assert result["supported_count"] == 1
    assert result["partially_supported_count"] == 1
    assert result["support_accuracy"] == 0.5
    assert len(result["relationships"]) == 2
    assert result["results"][0]["status"] == "partially_supported"
    assert result["results"][0]["citation_count"] == 2
    assert "checked_support_accuracy" not in result


def test_one_claim_with_two_url_refs_creates_two_relationships() -> None:
    report = """甲型发动机已经交付。[URL1][URL2]

## 证据来源
[URL1] https://example.com/one
[URL2] https://example.com/two
"""
    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已经交付"),
        ("[URL2]", "甲型发动机已经交付"),
    ]
    assert len({item["relationship_id"] for item in relationships}) == 2


def test_unchecked_relationship_remains_in_denominator() -> None:
    report = """甲型发动机已经交付。[URL1]
乙型发动机仍在试验。[URL2]

## 证据来源
[URL1] https://example.com/one
[URL2] https://example.com/two
"""
    def read(url: str) -> str:
        return "source" if url.endswith("/one") else ""

    with patch.object(source_evaluator, "_read_url_text", side_effect=read), patch.object(
        source_evaluator, "_check_claims_against_source", side_effect=lambda claims, text: (
            {"status": "supported", "confidence": 1.0, "reason": "match", "matched_terms": [], "matched_numbers": []}
            if text else
            {"status": "unchecked", "confidence": 0.0, "reason": "unreadable", "matched_terms": [], "matched_numbers": []}
        )
    ):
        result = source_evaluator.evaluate_public_url_sources(report)

    assert result["relationship_count"] == 2
    assert result["supported_count"] == 1
    assert result["unchecked_count"] == 1
    assert result["support_accuracy"] == 0.5
    assert result["results"][1]["source_readable"] is False


def test_missing_url_mapping_still_checks_each_relationship() -> None:
    report = """甲型发动机已经交付。[URL1]
乙型发动机仍在试验。[URL2]

## 证据来源
[URL1] https://example.com/one
"""
    def check(claims: list[str], source_text: str) -> dict:
        assert len(claims) == 1
        return {
            "status": "supported" if source_text else "unchecked",
            "confidence": 1.0 if source_text else 0.3,
            "reason": "checker reason",
            "matched_terms": ["乙型"] if not source_text else [],
            "matched_numbers": [],
        }

    with patch.object(source_evaluator, "_read_url_text", return_value="source") as reader, patch.object(
        source_evaluator, "_check_claims_against_source", side_effect=check
    ) as checker:
        result = source_evaluator.evaluate_public_url_sources(report)

    reader.assert_called_once_with("https://example.com/one")
    assert checker.call_count == 2
    assert [call.args[1] for call in checker.call_args_list] == ["source", ""]
    assert result["relationships"][1]["status"] == "unchecked"
    assert result["relationships"][1]["confidence"] == 0.3
    assert result["relationships"][1]["matched_terms"] == ["乙型"]
    assert "证据来源列表" in result["relationships"][1]["reason"]


def test_no_public_citations_have_no_accuracy() -> None:
    result = source_evaluator.evaluate_public_url_sources("只有本地证据。[原文1]")

    assert result["relationship_count"] == 0
    assert result["support_accuracy"] is None
    assert result["requirement_met"] is False
    assert result["relationships"] == []


def test_identical_claim_occurrences_are_not_deduplicated() -> None:
    report = """甲型发动机已经交付。[URL1]
另一节文字。
甲型发动机已经交付。[URL1]

## 证据来源
[URL1] https://example.com/one
"""
    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert len(relationships) == 2
    assert [item["claim"] for item in relationships] == ["甲型发动机已经交付"] * 2
    assert relationships[0]["relationship_id"] != relationships[1]["relationship_id"]
    assert relationships == source_evaluator._extract_url_claim_relationships(report)


def test_table_claims_and_evidence_list_are_handled_separately() -> None:
    report = """| 断言 | 引用 |
| --- | --- |
| 甲型发动机已经交付 | [URL1][URL2] |
| 乙型发动机仍在试验 | [URL2] |

## 证据来源
| [URL1] | https://example.com/one |
[URL2] https://example.com/two
"""
    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已经交付"),
        ("[URL2]", "甲型发动机已经交付"),
        ("[URL2]", "乙型发动机仍在试验"),
    ]


def test_different_refs_for_same_url_share_one_fetch() -> None:
    report = """甲型发动机已经交付。[URL1]
乙型发动机已经交付。[URL2]

## 证据来源
[URL1] https://example.com/engine
[URL2] https://example.com/engine
"""
    with patch.object(source_evaluator, "_read_url_text", return_value="source") as reader, patch.object(
        source_evaluator, "_check_claims_against_source", side_effect=_check
    ):
        result = source_evaluator.evaluate_public_url_sources(report)

    reader.assert_called_once_with("https://example.com/engine")
    assert result["unique_url_count"] == 1
    assert result["relationship_count"] == 2
    assert [item["ref"] for item in result["results"]] == ["[URL1]", "[URL2]"]


def test_unchecked_is_worst_aggregate_status_for_pruning() -> None:
    report = """甲型发动机已经交付。[URL1]
乙型发动机仍在试验。[URL1]

## 证据来源
[URL1] https://example.com/engine
"""
    def check(claims: list[str], _text: str) -> dict:
        status = "supported" if "甲型" in claims[0] else "unchecked"
        return {
            "status": status,
            "confidence": 1.0 if status == "supported" else 0.0,
            "reason": status,
            "matched_terms": [],
            "matched_numbers": [],
        }

    with patch.object(source_evaluator, "_read_url_text", return_value="source"), patch.object(
        source_evaluator, "_check_claims_against_source", side_effect=check
    ):
        result = source_evaluator.evaluate_public_url_sources(report)

    assert [item["status"] for item in result["relationships"]] == ["supported", "unchecked"]
    assert result["results"][0]["status"] == "unchecked"
    assert result["support_accuracy"] == 0.5


def test_report_tail_is_not_scored() -> None:
    report = """甲型发动机已经交付。[URL1]

## 本次运行统计
乙型发动机仍在试验。[URL2]
"""
    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [item["ref"] for item in relationships] == ["[URL1]"]


def test_two_claims_in_one_paragraph_keep_nearest_citations() -> None:
    report = "甲型发动机已经交付[URL1] 乙型发动机仍在试验[URL2]"

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已经交付"),
        ("[URL2]", "乙型发动机仍在试验"),
    ]


@pytest.mark.parametrize("local_first", [True, False])
def test_url_lookup_ignores_local_ref_with_same_number(local_first: bool) -> None:
    entries = [
        ("[原文1]", "local.pdf https://example.com/wrong"),
        ("[URL1]", "https://example.com/right"),
    ]
    evidence_map = dict(entries if local_first else reversed(entries))

    assert source_evaluator._url_for_ref("[URL1]", evidence_map) == "https://example.com/right"
    assert source_evaluator._url_for_ref("[URL1]", {"[原文1]": entries[0][1]}) == ""


def test_table_claim_includes_all_data_cells_for_real_matching() -> None:
    report = """| 类型 | 数值 | 引用 |
| --- | --- | --- |
| 甲型发动机 | 100 | [URL1] |

## 证据来源
[URL1] https://example.com/right
"""
    with patch.object(source_evaluator, "_read_url_text", return_value="乙型发动机 100"):
        result = source_evaluator.evaluate_public_url_sources(report)

    assert result["relationships"][0]["claim"] == "甲型发动机 100"
    assert result["relationships"][0]["status"] != "supported"


def test_table_escaped_pipe_stays_inside_one_claim_cell() -> None:
    report = r"| 甲型\|改型发动机 | 100 | [URL1] |"

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型 改型发动机 100"),
    ]


def test_table_header_and_separator_are_not_claim_relationships() -> None:
    report = """| 类型 | [URL1] |
| --- | --- |
| 甲型发动机 | [URL2] |
"""

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL2]", "甲型发动机"),
    ]


def test_narrative_evidence_phrase_does_not_hide_following_claims() -> None:
    report = """该结论的证据来源如下。
甲型发动机已经交付。[URL1]
乙型发动机仍在试验。[URL2]

## 证据来源
[URL1] https://example.com/one
[URL2] https://example.com/two
"""

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [item["ref"] for item in relationships] == ["[URL1]", "[URL2]"]


def test_next_heading_after_evidence_entries_restores_claim_parsing() -> None:
    report = """## 证据来源
[URL1] https://example.com/one
证据来源:
[URL2] https://example.com/two
## 分析
甲型发动机已经交付。[URL1]
"""

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已经交付"),
    ]


def test_evidence_directory_table_and_bullets_do_not_create_relationships() -> None:
    report = """## 证据来源列表
| 来源 | 引用 | 链接 |
| --- | --- | --- |
| FAA | [URL1] | https://example.com/one |
- 另见 FAA [URL2] https://example.com/two
"""

    with patch.object(source_evaluator, "_read_url_text") as reader:
        result = source_evaluator.evaluate_public_url_sources(report)

    reader.assert_not_called()
    assert result["relationship_count"] == 0
    assert result["relationships"] == []
    assert result["support_accuracy"] is None


def test_next_markdown_heading_ends_evidence_directory_interval() -> None:
    report = """## 证据来源列表
| FAA | [URL1] | https://example.com/one |
### 分析
甲型发动机已经交付。[URL2]
"""

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL2]", "甲型发动机已经交付"),
    ]


@pytest.mark.parametrize(
    "title",
    ["证据来源与可靠性分析", "证据来源说明", "来源列表评价", "证据来源方法"],
)
def test_extended_evidence_heading_is_analysis_not_directory(title: str) -> None:
    report = f"## {title}\n甲型发动机已交付。[URL1]\n"

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已交付"),
    ]


@pytest.mark.parametrize(
    "title",
    ["证据来源", "来源列表", "证据来源列表", "8. 证据来源", "八、证据来源："],
)
def test_exact_evidence_heading_starts_directory(title: str) -> None:
    report = f"## {title}\n| FAA | [URL1] | https://example.com/one |\n"

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert relationships == []


def test_standalone_next_line_citation_uses_previous_claim_only_without_boundary() -> None:
    report = """甲型发动机已经交付。
[URL1]

乙型发动机仍在试验。

[URL2]
## 分析
[URL3]
| 丙型发动机已交付 |
[URL4]
"""

    relationships = source_evaluator._extract_url_claim_relationships(report)

    assert [(item["ref"], item["claim"]) for item in relationships] == [
        ("[URL1]", "甲型发动机已经交付"),
    ]
