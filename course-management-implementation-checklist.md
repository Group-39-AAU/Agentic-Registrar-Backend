# Course Management Module — Implementation Checklist

This document lists, feature by feature and agent by agent, what each implementation "will have". It is meant as a working checklist so developers can scan exactly what capabilities ship with each piece of work. Companion to [cource-management-plan.md](cource-management-plan.md) and [course-management-usecase-plan.md](course-management-usecase-plan.md).

## SRS/SDS Traceability

Every operation, attribute, enum, and invariant on this checklist is traceable to a numbered table or figure in the project's source-of-truth specifications:

- **SRS** — `Software Requirements Specification` (Course-FR-01 to Course-FR-13 in §3.2.3; UC-CM-01 to UC-CM-13 in §3.3; Inverse Requirements in §3.5; Logical Database Requirements in §3.7).
- **SDS** — `Software Design Specification` §3.1.3 Course Management Class Diagram (Figure 5), §3.2.3 Course Management Sequence Diagrams (Figures 25–37), §3.3 State Diagram (Figure 39), §5.3 Detailed Design (Tables 55–84).

Concrete invariants imported verbatim from the SDS that drive the implementation:

| Constraint | Value | SDS source |
|---|---|---|
| Max credit load | 22 ECTS | Table 65 (`CurriculumComplianceAgent.validationRules`) |
| Min credit load (cannot drop below) | 12 ECTS | Table 80 (`EnrollmentAdjustmentAgent.adjustmentRules`) |
| Anomaly threshold (default) | 2.5 standard deviations | Table 71 (`AssessmentValidationAgent.anomalyThreshold`) |
| Warning threshold | CGPA < 2.0 | Table 74 (`AcademicStandingAgent.statusRules`) |
| Distinction threshold | CGPA > 3.5 | Table 74 |
| Lecture hours | 08:30 – 17:30 | Table 83 (`AcademicSchedulingAgent.timeSlots`) |
| Assessment-weight sum | exactly 100 | Table 60 (`Instructor.updateAssessmentBreakdown`) |
| Archive storage | Write-Once-Read-Many (WORM) | Table 77 (`AcademicRecordsAgent.archiveStoragePath`) |

---

## Track A — Registration, Scheduling and Advising

### Implement Course Registration will have

- A registration window per academic term that can be opened or closed by an officer
- A student-facing portal that lists only the courses offered in the currently open term
- A curriculum filter so a student only sees courses that belong to their program
- A registration draft that the student can build, save, and return to before submitting
- A final-submit action that locks the chosen courses and triggers automatic validation
- A payment gate that blocks finalisation until the cost-sharing form or per-credit payment is confirmed
- A visible registration status timeline matching SDS Figure 39: `Registration_Open → Advisor_Review → Validation_Pending (Checking_Prerequisites → Checking_Payment → Validation_Success | Payment_Hold) → Registered → Add_Drop_Window`
- A per-student registration record that stores the term, sponsorship type, chosen courses, and payment reference (sourced from `PayMock.getPaymentStatus(studentID, courseID)` per SDS Table 87)
- A re-registration guard that prevents two registrations from the same student in the same term
- An officer read-only view of every student's current registration for audit
- An immutable history entry for every status change on a registration

### Implement Add/Drop will have

- An add/drop request form accessible to the student only while the window is open (`EnrollmentAdjustmentAgent.validateAdjustmentWindow()` per SDS Table 81)
- A deadline snapshot stored on each request so late-window rule changes do not retroactively break records
- Automatic re-validation of credit load whenever a course is added or dropped, enforcing **min 12 ECTS** and **max 22 ECTS** per the SDS `adjustmentRules` invariant (Table 80)
- Automatic section-capacity update when a course is added or dropped (`EnrollmentAdjustmentAgent.updateSectionCapacity(courseID, action)` where `action ∈ {ADD, DROP}`)
- A payment cross-check for any add that attracts a new fee (`crossCheckPayment(studentID)`)
- An officer override path for requests that the agent has blocked
- A confirmation notification to the student after the request is resolved (`notifyAdjustmentSuccess(studentID)`)
- An immutable history entry for every add/drop resolution

