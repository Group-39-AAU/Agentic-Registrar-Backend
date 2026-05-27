"""
OUTDATED AGENT: This file contains the original implementation of the 
Academic Credential Verification Agent — LangGraph StateGraph.

NOLONGER IN ACTIVE USE

The second AI agent in the Agentic Registrar pipeline. Triggered when an
application is in UNDER_VERIFICATION status. It:

1. Runs OCR on the uploaded Grade 12 certificate
2. Queries the simulated MoE database using the extracted admission number
3. Cross-checks each field (name, subject scores, exam year)
4. Checks authenticity markers (stamp, authority signature)
5. Decides: PASS → AI_PRE_SCREENING or FLAG_FOR_REVIEW → stays

Each node writes an AIExecutionTrace for full explainability.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger

logger = get_logger("ai.credential_verification_agent")

AGENT_VERSION = "credential-verification-agent-v1.0"


# ── Agent State ──────────────────────────────────────────────

@dataclass
class CredentialVerificationState:
    """Mutable state passed through the LangGraph nodes."""
    # Input
    application_id: uuid.UUID = field(default_factory=uuid.uuid4)
    certificate_path: str = ""

    # Populated by extract_ocr node
    extracted_data: dict = field(default_factory=dict)

    # Populated by query_moe node
    moe_record: Optional[dict] = None
    moe_found: bool = False

    # Populated by cross_check node
    discrepancies: list[dict] = field(default_factory=list)
    matches: list[str] = field(default_factory=list)

    # Populated by check_authenticity node
    authenticity_issues: list[str] = field(default_factory=list)

    # Populated by all nodes
    traces: list[dict] = field(default_factory=list)

    # Final result
    overall_result: str = ""  # "PASS" or "FLAG_FOR_REVIEW"
    confidence_score: float = 0.0
    summary: str = ""


# ── Agent Nodes ──────────────────────────────────────────────

def extract_ocr(state: CredentialVerificationState) -> dict:
    print("AAAAA")
    """Node 1: Run OCR on the certificate and extract structured data."""
    from app.ai.ocr import extract_certificate_data

    reasoning_lines = []
    reasoning_lines.append(f"Processing certificate: {state.certificate_path}")

    extracted = extract_certificate_data(state.certificate_path)

    if extracted.get("student_name"):
        reasoning_lines.append(f"OK: Extracted student name: {extracted['student_name']}")
    else:
        reasoning_lines.append("WARNING: Could not extract student name")

    if extracted.get("admission_number"):
        reasoning_lines.append(f"OK: Extracted admission number: {extracted['admission_number']}")
    else:
        reasoning_lines.append("FAIL: Could not extract admission number — cannot cross-check")

    subjects = extracted.get("subjects", {})
    if subjects:
        reasoning_lines.append(f"OK: Extracted {len(subjects)} subject scores: {subjects}")
    else:
        reasoning_lines.append("WARNING: No subject scores extracted")

    if extracted.get("exam_year"):
        reasoning_lines.append(f"OK: Exam year: {extracted['exam_year']}")

    logger.info("OCR extraction complete for application %s", state.application_id)

    return {
        "extracted_data": extracted,
        "traces": state.traces + [{
            "step_name": "extract_ocr",
            "reasoning_log": "\n".join(reasoning_lines),
            "result": "OK" if extracted.get("admission_number") else "PARTIAL",
        }],
    }


def query_moe(state: CredentialVerificationState) -> dict:
    print("BBBBB")
    """
    Node 2: Query the MoE database using the extracted admission number.
    This is a SYNCHRONOUS DB query — we use the sync pattern here because
    LangGraph nodes are sync. The caller passes the MoE data in.
    """
    reasoning_lines = []
    admission_number = state.extracted_data.get("admission_number")

    if not admission_number:
        reasoning_lines.append("FAIL: No admission number available — cannot query MoE")
        return {
            "moe_found": False,
            "moe_record": None,
            "traces": state.traces + [{
                "step_name": "query_moe",
                "reasoning_log": "\n".join(reasoning_lines),
                "result": "FAIL",
            }],
        }

    # The MoE record is injected into state by the caller (router)
    # since LangGraph nodes are synchronous and can't do async DB queries.
    if state.moe_record is not None:
        reasoning_lines.append(f"OK: MoE record found for admission number: {admission_number}")
        reasoning_lines.append(f"  MoE name: {state.moe_record.get('full_name')}")
        reasoning_lines.append(f"  MoE subjects: {state.moe_record.get('subjects')}")
        reasoning_lines.append(f"  MoE total score: {state.moe_record.get('total_score')}")
        moe_found = True
    else:
        reasoning_lines.append(
            f"FAIL: No MoE record found for admission number: {admission_number}. "
            "This could indicate a fraudulent or unregistered certificate."
        )
        moe_found = False

    logger.info(
        "MoE query result for application %s: found=%s",
        state.application_id, moe_found,
    )

    return {
        "moe_found": moe_found,
        "traces": state.traces + [{
            "step_name": "query_moe",
            "reasoning_log": "\n".join(reasoning_lines),
            "result": "OK" if moe_found else "FAIL",
        }],
    }


def cross_check(state: CredentialVerificationState) -> dict:
    print("CCCCC")
    """Node 3: Compare extracted certificate data against MoE records."""
    reasoning_lines = []
    discrepancies = []
    matches = []

    if not state.moe_found or state.moe_record is None:
        reasoning_lines.append("SKIP: No MoE record to cross-check against")
        discrepancies.append({
            "field": "moe_record",
            "issue": "No MoE record found — cannot verify authenticity",
        })
        return {
            "discrepancies": discrepancies,
            "matches": matches,
            "traces": state.traces + [{
                "step_name": "cross_check",
                "reasoning_log": "\n".join(reasoning_lines),
                "result": "FAIL",
            }],
        }

    moe = state.moe_record
    extracted = state.extracted_data

    # ── Check name ──
    extracted_name = (extracted.get("student_name") or "").strip().lower()
    moe_name = (moe.get("full_name") or "").strip().lower()

    if extracted_name and moe_name:
        if extracted_name == moe_name:
            reasoning_lines.append(f"OK: Name matches — '{extracted.get('student_name')}'")
            matches.append("student_name")
        else:
            reasoning_lines.append(
                f"DISCREPANCY: Name mismatch — "
                f"Certificate: '{extracted.get('student_name')}' vs MoE: '{moe.get('full_name')}'"
            )
            discrepancies.append({
                "field": "student_name",
                "certificate_value": extracted.get("student_name"),
                "moe_value": moe.get("full_name"),
                "issue": "Name does not match MoE records",
            })
    else:
        reasoning_lines.append("WARNING: Cannot compare names — data missing")

    # ── Check exam year ──
    extracted_year = extracted.get("exam_year")
    moe_year = moe.get("exam_year")

    if extracted_year and moe_year:
        if extracted_year == moe_year:
            reasoning_lines.append(f"OK: Exam year matches — {extracted_year}")
            matches.append("exam_year")
        else:
            reasoning_lines.append(
                f"DISCREPANCY: Exam year mismatch — "
                f"Certificate: {extracted_year} vs MoE: {moe_year}"
            )
            discrepancies.append({
                "field": "exam_year",
                "certificate_value": extracted_year,
                "moe_value": moe_year,
                "issue": "Exam year does not match",
            })

    # ── Check each subject score ──
    cert_subjects = extracted.get("subjects", {})
    moe_subjects = moe.get("subjects", {})

    for subject, cert_score in cert_subjects.items():
        moe_score = moe_subjects.get(subject)
        if moe_score is None:
            reasoning_lines.append(
                f"WARNING: Subject '{subject}' on certificate not found in MoE records"
            )
            continue

        if cert_score == moe_score:
            reasoning_lines.append(f"OK: {subject} score matches — {cert_score}")
            matches.append(f"subject_{subject.lower()}")
        else:
            reasoning_lines.append(
                f"DISCREPANCY: {subject} score mismatch — "
                f"Certificate: {cert_score} vs MoE: {moe_score}"
            )
            discrepancies.append({
                "field": f"subject_{subject}",
                "certificate_value": cert_score,
                "moe_value": moe_score,
                "issue": f"{subject} score does not match MoE records",
            })

    # Check for subjects in MoE not on certificate
    for subject in moe_subjects:
        if subject not in cert_subjects:
            reasoning_lines.append(
                f"WARNING: MoE subject '{subject}' not found on certificate"
            )

    logger.info(
        "Cross-check for application %s: %d matches, %d discrepancies",
        state.application_id, len(matches), len(discrepancies),
    )

    return {
        "discrepancies": discrepancies,
        "matches": matches,
        "traces": state.traces + [{
            "step_name": "cross_check",
            "reasoning_log": "\n".join(reasoning_lines),
            "result": "PASS" if not discrepancies else "FAIL",
        }],
    }


def check_authenticity(state: CredentialVerificationState) -> dict:
    print("DDDDD")
    """Node 4: Verify stamp and authority signature presence."""
    reasoning_lines = []
    issues = []
    extracted = state.extracted_data

    has_stamp = extracted.get("has_stamp", False)
    has_signature = extracted.get("has_signature", False)

    if has_stamp:
        reasoning_lines.append("OK: Official stamp/seal detected")
    else:
        reasoning_lines.append("FAIL: No official stamp/seal detected — potential fraud indicator")
        issues.append("Missing official stamp/seal")

    if has_signature:
        reasoning_lines.append("OK: Authority signature detected")
    else:
        reasoning_lines.append("FAIL: No authority signature detected — potential fraud indicator")
        issues.append("Missing authority signature")

    result = "PASS" if not issues else "FAIL"
    logger.info(
        "Authenticity check for application %s: %s",
        state.application_id, result,
    )

    return {
        "authenticity_issues": issues,
        "traces": state.traces + [{
            "step_name": "check_authenticity",
            "reasoning_log": "\n".join(reasoning_lines),
            "result": result,
        }],
    }


def decide(state: CredentialVerificationState) -> dict:
    """Node 5: Final decision based on all checks."""
    total_issues = len(state.discrepancies) + len(state.authenticity_issues)
    total_checks = len(state.matches) + total_issues + (2 if state.moe_found else 0)

    if total_issues == 0 and state.moe_found:
        overall_result = "PASS"
        confidence = 1.0
        summary = (
            f"Certificate verification PASSED. "
            f"{len(state.matches)} data points matched MoE records. "
            f"Authenticity markers (stamp + signature) confirmed."
        )
    elif total_issues == 0 and not state.moe_found:
        overall_result = "FLAG_FOR_REVIEW"
        confidence = 0.3
        summary = (
            "Certificate could not be verified — no MoE record found. "
            "Manual review required."
        )
    else:
        # Calculate confidence based on how many checks passed vs failed
        if total_checks > 0:
            confidence = max(0.0, 1.0 - (total_issues / max(total_checks, 1)))
        else:
            confidence = 0.0

        overall_result = "FLAG_FOR_REVIEW"
        issue_details = []
        for d in state.discrepancies:
            issue_details.append(d["issue"])
        for a in state.authenticity_issues:
            issue_details.append(a)

        summary = (
            f"Certificate verification FAILED with {total_issues} issue(s): "
            f"{'; '.join(issue_details)}. "
            f"Flagged for human review."
        )

    reasoning = (
        f"Final decision: {overall_result}\n"
        f"Confidence: {confidence:.2f}\n"
        f"Matches: {state.matches}\n"
        f"Discrepancies: {[d['issue'] for d in state.discrepancies]}\n"
        f"Authenticity issues: {state.authenticity_issues}\n"
        f"Summary: {summary}"
    )

    logger.info(
        "Credential verification decision for application %s: %s (confidence=%.2f)",
        state.application_id, overall_result, confidence,
    )

    return {
        "overall_result": overall_result,
        "confidence_score": confidence,
        "summary": summary,
        "traces": state.traces + [{
            "step_name": "decide",
            "reasoning_log": reasoning,
            "result": overall_result,
        }],
    }


# ── Build the Graph ──────────────────────────────────────────

def build_credential_verification_graph():
    """
    Construct the LangGraph StateGraph for the Credential Verification Agent.

    Flow: extract_ocr → query_moe → cross_check → check_authenticity → decide → END
    """
    graph = StateGraph(CredentialVerificationState)

    graph.add_node("extract_ocr", extract_ocr)
    graph.add_node("query_moe", query_moe)
    graph.add_node("cross_check", cross_check)
    graph.add_node("check_authenticity", check_authenticity)
    graph.add_node("decide", decide)

    graph.set_entry_point("extract_ocr")
    graph.add_edge("extract_ocr", "query_moe")
    graph.add_edge("query_moe", "cross_check")
    graph.add_edge("cross_check", "check_authenticity")
    graph.add_edge("check_authenticity", "decide")
    graph.add_edge("decide", END)

    return graph.compile()


# ── Public API ──────────────────────────────────────────────

def run_credential_verification(
    application_id: uuid.UUID,
    certificate_path: str,
    moe_record: Optional[dict] = None,
) -> dict:
    """
    Run the full credential verification pipeline.

    Args:
        application_id: The application being verified
        certificate_path: Path to the uploaded GRADE_12_CERTIFICATE
        moe_record: Pre-fetched MoE record dict (queried async by the caller)

    Returns:
        Final state dict with overall_result, confidence_score, summary, traces,
        discrepancies, authenticity_issues, matches.
    """
    initial_state = CredentialVerificationState(
        application_id=application_id,
        certificate_path=certificate_path,
        moe_record=moe_record,
    )

    compiled_graph = build_credential_verification_graph()
    return compiled_graph.invoke(initial_state)
