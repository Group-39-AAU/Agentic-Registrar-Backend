from __future__ import annotations


def validate_credit_load(
    current_credits: float,
    additional_credits: float,
    *,
    min_credits: float,
    max_credits: float,
) -> bool:
    """Return True if the resulting credit load is within [min_credits, max_credits]."""

    if min_credits < 0 or max_credits <= 0 or min_credits > max_credits:
        raise ValueError("Invalid credit bounds")
    new_load = current_credits + additional_credits
    return min_credits <= new_load <= max_credits