### Implement Section and Schedule Generation will have

- An officer-triggered "Generate Schedule" action scoped to a department and term (`AcademicSchedulingAgent.generateTimetable(departmentID): Schedule` per SDS Table 84)
- Automatic grouping of registered students into sections based on capacity (`allocateSections(studentList)`)
- Automatic assignment of a room and a time slot to each section, drawn from the SDS `timeSlots` invariant of standard university lecture hours **08:30 – 17:30** (Table 83), via `getAvailableRooms(capacityReq, time): List<Room>`
- Automatic assignment of an instructor to each section from that department (`assignInstructor(courseID, instructorID)`)
- Conflict detection for room double-booking and instructor double-booking
- A fallback strategy that swaps rooms or time slots when conflicts are detected (`resolveRoomConflict(courseID, slot): Boolean`)
- A human-visible conflict report for cases the agent cannot resolve alone
- A read-only timetable endpoint for each student, instructor, and department
- A re-generate action that preserves history (old schedules are archived, not overwritten)

### Implement Academic Advisory will have

- A student-triggered "Evaluate My Plan" action before final submission (`AcademicAdvisoryAgent.evaluateStudyPlan(studentID)` per SDS Table 69)
- A gap-analysis report listing completed versus remaining courses
- A prioritised list of recommended next courses based on past performance (`provideAcademicGuidance(studentID): Advice`), with the SDS-mandated invariant that the suggestion engine prioritises **mandatory core courses over electives** (Table 68)
- A `RiskStatus ∈ {LOW, MEDIUM, HIGH}` label for the proposed load via `flagRiskLevel(studentID): RiskStatus`
- A link from a HIGH-risk verdict to a mandatory officer review — `approveCourseLoad(studentID): Boolean` confirms the load and, if HIGH risk, escalates to the `CourseManagementOfficer`
- A persisted recommendation record that the officer can see during add/drop review

### Curriculum Compliance Agent will have

- A prerequisite checker (`verifyPrerequisites(studentID, courseID): Boolean`) that returns true only when every required course was passed with grade ≥ F (SDS Table 66)
- A credit-load checker (`validateRegistration(app: Registration): Boolean`) backed by a `validationRules: RuleSet` whose invariant is **max 22 ECTS** per the SDS curriculum constraints (Table 65)
- A payment-status checker (`checkPaymentStatus(studentID): Boolean`) that consults `PayMock.getPaymentStatus`
- A structured block/flag result with a plain-language reason per failure
- An audit-log entry for every evaluation (student, inputs, outcome, timestamp)
- A pluggable curriculum rule set (`curriculumInformation: Map`) synchronised with the **latest Senate-approved department curriculum** (SDS invariant, Table 65)
- A read-only API for officers to "dry run" compliance on a proposed registration
- A prerequisite-bypass override path that, per SRS §3.5 Inverse Requirement and SDS Table 62, **requires the Department Head role** (`role == "Department_Head"`) — not the generic Course Management Officer

### Academic Scheduling Agent will have

- A greedy section-allocation routine that respects section capacity and lab limits
- A time-slot picker that uses university-defined teaching hours
- A room picker that matches required capacity to available rooms
- An instructor picker that respects existing instructor load
- A conflict resolver that swaps rooms or slots when two sections clash
- A produced-schedule artefact that stores every allocation decision
- A human-review escalation when conflicts cannot be resolved automatically
- An audit-log entry for every schedule decision

### Enrollment Adjustment Agent will have

- A window validator that checks the current date against the add/drop deadline
- A credit-load re-validator that runs after every hypothetical add or drop
- A capacity updater that increments or decrements a section's free seats
- A payment cross-checker invoked on every add
- A notification hook that fires on successful resolution
- An audit-log entry for every add/drop decision

