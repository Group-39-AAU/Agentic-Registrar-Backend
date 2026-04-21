"""Credential lookup agent for MoE admission-number verification."""

from dataclasses import dataclass, field
from typing import Optional

from app.shared.enums import DecisionType

AGENT_VERSION = "credential-verification-v3.0"


@dataclass
class CredentialLookupResult:
    """Normalized output produced by the credential lookup agent."""

    overall_result: str
    confidence: float
    recommended: DecisionType
    summary: str
    issues: list[str] = field(default_factory=list)
    traces: list[dict[str, str]] = field(default_factory=list)


def run_credential_lookup(
    admission_number: str,
    student_name: str,
    moe_full_name: Optional[str],
) -> CredentialLookupResult:
    """Cross-check applicant identity against MoE data using admission number."""
    issues: list[str] = []
    traces: list[dict[str, str]] = []

    if not admission_number:
        issues.append("Application has no admission number")
        traces.append(
            {
                "step_name": "moe_lookup",
                "reasoning_log": "FAIL: Admission number is missing",
            }
        )
    elif moe_full_name is None:
        issues.append(f"No MoE record found for admission number: {admission_number}")
        traces.append(
            {
                "step_name": "moe_lookup",
                "reasoning_log": (
                    f"FAIL: No record found for admission_number={admission_number}"
                ),
            }
        )
    else:
        traces.append(
            {
                "step_name": "moe_lookup",
                "reasoning_log": (
                    f"OK: Found MoE record for {admission_number}: {moe_full_name}"
                ),
            }
        )

        if student_name in moe_full_name or moe_full_name in student_name:
            traces.append(
                {
                    "step_name": "name_cross_check",
                    "reasoning_log": (
                        f"OK: Student name '{student_name}' matches MoE name '{moe_full_name}'"
                    ),
                }
            )
        else:
            issues.append(
                f"Name mismatch: application has '{student_name}' but MoE has '{moe_full_name}'"
            )
            traces.append(
                {
                    "step_name": "name_cross_check",
                    "reasoning_log": (
                        f"FAIL: '{student_name}' does not match '{moe_full_name}'"
                    ),
                }
            )

    if not issues:
        return CredentialLookupResult(
            overall_result="PASS",
            confidence=1.0,
            recommended=DecisionType.RECOMMEND_ADMIT,
            summary=(
                "Credentials verified: admission number "
                f"{admission_number} matches MoE record."
            ),
            issues=issues,
            traces=traces,
        )

    return CredentialLookupResult(
        overall_result="FLAG",
        confidence=0.0,
        recommended=DecisionType.FLAG_FOR_REVIEW,
        summary=f"Credential issues: {'; '.join(issues)}",
        issues=issues,
        traces=traces,
    )
