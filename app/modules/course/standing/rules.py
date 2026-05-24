"""
Track C — Academic Standing rules engine.

Pure functions implementing AAU Senate Legislation Articles 91.1
through 91.7 verbatim. No I/O, no DB access: the caller composes the
:class:`StandingComputeInput` from authorised grades and calls
:func:`evaluate_status` to get a :class:`StandingVerdict`.

Priority order matters — rules are evaluated top-to-bottom and the
first match wins. The order is:

  1. Held-for-review check (Art 90.7.1 — any I, NG, ** mark)
  2. Dismissal branches (Art 91.7.1 – 91.7.6)
  3. Warning branches (Art 91.3 – 91.6)
  4. Default: Promoted (Art 91.1)

The function returns the full reasoning trail (rule citations +
plain-language explanations) so the DH packet and the eventual LLM
narrative can quote the underlying rule chain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.shared.enums import AcademicStatusType, GradeLetter


# Letters that route the term through the held-for-review (Incomplete)
# branch rather than the numeric Article-91 evaluation. NG is the
# instructor's pre-submission default; I is the AC's post-grading
# conversion when illness or other reasons prevent completion. Both
# require human resolution before standing can be computed.
HOLD_LETTERS: frozenset[GradeLetter] = frozenset({
    GradeLetter.I, GradeLetter.NG,
})


@dataclass(frozen=True)
class TermGradeRow:
    """One authorised grade in the term being evaluated."""
    course_code: str
    letter: Optional[GradeLetter]
    credit_hours: int


@dataclass(frozen=True)
class StandingComputeInput:
    """
    Snapshot of everything Article 91 needs to evaluate one
    (student, term) pair. The caller (service layer) composes this
    from the Grade ledger + the student's history before invoking
    :func:`evaluate_status`.

    ``sgpa`` and ``cgpa`` may be None when the student has no
    grades-counting-toward-CGPA — the function treats that as
    a degenerate case and returns Incomplete-for-review.
    """
    # GPA snapshots (credit-weighted, computed by caller).
    sgpa: Optional[float]
    cgpa: Optional[float]

    # Term composition.
    term_grades: tuple[TermGradeRow, ...]
    term_credit_hours: int     # Sum of credit_hours of CGPA-counting grades

    # F-grade statistics for this term.
    f_count: int
    f_credit_total: int

    # New-admit context (for Articles 91.5 / 91.6 / 91.7.5 / 91.7.6).
    is_first_semester: bool
    is_first_year: bool

    # Probation continuity (for Article 91.7.1 / 91.7.4).
    prior_status: Optional[AcademicStatusType]
    consecutive_warning_count: int = 0


@dataclass(frozen=True)
class StandingVerdict:
    """
    Output of :func:`evaluate_status`. Carries the proposed status
    plus the reasoning trail for the DH packet.
    """
    proposed_status: AcademicStatusType
    requires_review: bool
    reasons: tuple[str, ...]
    rule_citations: tuple[str, ...] = field(default_factory=tuple)


# ── Constant thresholds (Article 91) ───────────────────────────

_WARNING_SGPA_FLOOR = 1.75
_WARNING_CGPA_FLOOR = 2.00
_DISMISSAL_FIRST_SEM_SGPA = 1.50
_DISMISSAL_FIRST_YEAR_CGPA = 1.75
_F_LOAD_ECTS_CEILING = 15
_F_CREDIT_DISMISSAL_THRESHOLD = 12


def evaluate_status(ctx: StandingComputeInput) -> StandingVerdict:
    """
    Run the Article-91 rules engine over ``ctx`` and return the
    proposed status with reasoning. Priority order is honoured;
    first match wins.

    Per Article 90.7.4 and 91.7, any I / NG mark in the term short-
    circuits to INCOMPLETE with ``requires_review=True`` so the
    officer resolves the unresolved coursework before the agent
    proposes a numeric verdict.
    """
    # ── 1. Held for review (Art 90.7.1) ──
    incomplete_rows = [
        g for g in ctx.term_grades
        if g.letter in HOLD_LETTERS
    ]
    if incomplete_rows:
        return StandingVerdict(
            proposed_status=AcademicStatusType.INCOMPLETE,
            requires_review=True,
            reasons=(
                f"{len(incomplete_rows)} unresolved mark(s) on this term "
                f"({', '.join(g.course_code for g in incomplete_rows)}). "
                "Term held for officer resolution before status is computed.",
            ),
            rule_citations=("Art 90.7.1",),
        )

    # ── 2. Degenerate input: nothing to evaluate ──
    if ctx.sgpa is None or ctx.cgpa is None:
        return StandingVerdict(
            proposed_status=AcademicStatusType.INCOMPLETE,
            requires_review=True,
            reasons=(
                "No grades counting toward CGPA were found for this term. "
                "Held for officer review.",
            ),
            rule_citations=("Art 90.7.4",),
        )

    # ── 3. Dismissal branches (Art 91.7) ──

    # 91.7.1 + 91.7.4 — consecutive probation without recovery.
    # Recovery = CGPA back to ≥ 2.00. If prior status was WARNING and
    # CGPA is still below the floor, dismissal is mandatory.
    if (
        ctx.prior_status == AcademicStatusType.WARNING
        and ctx.cgpa < _WARNING_CGPA_FLOOR
    ):
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Prior term status was WARNING and CGPA ({ctx.cgpa:.2f}) "
                f"remains below {_WARNING_CGPA_FLOOR:.2f}. Consecutive "
                "probation without recovery.",
            ),
            rule_citations=("Art 91.7.1", "Art 91.7.4"),
        )

    # 91.7.3 — SGPA < 1.75 AND CGPA < 2.00.
    if (
        ctx.sgpa < _WARNING_SGPA_FLOOR
        and ctx.cgpa < _WARNING_CGPA_FLOOR
    ):
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Semester GPA ({ctx.sgpa:.2f}) below "
                f"{_WARNING_SGPA_FLOOR:.2f} AND CGPA ({ctx.cgpa:.2f}) "
                f"below {_WARNING_CGPA_FLOOR:.2f}.",
            ),
            rule_citations=("Art 91.7.3",),
        )

    # 91.7.2 — F-load rules.
    if ctx.f_count >= 3 and ctx.f_credit_total > _F_CREDIT_DISMISSAL_THRESHOLD:
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Failed {ctx.f_count} course(s) totalling "
                f"{ctx.f_credit_total} ECTS, exceeding the "
                f"{_F_CREDIT_DISMISSAL_THRESHOLD}-ECTS ceiling for "
                "permissible failure load.",
            ),
            rule_citations=("Art 91.7.2",),
        )
    if ctx.f_count > 3:
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Failed {ctx.f_count} course(s) this semester, exceeding "
                "the cap of three.",
            ),
            rule_citations=("Art 91.7.2",),
        )

    # 91.7.5 — first-semester student with SGPA < 1.50.
    if ctx.is_first_semester and ctx.sgpa < _DISMISSAL_FIRST_SEM_SGPA:
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Newly admitted student with first-semester SGPA "
                f"({ctx.sgpa:.2f}) below {_DISMISSAL_FIRST_SEM_SGPA:.2f}.",
            ),
            rule_citations=("Art 91.7.5",),
        )

    # 91.7.6 — first-year student with CGPA < 1.75 (after first
    # semester). The first-semester case is handled by 91.7.5 above.
    if (
        ctx.is_first_year and not ctx.is_first_semester
        and ctx.cgpa < _DISMISSAL_FIRST_YEAR_CGPA
    ):
        return StandingVerdict(
            proposed_status=AcademicStatusType.DISMISSED,
            requires_review=False,
            reasons=(
                f"Newly admitted student with end-of-first-year CGPA "
                f"({ctx.cgpa:.2f}) below {_DISMISSAL_FIRST_YEAR_CGPA:.2f}.",
            ),
            rule_citations=("Art 91.7.6",),
        )

    # ── 4. Warning branches (Art 91.3 – 91.6) ──

    warning_reasons: list[str] = []
    warning_citations: list[str] = []

    # 91.3 — SGPA < 1.75 OR CGPA < 2.00.
    if ctx.sgpa < _WARNING_SGPA_FLOOR:
        warning_reasons.append(
            f"Semester GPA ({ctx.sgpa:.2f}) below "
            f"{_WARNING_SGPA_FLOOR:.2f}."
        )
        warning_citations.append("Art 91.3")
    elif ctx.cgpa < _WARNING_CGPA_FLOOR:
        warning_reasons.append(
            f"CGPA ({ctx.cgpa:.2f}) below {_WARNING_CGPA_FLOOR:.2f}."
        )
        warning_citations.append("Art 91.3")

    # 91.4 — up to 3 Fs in a semester carrying ≤ 15 ECTS.
    if (
        1 <= ctx.f_count <= 3
        and ctx.term_credit_hours <= _F_LOAD_ECTS_CEILING
    ):
        warning_reasons.append(
            f"Failed {ctx.f_count} course(s) in a low-load semester "
            f"({ctx.term_credit_hours} ECTS ≤ {_F_LOAD_ECTS_CEILING})."
        )
        warning_citations.append("Art 91.4")

    # 91.5 — first-semester student with SGPA in [1.50, 1.75).
    if (
        ctx.is_first_semester
        and _DISMISSAL_FIRST_SEM_SGPA <= ctx.sgpa < _WARNING_SGPA_FLOOR
    ):
        warning_reasons.append(
            f"Newly admitted student with first-semester SGPA "
            f"({ctx.sgpa:.2f}) in marginal range "
            f"[{_DISMISSAL_FIRST_SEM_SGPA:.2f}, "
            f"{_WARNING_SGPA_FLOOR:.2f})."
        )
        warning_citations.append("Art 91.5")

    # 91.6 — first-year student with CGPA in [1.75, 2.00).
    if (
        ctx.is_first_year and not ctx.is_first_semester
        and _DISMISSAL_FIRST_YEAR_CGPA <= ctx.cgpa < _WARNING_CGPA_FLOOR
    ):
        warning_reasons.append(
            f"Newly admitted student with end-of-first-year CGPA "
            f"({ctx.cgpa:.2f}) in marginal range "
            f"[{_DISMISSAL_FIRST_YEAR_CGPA:.2f}, "
            f"{_WARNING_CGPA_FLOOR:.2f})."
        )
        warning_citations.append("Art 91.6")

    if warning_reasons:
        return StandingVerdict(
            proposed_status=AcademicStatusType.WARNING,
            requires_review=False,
            reasons=tuple(warning_reasons),
            rule_citations=tuple(warning_citations),
        )

    # ── 5. Default: Promoted (Art 91.1) ──
    return StandingVerdict(
        proposed_status=AcademicStatusType.PROMOTED,
        requires_review=False,
        reasons=(
            f"Good standing: SGPA {ctx.sgpa:.2f}, CGPA {ctx.cgpa:.2f}, "
            f"no failing courses." if ctx.f_count == 0 else
            f"Good standing: SGPA {ctx.sgpa:.2f}, CGPA {ctx.cgpa:.2f}.",
        ),
        rule_citations=("Art 91.1",),
    )