### Academic Advisory Agent will have

- A history loader that pulls the student's past grades and current load
- A risk scorer that returns Low, Medium, or High based on CGPA and proposed load
- A recommendation engine that prefers mandatory core courses over electives
- A per-student advice record stored for the officer to review
- An audit-log entry for every advisory evaluation

---

## Track B — Grading Lifecycle

### Implement Grade Entry will have

- An instructor-only view of every section they teach
- A per-section assessment-breakdown editor (e.g., 30% midterm + 70% final) that rejects any total that is not exactly 100
- A roster view that lists every registered student in the section
- A batch-grade entry form that accepts numeric scores and auto-maps to letter grades
- A save-as-draft action so grades can be entered over several sittings
- A submit action that locks the batch and hands it to the validation agent
- A per-batch status timeline (Draft → Submitted → Flagged → Authorised / Rejected)
- A deadline indicator visible from every grading view
- An immutable entry history for every grade value change before submission

### Implement Grade Monitoring will have

- A scheduled scanner that watches every section whose grading window is open
- Automatic reminders to instructors a configurable number of days before the deadline
- An officer-visible dashboard of pending submissions across departments
- An alert raised to the officer when the deadline passes without a submission
- A per-section reminder-history log

### Implement Final Grade Authorization will have

- A generated "review packet" per submission containing grades, class average, and flags
- An authorise action that makes grades official and visible to students
- A reject action that sends the batch back to the instructor with a required reason
- An immutable decision record stamping the officer's identity and timestamp
- A lock that prevents any further edits after authorisation
- An officer-facing queue of pending authorisations sorted by deadline

### Assessment Validation Agent will have

- A range validator that rejects grades outside 0–100 (`detectGradeAnomalies(grades): List<Flag>` per SDS Table 72)
- A distribution analyser that flags statistically unusual batches using `anomalyThreshold: Float`, **default 2.5 standard deviations** (SDS Table 71)
- A class-average calculator (`calculateClassAverage(courseID): Double`) used as the baseline for anomaly detection
- A deadline compliance checker (`validateDeadlineCompliance(courseID): Boolean`)
- A grade-entry monitor (`monitorGradeEntry(courseID)`) that runs only when the academic calendar marks the course as "Grading Phase" (SDS pre-condition)
- A structured flag output with flag type, reason, and severity
- A pluggable anomaly threshold so the cut-off can be tuned by policy
- A grading-policy invariant that aligns with each department's assessment weights (e.g., 30% Mid + 70% Final) and rejects any breakdown whose weights do not sum to **exactly 100** — enforced at `Instructor.updateAssessmentBreakdown(courseID, weight): Boolean` (SDS Table 60)
- An audit-log entry for every validation run

### Grade Monitoring Agent will have

- A periodic task runner that scans open grading windows
- A reminder dispatcher for upcoming deadlines
- An alert dispatcher for missed deadlines
- A configurable reminder schedule (e.g., D-7, D-3, D-1)
- An audit-log entry for every reminder or alert fired

### Grade Authorization Facilitator Agent will have

- A packet builder that aggregates grades, averages, and flags for one review
- An officer-queue populator that keeps the queue sorted and deduplicated
- A state-transition helper that moves a batch from Flagged to Authorised or Rejected
- An instructor-notification hook fired after a reject action
- An audit-log entry for every officer decision facilitated

---

## Track C — Academic Standing, Records, and Exceptions

### Implement Academic Standing will have

- An officer-triggered "Compute Standing" action scoped to a term
- A per-student status record with semester GPA and cumulative CGPA
- An automatic proposal of status (Promoted, Warning, Distinction, Dismissed, Incomplete)
- A held-for-review outcome for students with "I" or "NG" marks rather than a guess
- An officer review page per proposed status with the underlying math visible
- An authorise action that makes the status official and triggers student notification
- An override action that lets the officer change the proposal with a written reason
- An immutable status-history ledger so every past status is reproducible

