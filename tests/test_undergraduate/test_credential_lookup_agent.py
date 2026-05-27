"""Unit tests for MoE credential lookup name cross-check."""

from app.modules.undergraduate.agents.credential_lookup_agent import run_credential_lookup
from app.shared.enums import DecisionType


def test_name_mismatch_flags_when_surname_differs_by_one_letter():
    result = run_credential_lookup(
        admission_number="ADM-001",
        student_name="ABENEZER SEIFUU",
        moe_full_name="ABENEZER SEIFU",
    )

    assert result.overall_result == "FLAG"
    assert result.recommended == DecisionType.FLAG_FOR_REVIEW
    assert any("Name mismatch" in issue for issue in result.issues)


def test_name_match_when_identical_after_normalization():
    result = run_credential_lookup(
        admission_number="ADM-001",
        student_name="  abenezer   seifu ",
        moe_full_name="ABENEZER SEIFU",
    )

    assert result.overall_result == "PASS"
    assert result.recommended == DecisionType.RECOMMEND_ADMIT
    assert not result.issues
