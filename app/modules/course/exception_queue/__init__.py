"""
Course Management — Track C (Unified Exception Queue) submodule.

Named ``exception_queue/`` rather than ``exceptions/`` to avoid
clashing with the existing ``course/exceptions.py`` module that
defines the domain exception hierarchy.

A single read-only join over the three existing per-source queues:

  * Track A — open HIGH-risk advisory verdicts
    (``AdvisoryRecommendation.requires_officer_review = True`` AND
    ``reviewed_at IS NULL``)

  * Track B — FLAGGED grade batches awaiting DH review
    (``GradeBatch.status == FLAGGED``)

  * Track C — standing rows held for review
    (``AcademicStanding.requires_review = True`` AND
    ``AcademicStanding.final_status IS NULL``)

Each row is normalised into a uniform ``ExceptionQueueEntry`` shape
with ``source`` + ``source_id`` + a deep_link the UI uses to route
to the source-specific resolution endpoint. No new table — this is
a presentation layer over what's already persisted.
"""