### Implement Academic Record Generation will have

- A student-facing "Download grade report" action for any completed term
- A student-facing "Download filing slip" action for any registered term
- A digitally signed PDF for every generated document
- A tamper-check endpoint that verifies any PDF against its stored signature
- A per-term archive run that moves transient data into the permanent store
- A document-metadata table recording who generated what and when
- An officer re-issue action for cases where an earlier document was generated before an exception was resolved

### Implement Manual Exception Handling will have

- A single unified exception queue that collects flags from registration, grading, and standing
- A normalised exception record with source, payload, status, and created-at
- An officer resolve action that requires a written justification
- An automatic re-trigger of the originating workflow after resolution
- A per-exception audit trail that captures every touch
- A filter and search on the queue by source, status, and student

### Academic Standing Agent will have

- A GPA calculator (`calculateGPA(studentID, semester): Double`) that weights grades by credit hours (SDS Table 75)
- A CGPA calculator (`calculateCGPA(studentID): Double`) that aggregates every completed term
- A status rule engine (`statusRules: Map<Status, Rule>`) driven by AAU senate legislation, with the SDS-mandated thresholds **Warning at CGPA < 2.0** and **Distinction at CGPA > 3.5** (Table 74)
- A status assignment operation `assignStatus(studentID): AcademicStatus` that returns one of `{PROMOTED, WARNING, DISTINCTION, DISMISSED, INCOMPLETE}` and only sets the official status after the `CourseManagementOfficer` has authorised it
- An edge-case handler (`handleEdgeCase(studentID)`) triggered when a student has "I" (Incomplete) or "NG" (No Grade) marks; it places a hold and alerts the officer rather than guessing
- A notification hook (`notifyStatusChange(studentID, status)`) that fires email/SMS on every authorised status change
- An audit-log entry for every computation

### Academic Records Agent will have

- A PDF-renderer hook (`generateGradeReport(studentID): PDF` and `generateFilingSlip(studentID, semester): PDF`) driven by a `reportTemplate: Template` invariant that follows the official AAU Registrar branding and layout (SDS Table 77)
- A digital-signature generator keyed by `encryptionKey: String` (SDS Table 77) used to sign every generated PDF
- An integrity verifier (`verifyDocumentIntegrity(documentID): Boolean`) that validates any previously signed document
- An archive runner (`archiveSemesterData()`) whose `archiveStoragePath` invariant requires a secure **write-once-read-many (WORM)** volume per SDS Table 77 — runs only after every departmental grade has been authorised by the `CourseManagementOfficer`
- A legacy-SIS sync hook (`syncWithLegacySIS(studentID): Boolean`) that pushes agent-calculated totals to the university's centralised legacy system (SDS Table 78)
- An audit-log entry for every document generated, archived, or verified

### Exception Resolution Agent will have

- A normaliser that converts source-specific flags into a unified shape
- A router that places each normalised flag in the correct officer queue
- A re-trigger hook that restarts the originating workflow after resolution
- A justification enforcer that blocks resolve actions without a written reason
- An audit-log entry for every flag normalised, routed, and resolved

---

## Shared Foundation (Phase 0, joint)

### Implement the Shared Course Catalog will have

- A Course entity (code, title, credit hours, semester, owning department)
- A CourseOffering entity scoped to a term with capacity and section count
- A Section entity with section code, time slot, room, and instructor
- A Prerequisite mapping linking a course to its required predecessor courses
- An AcademicTerm entity with start and end dates plus an open/closed flag
- A `Student` entity with the SDS attributes `studentID` (AAU format `UGR/XXXX/YY`), `fullName`, `currentSemester ∈ [1,12]`, `academicHistory: List<Grade>`, `enrollmentStatus ∈ {ACTIVE, DISMISSED, WITHDRAWN, GRADUATED}` (SDS Table 56) — distinct from `AcademicStatus` (the per-term status)
- An `Instructor` entity with `instructorID` format `STAFF/XXXX/YY`, `department`, `assignedCourses` (SDS Table 59)
- A `CourseManagementOfficer` entity with `staffID` format `REG/XXXX/YY`, `role ∈ {"Registrar_Officer", "Department_Head"}`, and `authorizationLevel ∈ [1,5]` (SDS Table 62) — used to gate prerequisite override (Department_Head only) vs. grade and status authorisation (either)
- A one-shot migration that creates all of the above
- A seed script with fixed UUIDs for 20 courses, 5 sections each, 10 instructors, and 30 students so every track can demo independently

