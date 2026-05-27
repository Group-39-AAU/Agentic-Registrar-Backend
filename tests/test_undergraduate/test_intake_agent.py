"""
Intake Validation Agent — node-level and pipeline tests.

No DB, no LLM. Tests drive the deterministic LangGraph node
functions directly (check_profile_complete, check_payment_verified,
decide) and then run the fully compiled pipeline via
run_intake_validation to lock the happy path and common error cases.
"""
from __future__ import annotations

import uuid

import pytest

from app.modules.undergraduate.agents.intake_agent import (
    IntakeState,
    check_payment_verified,
    check_profile_complete,
    decide,
    run_intake_validation,
)
from app.shared.enums import PaymentStatus, SponsorshipType


# ── Helpers ──────────────────────────────────────────────────────


def _state(**kwargs) -> IntakeState:
    defaults = dict(
        application_id=uuid.uuid4(),
        sponsorship_type=SponsorshipType.GOVERNMENT.value,
        stream="NATURAL",
        admission_number="ADM-001",
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
        payment_status=PaymentStatus.COMPLETED.value,
        current_status="PAYMENT_VERIFIED",
    )
    defaults.update(kwargs)
    return IntakeState(**defaults)


# ── check_profile_complete ────────────────────────────────────────


def test_government_application_passes_without_program_choices():
    state = _state(sponsorship_type=SponsorshipType.GOVERNMENT.value)
    result = check_profile_complete(state)
    assert "profile_completeness" in result.checks_passed
    assert "profile_completeness" not in result.checks_failed


def test_missing_stream_fails_profile_check():
    result = check_profile_complete(_state(stream=""))
    assert "profile_completeness" in result.checks_failed


def test_missing_admission_number_fails_profile_check():
    result = check_profile_complete(_state(admission_number=""))
    assert "profile_completeness" in result.checks_failed


def test_self_sponsored_without_program_choices_fails():
    state = _state(
        sponsorship_type=SponsorshipType.SELF_SPONSORED.value,
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
    )
    result = check_profile_complete(state)
    assert "profile_completeness" in result.checks_failed


def test_self_sponsored_with_all_program_choices_passes():
    pid = uuid.uuid4()
    state = _state(
        sponsorship_type=SponsorshipType.SELF_SPONSORED.value,
        program_choice_1_id=pid,
        program_choice_2_id=pid,
        program_choice_3_id=pid,
    )
    result = check_profile_complete(state)
    assert "profile_completeness" in result.checks_passed


def test_profile_check_appends_trace():
    state = _state()
    result = check_profile_complete(state)
    assert any(t["step_name"] == "check_profile_complete" for t in result.traces)


# ── check_payment_verified ────────────────────────────────────────


def test_completed_payment_passes():
    result = check_payment_verified(_state(payment_status=PaymentStatus.COMPLETED.value))
    assert "payment_verified" in result.checks_passed


def test_pending_payment_fails():
    result = check_payment_verified(_state(payment_status=PaymentStatus.PENDING.value))
    assert "payment_verified" in result.checks_failed


def test_failed_payment_fails():
    result = check_payment_verified(_state(payment_status=PaymentStatus.FAILED.value))
    assert "payment_verified" in result.checks_failed


def test_payment_check_appends_trace():
    state = _state()
    result = check_payment_verified(state)
    assert any(t["step_name"] == "check_payment_verified" for t in result.traces)


# ── decide ────────────────────────────────────────────────────────


def test_decide_pass_when_no_failures():
    state = IntakeState()
    state.checks_passed = ["profile_completeness", "payment_verified"]
    state.checks_failed = []
    assert decide(state).overall_result == "PASS"


def test_decide_flag_when_any_failure():
    state = IntakeState()
    state.checks_passed = ["payment_verified"]
    state.checks_failed = ["profile_completeness"]
    assert decide(state).overall_result == "FLAG_FOR_REVIEW"


def test_decide_appends_trace():
    state = IntakeState()
    state.checks_passed = []
    state.checks_failed = []
    result = decide(state)
    assert any(t["step_name"] == "decide" for t in result.traces)


# ── Full pipeline ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_pass_for_valid_government_application():
    # LangGraph invoke() returns a dict when the state is a dataclass.
    result = await run_intake_validation(
        application_id=uuid.uuid4(),
        sponsorship_type=SponsorshipType.GOVERNMENT.value,
        stream="NATURAL",
        admission_number="ADM-001",
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
        payment_status=PaymentStatus.COMPLETED.value,
        current_status="PAYMENT_VERIFIED",
    )
    assert result["overall_result"] == "PASS"
    assert not result["checks_failed"]


@pytest.mark.asyncio
async def test_pipeline_flags_self_sponsored_without_program_choices():
    result = await run_intake_validation(
        application_id=uuid.uuid4(),
        sponsorship_type=SponsorshipType.SELF_SPONSORED.value,
        stream="SOCIAL",
        admission_number="ADM-002",
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
        payment_status=PaymentStatus.COMPLETED.value,
        current_status="PAYMENT_VERIFIED",
    )
    assert result["overall_result"] == "FLAG_FOR_REVIEW"
    assert "profile_completeness" in result["checks_failed"]


@pytest.mark.asyncio
async def test_pipeline_flags_unpaid_application():
    result = await run_intake_validation(
        application_id=uuid.uuid4(),
        sponsorship_type=SponsorshipType.GOVERNMENT.value,
        stream="NATURAL",
        admission_number="ADM-003",
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
        payment_status=PaymentStatus.PENDING.value,
        current_status="SUBMITTED",
    )
    assert result["overall_result"] == "FLAG_FOR_REVIEW"
    assert "payment_verified" in result["checks_failed"]


@pytest.mark.asyncio
async def test_pipeline_result_always_contains_traces():
    result = await run_intake_validation(
        application_id=uuid.uuid4(),
        sponsorship_type=SponsorshipType.GOVERNMENT.value,
        stream="NATURAL",
        admission_number="ADM-004",
        program_choice_1_id=None,
        program_choice_2_id=None,
        program_choice_3_id=None,
        payment_status=PaymentStatus.COMPLETED.value,
        current_status="PAYMENT_VERIFIED",
    )
    step_names = {t["step_name"] for t in result["traces"]}
    assert {"check_profile_complete", "check_payment_verified", "decide"} <= step_names
