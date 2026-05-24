"""
Course Management — Track C (Academic Standing & Records) submodule.

Self-contained subpackage under the course module that owns everything
between "grades become AUTHORISED" and "official paperwork issued":

  * Per-term SGPA, cumulative CGPA, and academic status (Promoted /
    Warning / Distinction / Dismissed / Incomplete) per AAU Senate
    Legislation Articles 90 & 91.
  * Three-dropdown DH workflow (term → department → section →
    students) for reviewing proposed standings.
  * Department-Head authorisation gate before any status is official.
  * Student-facing standing view + transcript extension.

Lives under course/ (not as a top-level module) because it depends on
the Student, AcademicTerm, Section, Registration, RegistrationCourse,
Grade, and CourseManagementOfficer entities defined in
course/models.py, and on the AcademicStatusType / OfficerRole /
GradeLetter enums in app.shared.enums.

This package ships incrementally:

  PR C1 (this commit) — Senate-aligned scaffolding for the DH-facing
      browse flow. No compute / authorise / override yet:
        GET  /courses/standing/terms
        GET  /courses/standing/terms/{tid}/departments
        GET  /courses/standing/terms/{tid}/departments/{dept}/sections
        GET  /courses/standing/terms/{tid}/sections/{sid}/students

  PR C2 will add the AcademicStandingAgent + compute / authorise /
  override / notify endpoints. PR C3 adds the student-facing view.
"""