### Implement the Shared CourseBaseAgent will have

- A protected `agentID: String` per instance, a unique UUID or structured string (e.g., `AGENT_CCA_01`) per the SDS BaseAgent invariant (Table 86)
- A protected `status: AgentStatus` whose enum values are exactly `{IDLE, BUSY, WAITING_HUMAN, ERROR}` (SDS Table 86)
- A protected `logAction(action: String): void` that writes to the module audit log
- A protected `processTask(): void` entry point that every concrete agent overrides
- A public `getStatus(): AgentStatus` accessor used by observability dashboards (SDS Table 85)
- A policy-grounding hook backed by **pgvector** to support Retrieval-Augmented Generation over AAU policies, regulations, and SOPs (SRS §3.7 "Vectorized Policy Knowledge Base"); rule-based agents in phase 1 leave this hook as a no-op

### Implement the Shared Enums will have

- `RegistrationStatus` matching SDS Figure 39 lifecycle: `REGISTRATION_OPEN, ADVISOR_REVIEW, CHECKING_PREREQUISITES, CHECKING_PAYMENT, PAYMENT_HOLD, VALIDATION_SUCCESS, REGISTERED, ADD_DROP_WINDOW, CANCELLED`
- `GradeLetter` (A, A-, B+, B, B-, C+, C, C-, D, F, I, NG)
- `AcademicStatusType` (PROMOTED, WARNING, DISTINCTION, DISMISSED, INCOMPLETE)
- `EnrollmentStatus` (ACTIVE, DISMISSED, WITHDRAWN, GRADUATED) — per-student lifecycle, distinct from per-term `AcademicStatusType`
- `ExceptionStatus` (OPEN, IN_REVIEW, RESOLVED, ESCALATED)
- `AgentStatus` (IDLE, BUSY, WAITING_HUMAN, ERROR) — exact casing per SDS Table 86
- `AddDropAction` (ADD, DROP) — per SDS `EnrollmentAdjustmentAgent.updateSectionCapacity` precondition (Table 81)
- `GradeSubmissionStatus` (DRAFT, SUBMITTED, FLAGGED, AUTHORISED, REJECTED)
- `OfficerRole` (REGISTRAR_OFFICER, DEPARTMENT_HEAD) — gates prerequisite-override per SRS §3.5
- `RiskStatus` (LOW, MEDIUM, HIGH) — returned by `AcademicAdvisoryAgent.flagRiskLevel`

---

## Cross-Cutting Guarantees (every feature and agent above will have)

- An entry in the system audit log for every state change and every agent decision
- A role-guarded API so only the intended actor (student, instructor, officer) can reach the endpoint
- Pydantic schemas that enforce input validation at the edge
- Alembic migrations that create every new table
- Seed data that lets the feature be demoed in isolation
- At least one unit test per rule and one integration test per happy-path journey
- Route registration under `/api/v1/courses/...` so the module appears in the generated OpenAPI docs

---

## SRS Functional-Requirement Coverage

- Track A covers Course-FR-01, FR-02, FR-03, FR-04, FR-05, FR-06
- Track B covers Course-FR-07, FR-08, FR-09
- Track C covers Course-FR-10, FR-11, FR-12, FR-13

Every Course-FR is covered exactly once by exactly one track, so ownership is unambiguous.
