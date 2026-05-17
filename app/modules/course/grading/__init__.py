"""
Course Management — Track B (Grading Lifecycle) submodule.

Self-contained subpackage under the course module that owns everything
the grading workflow needs: roster derivation with add/drop deltas,
assessment-breakdown management, grade-batch lifecycle, the AI grading
monitor agent, the department-head authorization workflow, and the
student-facing transcript view.

Lives under course/ (not as a top-level module) because it depends on
the Grade, Section, Instructor, Registration, RegistrationCourse, and
StudentScheduleAddition entities defined in course/models.py, and on
the GradeSubmissionStatus / GradeLetter / OfficerRole enums in
app.shared.enums.

This package is being built incrementally:

  PR 1 (this commit) — roster derivation + two read endpoints:
      GET  /courses/grading/me/sections
      GET  /courses/grading/sections/{sid}/courses/{cid}/roster

  Later PRs add: assessment-breakdown CRUD, grade-batch lifecycle,
  the GradingMonitorAgent (LLM-as-reasoner with deterministic tool
  evidence), department-head authorisation, and the student transcript.
"""
