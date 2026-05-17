"""
Track C PR C2 — Article-91 rules engine tests.

Pure-function tests over :func:`evaluate_status`. No fixtures, no DB.
One test per Article-91 branch + ordering / priority tests.
"""
from __future__ import annotations

import pytest

from app.modules.course.standing.rules import (
    StandingComputeInput, TermGradeRow, evaluate_status,
)
from app.shared.enums import AcademicStatusType, GradeLetter


def _ctx(
    *,
    sgpa: float | None = 3.0,
    cgpa: float | None = 3.0,
    term_credit: int = 12,
    f_count: int = 0,
    f_credit: int = 0,
    is_first_semester: bool = False,
    is_first_year: bool = False,
    prior_status: AcademicStatusType | None = None,
    consecutive_warning: int = 0,
    term_grades: tuple[TermGradeRow, ...] = (),
) -> StandingComputeInput:
    return StandingComputeInput(
        sgpa=sgpa, cgpa=cgpa,
        term_grades=term_grades,
        term_credit_hours=term_credit,
        f_count=f_count, f_credit_total=f_credit,
        is_first_semester=is_first_semester, is_first_year=is_first_year,
        prior_status=prior_status,
        consecutive_warning_count=consecutive_warning,
    )


# ── Held-for-review branch (Art 90.7.1) ────────────────────────


def test_incomplete_mark_holds_for_review():
    """Any I in the term routes to INCOMPLETE with requires_review."""
    rows = (
        TermGradeRow("CS101", GradeLetter.I, 3),
        TermGradeRow("CS102", GradeLetter.A, 3),
    )
    verdict = evaluate_status(_ctx(term_grades=rows, sgpa=4.0, cgpa=4.0))
    assert verdict.proposed_status is AcademicStatusType.INCOMPLETE
    assert verdict.requires_review is True
    assert "Art 90.7.1" in verdict.rule_citations


def test_ng_mark_holds_for_review():
    rows = (TermGradeRow("CS101", GradeLetter.NG, 3),)
    verdict = evaluate_status(_ctx(term_grades=rows, sgpa=None, cgpa=None))
    assert verdict.proposed_status is AcademicStatusType.INCOMPLETE
    assert verdict.requires_review is True


def test_missing_gpa_holds_for_review():
    """SGPA / CGPA = None → can't evaluate → INCOMPLETE."""
    verdict = evaluate_status(_ctx(sgpa=None, cgpa=None))
    assert verdict.proposed_status is AcademicStatusType.INCOMPLETE
    assert verdict.requires_review is True


# ── Dismissal branches (Art 91.7) ──────────────────────────────


def test_91_7_1_consecutive_probation_dismisses():
    """Prior=WARNING AND CGPA still <2.00 → DISMISSED."""
    verdict = evaluate_status(_ctx(
        sgpa=1.90, cgpa=1.85,
        prior_status=AcademicStatusType.WARNING,
    ))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
    assert "Art 91.7.1" in verdict.rule_citations


def test_91_7_1_recovery_avoids_dismissal():
    """Prior=WARNING but CGPA recovered to ≥2.00 → not dismissed by 91.7.1."""
    verdict = evaluate_status(_ctx(
        sgpa=2.50, cgpa=2.10,
        prior_status=AcademicStatusType.WARNING,
    ))
    # Should NOT be dismissed by 91.7.1 (CGPA recovered).
    assert verdict.proposed_status is not AcademicStatusType.DISMISSED


def test_91_7_3_sgpa_and_cgpa_both_below_dismisses():
    """SGPA<1.75 AND CGPA<2.00 → DISMISSED."""
    verdict = evaluate_status(_ctx(sgpa=1.50, cgpa=1.90))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
    assert "Art 91.7.3" in verdict.rule_citations


def test_91_7_2_f_count_over_three_dismisses():
    """F-count > 3 in a single semester → DISMISSED."""
    verdict = evaluate_status(_ctx(
        sgpa=1.80, cgpa=2.10, f_count=4, f_credit=10,
    ))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
    assert "Art 91.7.2" in verdict.rule_citations


def test_91_7_2_three_fs_on_high_load_dismisses():
    """3 Fs covering >12 ECTS → DISMISSED."""
    verdict = evaluate_status(_ctx(
        sgpa=1.80, cgpa=2.10, f_count=3, f_credit=13,
    ))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED


def test_91_7_2_three_fs_on_light_load_does_not_dismiss():
    """3 Fs on exactly 12 ECTS doesn't trigger 91.7.2's >12 rule."""
    verdict = evaluate_status(_ctx(
        sgpa=2.10, cgpa=2.50, f_count=3, f_credit=12,
        term_credit=12,
    ))
    # 3 Fs at exactly 12 ECTS → falls through to warning branches at most.
    assert verdict.proposed_status is not AcademicStatusType.DISMISSED


