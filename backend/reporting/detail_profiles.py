"""Report length/detail profiles used by the 3-Agent report pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Dict


@dataclass(frozen=True)
class ReportDetailProfile:
    id: str
    label: str
    model: str
    body_min_chars: int
    body_max_chars: int
    section_min_chars: int
    expected_themes: int
    max_topics: int
    evidence_budget: int
    writer_max_tokens: int
    max_review_rounds: int
    include_toc: bool

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "body_min_chars": self.body_min_chars,
            "body_max_chars": self.body_max_chars,
            "max_topics": self.max_topics,
            "max_review_rounds": self.max_review_rounds,
            "include_toc": self.include_toc,
        }


REPORT_DETAIL_PROFILES: Dict[str, ReportDetailProfile] = {
    "brief": ReportDetailProfile(
        id="brief",
        label="短报告",
        model="deepseek-chat",
        body_min_chars=3500,
        body_max_chars=5000,
        section_min_chars=500,
        expected_themes=3,
        max_topics=6,
        evidence_budget=24000,
        writer_max_tokens=32768,
        max_review_rounds=3,
        include_toc=True,
    ),
    "detailed": ReportDetailProfile(
        id="detailed",
        label="详细报告",
        model="deepseek-v4-pro",
        body_min_chars=5000,
        body_max_chars=8000,
        section_min_chars=500,
        expected_themes=3,
        max_topics=6,
        evidence_budget=24000,
        writer_max_tokens=32768,
        max_review_rounds=3,
        include_toc=True,
    ),
}


def resolve_report_detail_profile(value: str) -> ReportDetailProfile:
    key = (value or "").strip().lower()
    try:
        return REPORT_DETAIL_PROFILES[key]
    except KeyError as exc:
        raise ValueError("report_detail must be 'brief' or 'detailed'") from exc


def _plain_model_name(value: str | None, fallback: str) -> str:
    configured = (value or fallback).strip()
    return configured.split(":", 1)[-1] if ":" in configured else configured


def resolve_profile_model(profile: ReportDetailProfile, environment=None) -> str:
    environment = os.environ if environment is None else environment
    override_env = (
        "DEEPSEEK_BRIEF_MODEL" if profile.id == "brief" else "DEEPSEEK_DETAILED_MODEL")
    return _plain_model_name(environment.get(override_env), profile.model)


def profile_with_environment_model(
        profile: ReportDetailProfile,
        environment=None,
) -> ReportDetailProfile:
    return replace(profile, model=resolve_profile_model(profile, environment))


def report_detail_catalog(environment=None) -> list[Dict[str, Any]]:
    return [
        profile_with_environment_model(profile, environment).public_metadata()
        for profile in REPORT_DETAIL_PROFILES.values()
    ]
