"""
Course Management — Track C (Academic Records) submodule.

Owns the student-facing "give me my paperwork" endpoints:

  * Grade report — official-shape per-term payload (grades + SGPA +
    CGPA + authorised academic status). One per completed term.
  * Filing slip — registration-support document for an upcoming or
    in-progress term (registered courses + section + payment status +
    carry-over standing).

PR C4 ships these as **structured JSON** so the frontend can render
HTML / print to PDF client-side. A later hardening PR introduces
server-side PDF rendering, HMAC signing, and the WORM archive.
"""