def test_91_7_5_first_semester_sgpa_below_1_50_dismisses():
    verdict = evaluate_status(_ctx(
        sgpa=1.40, cgpa=1.40,
        is_first_semester=True, is_first_year=True,
    ))
    # Note: 91.7.3 (sgpa<1.75 AND cgpa<2.0) also matches; priority order
    # places 91.7.3 first, so we get DISMISSED via 91.7.3.
    assert verdict.proposed_status is AcademicStatusType.DISMISSED


def test_91_7_5_isolated_first_semester_failure():
    """
    First semester student with SGPA=1.40 but with CGPA back at ≥2.00
    (hypothetical via prior credit?). In practice not reachable, but
    the rule still applies if 91.7.3 doesn't fire.
    """
    verdict = evaluate_status(_ctx(
        sgpa=1.40, cgpa=2.10,
        is_first_semester=True, is_first_year=True,
    ))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
    assert "Art 91.7.5" in verdict.rule_citations


def test_91_7_6_first_year_cgpa_below_1_75_dismisses():
    """End-of-first-year CGPA < 1.75 → DISMISSED."""
    verdict = evaluate_status(_ctx(
        sgpa=2.00, cgpa=1.70,
        is_first_semester=False, is_first_year=True,
    ))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
    assert "Art 91.7.6" in verdict.rule_citations


# ── Warning branches (Art 91.3 – 91.6) ─────────────────────────


def test_91_3_low_sgpa_warning():
    """SGPA<1.75 (but CGPA OK) → WARNING."""
    verdict = evaluate_status(_ctx(sgpa=1.70, cgpa=2.50))
    assert verdict.proposed_status is AcademicStatusType.WARNING


def test_91_3_low_cgpa_warning():
    """CGPA<2.00 (but SGPA OK) → WARNING."""
    verdict = evaluate_status(_ctx(sgpa=2.50, cgpa=1.95))
    assert verdict.proposed_status is AcademicStatusType.WARNING


def test_91_4_f_count_on_light_load_warning():
    """Up to 3 Fs in a ≤15-ECTS semester → WARNING."""
    verdict = evaluate_status(_ctx(
        sgpa=2.10, cgpa=2.50, f_count=2, f_credit=6,
        term_credit=12,
    ))
    assert verdict.proposed_status is AcademicStatusType.WARNING
    assert "Art 91.4" in verdict.rule_citations


def test_91_4_does_not_fire_on_high_load_semester():
    """3 Fs on a 16-ECTS semester does not trigger 91.4."""
    verdict = evaluate_status(_ctx(
        sgpa=2.30, cgpa=2.50, f_count=3, f_credit=9,
        term_credit=16,
    ))
    # No warning rule fires → promoted.
    assert verdict.proposed_status is AcademicStatusType.PROMOTED


def test_91_5_first_semester_marginal_sgpa_warning():
    """First-semester SGPA in [1.50, 1.75) → WARNING."""
    verdict = evaluate_status(_ctx(
        sgpa=1.60, cgpa=2.10,
        is_first_semester=True, is_first_year=True,
    ))
    # 91.3 also fires for SGPA<1.75; ours composes both reasons.
    assert verdict.proposed_status is AcademicStatusType.WARNING


def test_91_6_first_year_marginal_cgpa_warning():
    """End-of-first-year CGPA in [1.75, 2.00) → WARNING."""
    verdict = evaluate_status(_ctx(
        sgpa=2.50, cgpa=1.80,
        is_first_semester=False, is_first_year=True,
    ))
    assert verdict.proposed_status is AcademicStatusType.WARNING


# ── Default / promoted (Art 91.1) ──────────────────────────────


def test_91_1_good_standing_promoted():
    verdict = evaluate_status(_ctx(sgpa=3.50, cgpa=3.30))
    assert verdict.proposed_status is AcademicStatusType.PROMOTED
    assert verdict.requires_review is False


def test_91_1_high_load_with_one_f_still_promoted():
    """1 F on a 16-ECTS load with healthy GPAs → PROMOTED (not 91.4)."""
    verdict = evaluate_status(_ctx(
        sgpa=2.80, cgpa=2.90, f_count=1, f_credit=3,
        term_credit=16,
    ))
    assert verdict.proposed_status is AcademicStatusType.PROMOTED


# ── Priority ordering ──────────────────────────────────────────


def test_incomplete_takes_priority_over_dismissal_branches():
    """An I in the term wins even when GPAs would otherwise dismiss."""
    rows = (TermGradeRow("CS101", GradeLetter.I, 3),)
    verdict = evaluate_status(_ctx(
        sgpa=1.20, cgpa=1.20, f_count=4,
        term_grades=rows,
    ))
    assert verdict.proposed_status is AcademicStatusType.INCOMPLETE


def test_dismissal_takes_priority_over_warning():
    """SGPA=1.50 AND CGPA=1.85 satisfies BOTH 91.7.3 (dismiss) and
    91.3 (warn). Dismissal wins."""
    verdict = evaluate_status(_ctx(sgpa=1.50, cgpa=1.85))
    assert verdict.proposed_status is AcademicStatusType.DISMISSED
