"""
Course Management — consolidated seed script.

Merges four previously separate scripts into one idempotent runner:

  1. seed_course.py
       Phase 0 catalog (terms, courses, prerequisites, classrooms,
       instructors + assignments, students, officers) plus Track A
       sample registrations, the SE-sem1/upper-year bulk cohorts,
       grade backfill, and the Track C standing demo cohort.

  2. migrate_terms_to_ethiopian_phases.py
       Idempotent backfill of the four Ethiopian-phase AcademicTerm
       rows — folded into _seed_terms() since the seed already covers
       the same set of terms. The final "term state" summary print
       at the end of this script comes from here.

  3. seed_extra_se_sem1_courses.py
       Six extra Software-Engineering / semester-1 catalog rows
       (SE105–SE110) used to give the "add course" flow more
       options. Run after _seed_courses so the prerequisite seeder
       sees them.

  4. seed_track_b_roster.py
       Self-contained Track B PR-1 demo scenario: a separate term
       ("Track-B-Demo-2026"), one instructor teaching CS101 across
       two sections, with the ADD/DROP/draft edge cases the roster
       endpoints need to exercise.

Idempotent end-to-end: running twice does not create duplicates.

Usage:
    cd /path/to/Agentic-Registrar-Backend
    source venv/bin/activate
    python scripts/seed_course_management.py
"""

import asyncio
import uuid
from datetime import date, time, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, AdvisoryRecommendation, ClassScheduleSlot, Classroom,
    Course, CoursePrerequisite, CourseManagementOfficer, Grade, Instructor,
    InstructorAssignment, Registration, RegistrationCourse,
    RegistrationStatusHistory, Section, Student, StudentScheduleAddition,
)
from app.modules.course.grade_points import points_for
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeLetter, GradeSubmissionStatus,
    OfficerRole, RegistrationStatus, RiskStatus, SponsorshipType, UserRole,
)


DATABASE_URL = str(settings.DATABASE_URL)


# ══════════════════════════════════════════════════════════════
#  Deterministic UUIDs
# ══════════════════════════════════════════════════════════════
# Two stable namespaces — Phase 0 + Track A reuse one, Track B uses
# its own so the demo scenario can't accidentally collide with the
# bulk cohort ids.

_NS = uuid.UUID("c0a3e000-0000-4000-8000-000000000000")     # phase 0
_NS_TB = uuid.UUID("b0a3e000-0000-4000-8000-000000000001")  # track b pr 1


def _uid(*parts: str) -> uuid.UUID:
    """Stable UUID in the Phase 0 namespace."""
    return uuid.uuid5(_NS, "/".join(parts))


def _uid_tb(*parts: str) -> uuid.UUID:
    """Stable UUID in the Track B namespace."""
    return uuid.uuid5(_NS_TB, "/".join(parts))


# ══════════════════════════════════════════════════════════════
#  Academic Terms (Ethiopian phases)
# ══════════════════════════════════════════════════════════════
# Every academic year is split into two phases:
#   Phase One: September → end of January  (≈ Meskerem → Tir)
#   Phase Two: February  → end of June     (≈ Yekatit  → Sene)
#
# Whichever phase covers "today" is seeded with ``is_open=True`` so the
# registration portal is testable out of the box. The other three stay
# closed and officers can flip the windows freely.
TERMS = [
    {
        "id": _uid("term", "2025-2026-phase-1"),
        "term_name": "2025/2026",
        "phase": AcademicPhase.ONE,
        "start_date": date(2025, 9, 1),
        "end_date": date(2026, 1, 31),
        "is_open": False,
        "description": "Phase One of the 2025/2026 academic year (Sep–Jan).",
    },
    {
        "id": _uid("term", "2025-2026-phase-2"),
        "term_name": "2025/2026",
        "phase": AcademicPhase.TWO,
        "start_date": date(2026, 2, 1),
        "end_date": date(2026, 6, 30),
        "is_open": True,
        "description": "Phase Two of the 2025/2026 academic year (Feb–Jun).",
    },
    {
        "id": _uid("term", "2026-2027-phase-1"),
        "term_name": "2026/2027",
        "phase": AcademicPhase.ONE,
        "start_date": date(2026, 9, 1),
        "end_date": date(2027, 1, 31),
        "is_open": False,
        "description": "Phase One of the 2026/2027 academic year (Sep–Jan).",
    },
    {
        "id": _uid("term", "2026-2027-phase-2"),
        "term_name": "2026/2027",
        "phase": AcademicPhase.TWO,
        "start_date": date(2027, 2, 1),
        "end_date": date(2027, 6, 30),
        "is_open": False,
        "description": "Phase Two of the 2026/2027 academic year (Feb–Jun).",
    },
]


# ══════════════════════════════════════════════════════════════
#  Departments + curriculum
# ══════════════════════════════════════════════════════════════
# Six engineering departments, 5-year programs (10 semesters each),
# 4 courses per (department, semester) cell. 6 × 10 × 4 = 240 courses.

DEPARTMENTS = [
    "Software Engineering",
    "Electrical Engineering",
    "Chemical Engineering",
    "Civil Engineering",
    "Mechanical Engineering",
    "Bio Medical Engineering",
]

DEPT_CODE = {
    "Software Engineering":   "SE",
    "Electrical Engineering": "EE",
    "Chemical Engineering":   "ChE",
    "Civil Engineering":      "CE",
    "Mechanical Engineering": "ME",
    "Bio Medical Engineering": "BME",
}


CURRICULUM: dict[str, dict[int, list[tuple[str, int]]]] = {
    "Software Engineering": {
        1:  [("Calculus I", 4), ("Programming Fundamentals", 4), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Physics for Engineers", 4), ("Discrete Mathematics", 3), ("SE Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Data Structures", 4), ("Object-Oriented Programming", 4)],
        4:  [("Probability & Statistics", 3), ("Algorithms", 4), ("Database Systems", 4), ("Web Programming", 3)],
        5:  [("Operating Systems", 4), ("Computer Networks", 3), ("Software Engineering Principles", 3), ("Requirements Engineering", 3)],
        6:  [("Software Architecture", 3), ("Software Testing", 3), ("Human-Computer Interaction", 3), ("Software Project Management", 3)],
        7:  [("Distributed Systems", 3), ("DevOps & Continuous Delivery", 3), ("Software Security", 3), ("Software Quality Assurance", 3)],
        8:  [("Mobile Application Development", 3), ("Cloud Computing", 3), ("Agile Methods", 3), ("Software Maintenance", 3)],
        9:  [("Industrial Internship", 4), ("Engineering Economics", 3), ("Enterprise Software", 3), ("Software Verification", 3)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in SE", 3)],
    },
    "Electrical Engineering": {
        1:  [("Calculus I", 4), ("Programming for EE", 3), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Physics for EE", 4), ("Discrete Mathematics", 3), ("Electrical Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Circuit Theory I", 4), ("Electronics I", 4)],
        4:  [("Probability & Statistics", 3), ("Circuit Theory II", 4), ("Electronics II", 4), ("Digital Logic Design", 3)],
        5:  [("Signals & Systems", 4), ("Electromagnetics I", 3), ("Microprocessors", 3), ("Power Systems I", 3)],
        6:  [("Control Systems", 4), ("Electromagnetics II", 3), ("Communication Systems", 3), ("Power Electronics", 3)],
        7:  [("Power Systems II", 4), ("VLSI Design", 3), ("Embedded Systems", 3), ("Engineering Economics", 3)],
        8:  [("Renewable Energy Systems", 3), ("Industrial Electronics", 3), ("Wireless Communication", 3), ("Project Proposal", 2)],
        9:  [("Industrial Internship", 4), ("Power System Protection", 3), ("Smart Grid Technology", 3), ("Industrial Control", 3)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in EE", 3)],
    },
    "Chemical Engineering": {
        1:  [("Calculus I", 4), ("Chemistry I", 4), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Chemistry II", 4), ("Discrete Mathematics", 3), ("Chemical Engineering Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Material Balance", 4), ("Engineering Chemistry", 3)],
        4:  [("Probability & Statistics", 3), ("Energy Balance", 3), ("Thermodynamics I", 4), ("Fluid Mechanics", 3)],
        5:  [("Heat Transfer", 4), ("Mass Transfer", 3), ("Reaction Engineering I", 3), ("Process Equipment Design", 3)],
        6:  [("Reaction Engineering II", 3), ("Process Control", 3), ("Unit Operations I", 4), ("Chemical Reactor Design", 3)],
        7:  [("Unit Operations II", 4), ("Process Simulation", 3), ("Petroleum Engineering", 3), ("Engineering Economics", 3)],
        8:  [("Plant Design I", 4), ("Industrial Chemistry", 3), ("Environmental Engineering", 3), ("Biochemical Engineering", 3)],
        9:  [("Plant Design II", 4), ("Process Safety", 3), ("Optimization in ChE", 3), ("Industrial Internship", 4)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in ChE", 3)],
    },
    "Civil Engineering": {
        1:  [("Calculus I", 4), ("Programming for CE", 3), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Physics for CE", 4), ("Discrete Mathematics", 3), ("Civil Engineering Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Surveying I", 4), ("Engineering Geology", 3)],
        4:  [("Probability & Statistics", 3), ("Surveying II", 3), ("Strength of Materials", 4), ("Construction Materials", 3)],
        5:  [("Theory of Structures I", 4), ("Soil Mechanics I", 3), ("Hydraulics", 3), ("Transportation Engineering I", 3)],
        6:  [("Theory of Structures II", 4), ("Soil Mechanics II", 3), ("Hydrology", 3), ("Transportation Engineering II", 3)],
        7:  [("Reinforced Concrete I", 4), ("Foundation Engineering", 3), ("Water Supply Engineering", 3), ("Engineering Economics", 3)],
        8:  [("Reinforced Concrete II", 4), ("Steel Structures", 4), ("Wastewater Engineering", 3), ("Highway Engineering", 3)],
        9:  [("Construction Management", 3), ("Bridge Engineering", 3), ("Earthquake Engineering", 3), ("Industrial Internship", 4)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in CE", 3)],
    },
    "Mechanical Engineering": {
        1:  [("Calculus I", 4), ("Programming for ME", 3), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Physics for ME", 4), ("Discrete Mathematics", 3), ("Mechanical Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Strength of Materials I", 4), ("Manufacturing Processes I", 3)],
        4:  [("Probability & Statistics", 3), ("Strength of Materials II", 3), ("Thermodynamics I", 4), ("Manufacturing Processes II", 3)],
        5:  [("Fluid Mechanics", 4), ("Thermodynamics II", 3), ("Machine Design I", 3), ("Mechanics of Machinery", 3)],
        6:  [("Heat Transfer", 4), ("Machine Design II", 3), ("Internal Combustion Engines", 3), ("Material Science", 3)],
        7:  [("Mechanical Vibrations", 3), ("Industrial Engineering", 3), ("Refrigeration & Air Conditioning", 3), ("Engineering Economics", 3)],
        8:  [("Production Engineering", 3), ("Power Plant Engineering", 3), ("Automotive Engineering", 3), ("Robotics", 3)],
        9:  [("Mechatronics", 3), ("CAD/CAM", 3), ("Renewable Energy Systems", 3), ("Industrial Internship", 4)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in ME", 3)],
    },
    "Bio Medical Engineering": {
        1:  [("Calculus I", 4), ("Biology I", 3), ("Engineering Drawing", 3), ("Engineering Mechanics", 3)],
        2:  [("Calculus II", 4), ("Physics for BME", 4), ("Discrete Mathematics", 3), ("Biomedical Workshop", 2)],
        3:  [("Linear Algebra", 3), ("Differential Equations", 3), ("Human Anatomy & Physiology I", 4), ("Biochemistry", 3)],
        4:  [("Probability & Statistics", 3), ("Human Anatomy & Physiology II", 4), ("Biomechanics", 3), ("Biomaterials", 3)],
        5:  [("Medical Instrumentation I", 4), ("Bio Signals & Systems", 3), ("Medical Imaging I", 3), ("Biomedical Sensors", 3)],
        6:  [("Medical Instrumentation II", 4), ("Medical Imaging II", 3), ("Tissue Engineering", 3), ("Clinical Engineering", 3)],
        7:  [("Medical Devices Design", 3), ("Biomechanical Engineering", 3), ("Engineering Economics", 3), ("Hospital Engineering", 3)],
        8:  [("Rehabilitation Engineering", 3), ("Biomedical Materials", 3), ("Telemedicine", 3), ("Medical Robotics", 3)],
        9:  [("Genetic Engineering", 3), ("Bioinformatics", 3), ("Industrial Internship", 4), ("Regulations & Standards", 3)],
        10: [("Final Year Project I", 4), ("Final Year Project II", 4), ("Engineering Ethics", 2), ("Special Topics in BME", 3)],
    },
}


def _course_code(department: str, semester: int, slot: int) -> str:
    """``SE101`` for sem 1 slot 1, ``EE1004`` for sem 10 slot 4."""
    return f"{DEPT_CODE[department]}{semester}{slot:02d}"


COURSES = [
    (
        _course_code(department, semester, slot),
        title,
        credits,
        semester,
        department,
    )
    for department in DEPARTMENTS
    for semester, slots in CURRICULUM[department].items()
    for slot, (title, credits) in enumerate(slots, start=1)
]


# Six additional SE / semester-1 catalog rows (was: seed_extra_se_sem1_courses.py).
# Codes use slots 05-10 so they never collide with the base seed
# (SE101-SE104). All rows are department="Software Engineering",
# semester=1; the "add course" flow uses these to widen its picker.
EXTRA_SE_SEM1 = [
    ("SE105", "Introduction to Computing",    3),
    ("SE106", "Communicative English Skills", 3),
    ("SE107", "Civics & Ethical Education",   2),
    ("SE108", "Logic & Critical Thinking",    2),
    ("SE109", "Inclusiveness",                1),
    ("SE110", "General Psychology",           3),
]


# ══════════════════════════════════════════════════════════════
#  Prerequisites (intra-department only)
# ══════════════════════════════════════════════════════════════

PREREQUISITES = [
    # ── Software Engineering ────────────────────────────────────
    ("SE201", "SE101"),
    ("SE303", "SE102"),
    ("SE402", "SE303"),
    ("SE403", "SE303"),
    ("SE601", "SE503"),

    # ── Electrical Engineering ──────────────────────────────────
    ("EE201", "EE101"),
    ("EE402", "EE303"),
    ("EE403", "EE304"),
    ("EE503", "EE404"),
    ("EE602", "EE502"),

    # ── Chemical Engineering ────────────────────────────────────
    ("ChE201", "ChE101"),
    ("ChE403", "ChE201"),
    ("ChE503", "ChE403"),
    ("ChE603", "ChE503"),

    # ── Civil Engineering ───────────────────────────────────────
    ("CE201", "CE101"),
    ("CE402", "CE303"),
    ("CE501", "CE403"),
    ("CE601", "CE501"),
    ("CE502", "CE304"),

    # ── Mechanical Engineering ──────────────────────────────────
    ("ME201", "ME101"),
    ("ME402", "ME303"),
    ("ME403", "ME201"),
    ("ME502", "ME403"),
    ("ME601", "ME501"),

    # ── Bio Medical Engineering ─────────────────────────────────
    ("BME201", "BME101"),
    ("BME402", "BME303"),
    ("BME501", "BME402"),
    ("BME601", "BME501"),
]


# ══════════════════════════════════════════════════════════════
#  Instructors (12 across 6 departments — 2 per department)
# ══════════════════════════════════════════════════════════════

INSTRUCTORS = [
    ("STAFF/0001/10", "Alemayehu", "Bekele",   "Software Engineering"),
    ("STAFF/0002/10", "Bethel",    "Tadesse",  "Software Engineering"),
    ("STAFF/0003/10", "Chala",     "Mekonnen", "Electrical Engineering"),
    ("STAFF/0004/10", "Dawit",     "Girma",    "Electrical Engineering"),
    ("STAFF/0005/10", "Eyerusalem","Kassa",    "Chemical Engineering"),
    ("STAFF/0006/10", "Feven",     "Asfaw",    "Chemical Engineering"),
    ("STAFF/0007/10", "Getachew",  "Lemma",    "Civil Engineering"),
    ("STAFF/0008/10", "Hanna",     "Negussie", "Civil Engineering"),
    ("STAFF/0009/10", "Iskinder",  "Worku",    "Mechanical Engineering"),
    ("STAFF/0010/10", "Jemal",     "Hussein",  "Mechanical Engineering"),
    ("STAFF/0011/10", "Kalkidan",  "Demeke",   "Bio Medical Engineering"),
    ("STAFF/0012/10", "Lensa",     "Hailu",    "Bio Medical Engineering"),
]


# ══════════════════════════════════════════════════════════════
#  Classrooms (per-department physical room inventory)
# ══════════════════════════════════════════════════════════════

CLASSROOMS = [
    ("SE-101",   80, "Software Engineering"),
    ("SE-201",   60, "Software Engineering"),
    ("SE-LAB-1", 30, "Software Engineering"),

    ("EE-101",   60, "Electrical Engineering"),
    ("EE-201",   50, "Electrical Engineering"),
    ("EE-LAB-1", 30, "Electrical Engineering"),

    ("ChE-101",   60, "Chemical Engineering"),
    ("ChE-201",   50, "Chemical Engineering"),
    ("ChE-LAB-1", 30, "Chemical Engineering"),

    ("CE-101",   60, "Civil Engineering"),
    ("CE-201",   50, "Civil Engineering"),
    ("CE-LAB-1", 30, "Civil Engineering"),

    ("ME-101",   60, "Mechanical Engineering"),
    ("ME-201",   50, "Mechanical Engineering"),
    ("ME-LAB-1", 30, "Mechanical Engineering"),

    ("BME-101",   60, "Bio Medical Engineering"),
    ("BME-201",   50, "Bio Medical Engineering"),
    ("BME-LAB-1", 30, "Bio Medical Engineering"),
]


# ══════════════════════════════════════════════════════════════
#  Students (30 across semesters 1–8, distributed across 6 depts)
# ══════════════════════════════════════════════════════════════

_GOV = SponsorshipType.GOVERNMENT
_SELF = SponsorshipType.SELF_SPONSORED

STUDENTS = [
    ("UGR/0001/14", "Abel",      "Tesfaye",     1, "Software Engineering",   _GOV),
    ("UGR/0002/14", "Beza",      "Worku",       2, "Software Engineering",   _SELF),
    ("UGR/0003/14", "Caleb",     "Mulugeta",    3, "Software Engineering",   _GOV),
    ("UGR/0004/14", "Dina",      "Hailemariam", 5, "Software Engineering",   _GOV),
    ("UGR/0005/14", "Ermias",    "Bekele",      7, "Software Engineering",   _SELF),

    ("UGR/0006/14", "Frehiwot",  "Asrat",       1, "Electrical Engineering", _GOV),
    ("UGR/0007/14", "Gemechu",   "Olana",       2, "Electrical Engineering", _GOV),
    ("UGR/0008/14", "Helen",     "Yohannes",    4, "Electrical Engineering", _SELF),
    ("UGR/0009/14", "Isaac",     "Demeke",      6, "Electrical Engineering", _GOV),
    ("UGR/0010/14", "Jerusalem", "Tilahun",     8, "Electrical Engineering", _GOV),

    ("UGR/0011/14", "Kalkidan",  "Sisay",       1, "Chemical Engineering",   _GOV),
    ("UGR/0012/14", "Lidya",     "Abebe",       3, "Chemical Engineering",   _SELF),
    ("UGR/0013/14", "Marcos",    "Negash",      4, "Chemical Engineering",   _GOV),
    ("UGR/0014/14", "Nardos",    "Birhanu",     5, "Chemical Engineering",   _GOV),
    ("UGR/0015/14", "Obse",      "Tariku",      7, "Chemical Engineering",   _GOV),

    ("UGR/0016/14", "Petros",    "Selam",       2, "Civil Engineering",      _GOV),
    ("UGR/0017/14", "Rahel",     "Yilma",       3, "Civil Engineering",      _SELF),
    ("UGR/0018/14", "Samuel",    "Habte",       5, "Civil Engineering",      _GOV),
    ("UGR/0019/14", "Tigist",    "Mekuria",     6, "Civil Engineering",      _GOV),
    ("UGR/0020/14", "Ujulu",     "Gobena",      8, "Civil Engineering",      _SELF),

    ("UGR/0021/14", "Veronica",  "Eshete",      1, "Mechanical Engineering", _GOV),
    ("UGR/0022/14", "Wondwossen","Aklilu",      2, "Mechanical Engineering", _GOV),
    ("UGR/0023/14", "Xavier",    "Birru",       4, "Mechanical Engineering", _SELF),
    ("UGR/0024/14", "Yared",     "Lemessa",     6, "Mechanical Engineering", _GOV),
    ("UGR/0025/14", "Zewditu",   "Asfaw",       7, "Mechanical Engineering", _GOV),

    ("UGR/0026/14", "Amanuel",   "Getaneh",     1, "Bio Medical Engineering", _GOV),
    ("UGR/0027/14", "Bisrat",    "Kebede",      3, "Bio Medical Engineering", _SELF),
    ("UGR/0028/14", "Christian", "Wolde",       5, "Bio Medical Engineering", _GOV),
    ("UGR/0029/14", "Daniel",    "Tamirat",     6, "Bio Medical Engineering", _GOV),
    ("UGR/0030/14", "Eleni",     "Berhanu",     8, "Bio Medical Engineering", _GOV),
]


# ══════════════════════════════════════════════════════════════
#  Officers
# ══════════════════════════════════════════════════════════════

OFFICERS = [
    (
        "REG/9001/10", "Tewodros", "Adane",
        OfficerRole.REGISTRAR_OFFICER, 5,
        "registrar.officer@aau.edu.et",
    ),
    (
        "REG/9002/10", "Selamawit", "Mengistu",
        OfficerRole.DEPARTMENT_HEAD, 3,
        "se.dept.head@aau.edu.et",
    ),
]


# ══════════════════════════════════════════════════════════════
#  Track A — sample registrations
# ══════════════════════════════════════════════════════════════

SAMPLE_REGISTRATIONS = [
    {
        "student_id": "UGR/0001/14",
        "course_codes": ["SE101", "SE102"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.SELF_SPONSORED,
        "payment_reference": "MOCK-PAID-FALL2026-UGR0001",
        "advisory": None,
    },
    {
        "student_id": "UGR/0007/14",
        "course_codes": ["EE201"],
        "status": RegistrationStatus.REGISTRATION_OPEN,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0019/14",
        "course_codes": ["CE601"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.SELF_SPONSORED,
        "payment_reference": "MOCK-PAID-FALL2026-UGR0019",
        "advisory": "LOW",
    },
    # First-semester cohort: every sem-1 student fully registered in
    # their department's 4-course semester-1 curriculum so the
    # AcademicSchedulingAgent has one (department, semester=1) cohort
    # per department to allocate.
    {
        "student_id": "UGR/0006/14",
        "course_codes": ["EE101", "EE102", "EE103", "EE104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0011/14",
        "course_codes": ["ChE101", "ChE102", "ChE103", "ChE104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0021/14",
        "course_codes": ["ME101", "ME102", "ME103", "ME104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0026/14",
        "course_codes": ["BME101", "BME102", "BME103", "BME104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
]


# ══════════════════════════════════════════════════════════════
#  Bulk SE-sem-1 cohort (70 fresh students, REGISTERED in open term)
# ══════════════════════════════════════════════════════════════

SE_SEM1_BULK_COUNT = 70
SE_SEM1_BULK_START_SEQ = 100      # → UGR/0100/14 .. UGR/0169/14
SE_SEM1_BULK_COURSES = ["SE101", "SE102", "SE103", "SE104"]


# ══════════════════════════════════════════════════════════════
#  Bulk SE upper-year cohorts (years 2–5, 60 students each)
# ══════════════════════════════════════════════════════════════

# (batch_year, target_semester, count)
SE_BULK_UPPER_COHORTS: list[tuple[int, int, int]] = [
    (13, 3, 60),   # Year 2
    (12, 5, 60),   # Year 3
    (11, 7, 60),   # Year 4
    (10, 9, 60),   # Year 5
]


# ══════════════════════════════════════════════════════════════
#  Phase 0 seed functions
# ══════════════════════════════════════════════════════════════


async def _seed_terms(session: AsyncSession) -> list[AcademicTerm]:
    """
    Seed both phases of each academic year. Idempotent per term: an
    existing row (matched by ``(term_name, phase)``) is reused
    unchanged.
    """
    seeded: list[AcademicTerm] = []
    for spec in TERMS:
        existing = (
            await session.execute(
                select(AcademicTerm).where(
                    AcademicTerm.term_name == spec["term_name"],
                    AcademicTerm.phase == spec["phase"],
                )
            )
        ).scalar_one_or_none()
        if existing:
            print(
                f"⚠️  Academic term '{spec['term_name']}' "
                f"(phase {spec['phase'].value}) already exists — skipping."
            )
            seeded.append(existing)
            continue

        term = AcademicTerm(**spec)
        session.add(term)
        await session.commit()
        print(f"✅ Seeded academic term: {term.term_name} (open={term.is_open}).")
        seeded.append(term)
    return seeded


async def _seed_courses(session: AsyncSession) -> dict[str, Course]:
    by_code: dict[str, Course] = {}
    rows = (await session.execute(select(Course))).scalars().all()
    by_code.update({c.code: c for c in rows})

    new_count = 0
    for code, title, credit_hours, semester, department in COURSES:
        if code in by_code:
            continue
        course = Course(
            id=_uid("course", code),
            code=code,
            title=title,
            credit_hours=credit_hours,
            semester=semester,
            department=department,
        )
        session.add(course)
        by_code[code] = course
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} courses (catalog total: {len(by_code)}).")
    else:
        print(f"⚠️  All {len(by_code)} courses already present — skipping.")
    return by_code


async def _seed_extra_se_sem1_courses(
    session: AsyncSession,
    courses_by_code: dict[str, Course],
) -> None:
    """
    Add the six extra Software-Engineering / semester-1 catalog rows
    (SE105-SE110). Idempotent: re-runs skip existing codes.
    """
    new_count = 0
    for code, title, credits in EXTRA_SE_SEM1:
        if code in courses_by_code:
            continue
        course = Course(
            id=_uid("course", code),
            code=code,
            title=title,
            credit_hours=credits,
            semester=1,
            department="Software Engineering",
        )
        session.add(course)
        courses_by_code[code] = course
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} extra SE-sem-1 courses (SE105-SE110).")
    else:
        print("⚠️  Extra SE-sem-1 courses already present — skipping.")


async def _seed_prerequisites(
    session: AsyncSession,
    courses_by_code: dict[str, Course],
) -> None:
    existing = (
        await session.execute(select(CoursePrerequisite))
    ).scalars().all()
    existing_pairs = {(p.course_id, p.prerequisite_course_id) for p in existing}

    new_count = 0
    for course_code, prereq_code in PREREQUISITES:
        course = courses_by_code[course_code]
        prereq = courses_by_code[prereq_code]
        pair = (course.id, prereq.id)
        if pair in existing_pairs:
            continue
        session.add(
            CoursePrerequisite(
                id=_uid("prereq", course_code, prereq_code),
                course_id=course.id,
                prerequisite_course_id=prereq.id,
            )
        )
        new_count += 1

    await session.commit()
    if new_count:
        print(
            f"✅ Seeded {new_count} prerequisite edges "
            f"(catalog total: {len(existing) + new_count})."
        )
    else:
        print(
            f"⚠️  All {len(existing)} prerequisite edges already present "
            "— skipping."
        )


async def _ensure_user(
    session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str,
    role: UserRole,
    user_uid: uuid.UUID,
) -> User:
    """Idempotent helper: get-or-create a User row for a seeded actor."""
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        return existing
    user = User(
        id=user_uid,
        email=email,
        first_name=first_name,
        last_name=last_name,
        hashed_password=hash_password("password123"),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_classrooms(session: AsyncSession) -> dict[str, Classroom]:
    """
    Seed the per-department classroom inventory. Idempotent: any
    classroom whose ``name`` already exists is reused unchanged.
    """
    rows = (await session.execute(select(Classroom))).scalars().all()
    by_name: dict[str, Classroom] = {c.name: c for c in rows}

    new_count = 0
    for name, capacity, department in CLASSROOMS:
        if name in by_name:
            continue
        room = Classroom(
            id=_uid("classroom", name),
            name=name,
            capacity=capacity,
            department=department,
        )
        session.add(room)
        by_name[name] = room
        new_count += 1

    await session.commit()
    if new_count:
        print(
            f"✅ Seeded {new_count} classrooms "
            f"(catalog total: {len(by_name)})."
        )
    else:
        print(f"⚠️  All {len(by_name)} classrooms already present — skipping.")
    return by_name


async def _seed_instructors(
    session: AsyncSession,
) -> dict[str, Instructor]:
    by_staff_id: dict[str, Instructor] = {}
    rows = (await session.execute(select(Instructor))).scalars().all()
    by_staff_id.update({i.instructor_id: i for i in rows})

    new_count = 0
    for staff_id, first_name, last_name, department in INSTRUCTORS:
        if staff_id in by_staff_id:
            continue
        slug = staff_id.lower().replace("/", "-")
        user = await _ensure_user(
            session,
            email=f"{slug}@aau.edu.et",
            first_name=first_name,
            last_name=last_name,
            role=UserRole.INSTRUCTOR,
            user_uid=_uid("user", "instructor", staff_id),
        )
        instructor = Instructor(
            id=_uid("instructor", staff_id),
            user_id=user.id,
            instructor_id=staff_id,
            department=department,
        )
        session.add(instructor)
        by_staff_id[staff_id] = instructor
        new_count += 1

    await session.commit()
    if new_count:
        print(
            f"✅ Seeded {new_count} instructors "
            f"(catalog total: {len(by_staff_id)})."
        )
    else:
        print(
            f"⚠️  All {len(by_staff_id)} instructors already present "
            "— skipping."
        )
    return by_staff_id


async def _seed_instructor_assignments(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
    instructors_by_staff_id: dict[str, Instructor],
) -> None:
    """
    Round-robin assign instructors to courses within each department.
    Cohort Section rows are emitted on demand by
    AcademicSchedulingAgent — not seeded here.
    """
    existing_for_term = (
        await session.execute(
            select(InstructorAssignment).where(
                InstructorAssignment.term_id == term.id,
            )
        )
    ).scalars().all()
    for row in existing_for_term:
        await session.delete(row)
    if existing_for_term:
        await session.flush()

    instructors_by_dept: dict[str, list[Instructor]] = {}
    for ins in instructors_by_staff_id.values():
        instructors_by_dept.setdefault(ins.department, []).append(ins)
    for ins_list in instructors_by_dept.values():
        ins_list.sort(key=lambda i: i.instructor_id)

    courses_by_dept: dict[str, list[Course]] = {}
    for code, course in courses_by_code.items():
        courses_by_dept.setdefault(course.department, []).append(course)
    for course_list in courses_by_dept.values():
        course_list.sort(key=lambda c: c.code)

    assignment_count = 0
    for dept, dept_courses in courses_by_dept.items():
        dept_instructors = instructors_by_dept.get(dept, [])
        if not dept_instructors:
            continue
        for idx, course in enumerate(dept_courses):
            instructor = dept_instructors[idx % len(dept_instructors)]
            existing_assn = (
                await session.execute(
                    select(InstructorAssignment).where(
                        InstructorAssignment.instructor_id == instructor.id,
                        InstructorAssignment.course_id == course.id,
                        InstructorAssignment.term_id == term.id,
                    )
                )
            ).scalar_one_or_none()
            if existing_assn is None:
                session.add(
                    InstructorAssignment(
                        id=_uid(
                            "assn", term.term_name, term.phase.value,
                            instructor.instructor_id, course.code,
                        ),
                        instructor_id=instructor.id,
                        course_id=course.id,
                        term_id=term.id,
                    )
                )
                assignment_count += 1

    await session.commit()
    if assignment_count:
        print(
            f"✅ Seeded {assignment_count} instructor assignments "
            f"for term '{term.term_name}'."
        )
    else:
        print(
            f"⚠️  Instructor assignments for term '{term.term_name}' "
            "already present — skipping."
        )


async def _seed_students(session: AsyncSession) -> None:
    existing = (await session.execute(select(Student))).scalars().all()
    by_student_id = {s.student_id: s for s in existing}

    new_count = 0
    reconciled = 0
    for student_id, first_name, last_name, semester, department, sponsorship in STUDENTS:
        if student_id in by_student_id:
            student = by_student_id[student_id]
            changed = False
            if student.department != department:
                student.department = department
                changed = True
            if student.sponsorship_type != sponsorship:
                student.sponsorship_type = sponsorship
                changed = True
            if changed:
                reconciled += 1
            continue
        slug = student_id.lower().replace("/", "-")
        user = await _ensure_user(
            session,
            email=f"{slug}@aau.edu.et",
            first_name=first_name,
            last_name=last_name,
            role=UserRole.STUDENT,
            user_uid=_uid("user", "student", student_id),
        )
        session.add(
            Student(
                id=_uid("student", student_id),
                user_id=user.id,
                student_id=student_id,
                full_name=f"{first_name} {last_name}",
                current_semester=semester,
                department=department,
                sponsorship_type=sponsorship,
                enrollment_status=EnrollmentStatus.ACTIVE,
            )
        )
        new_count += 1

    await session.commit()
    if new_count:
        print(
            f"✅ Seeded {new_count} students "
            f"(catalog total: {len(existing) + new_count})."
        )
    elif reconciled:
        print(
            f"↻  Reconciled department/sponsorship on {reconciled} "
            "existing students."
        )
    else:
        print(f"⚠️  All {len(existing)} students already present — skipping.")


async def _seed_officers(session: AsyncSession) -> None:
    existing = (
        await session.execute(select(CourseManagementOfficer))
    ).scalars().all()
    by_staff = {o.staff_id: o for o in existing}

    new_count = 0
    for staff_id, first_name, last_name, role, level, email in OFFICERS:
        if staff_id in by_staff:
            continue
        user = await _ensure_user(
            session,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.REGISTRAR_OFFICER,
            user_uid=_uid("user", "officer", staff_id),
        )
        session.add(
            CourseManagementOfficer(
                id=_uid("officer", staff_id),
                user_id=user.id,
                staff_id=staff_id,
                role=role,
                authorization_level=level,
            )
        )
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} course-management officers.")
    else:
        print(f"⚠️  All {len(existing)} officers already present — skipping.")


async def _seed_registrations(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
) -> None:
    """Seed sample registrations + their status-history seed rows."""
    students = (await session.execute(select(Student))).scalars().all()
    by_student_id = {s.student_id: s for s in students}

    new_regs = 0
    new_links = 0
    new_history = 0
    for sample in SAMPLE_REGISTRATIONS:
        student = by_student_id.get(sample["student_id"])
        if student is None:
            continue
        existing = (
            await session.execute(
                select(Registration).where(
                    Registration.student_id == student.id,
                    Registration.term_id == term.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        reg = Registration(
            id=_uid(
                "registration", term.term_name, term.phase.value,
                sample["student_id"],
            ),
            student_id=student.id,
            term_id=term.id,
            status=sample["status"],
            sponsorship_type=sample["sponsorship"],
            payment_reference=sample["payment_reference"],
        )
        session.add(reg)
        await session.flush()
        new_regs += 1

        session.add(
            RegistrationStatusHistory(
                id=_uid("reghist", "init", sample["student_id"]),
                registration_id=reg.id,
                previous_status=None,
                new_status=RegistrationStatus.REGISTRATION_OPEN,
                trigger_reason="seeded draft",
            )
        )
        new_history += 1
        if sample["status"] == RegistrationStatus.REGISTERED:
            session.add(
                RegistrationStatusHistory(
                    id=_uid("reghist", "registered", sample["student_id"]),
                    registration_id=reg.id,
                    previous_status=RegistrationStatus.REGISTRATION_OPEN,
                    new_status=RegistrationStatus.REGISTERED,
                    agent_id="SEED_SCRIPT",
                    trigger_reason="seeded as already finalised",
                )
            )
            new_history += 1

        for code in sample["course_codes"]:
            course = courses_by_code.get(code)
            if course is None:
                continue
            session.add(
                RegistrationCourse(
                    id=_uid("regcourse", sample["student_id"], code),
                    registration_id=reg.id,
                    course_id=course.id,
                )
            )
            new_links += 1

        if sample["advisory"] == "LOW":
            session.add(
                AdvisoryRecommendation(
                    id=_uid("advisory", "low", sample["student_id"]),
                    student_id=student.id,
                    term_id=term.id,
                    risk_status=RiskStatus.LOW,
                    risk_explanation=(
                        "Seeded LOW-risk baseline: CGPA 3.2 with light "
                        "single-course load."
                    ),
                    proposed_courses=[
                        str(courses_by_code[c].id)
                        for c in sample["course_codes"]
                    ],
                    recommended_courses=[],
                    gap_analysis={
                        "department": student.department or "Unknown",
                        "current_semester": student.current_semester,
                        "completed_count": 0,
                        "remaining_count": 0,
                        "curriculum_size": 0,
                    },
                    requires_officer_review=False,
                )
            )

    await session.commit()
    if new_regs:
        print(
            f"✅ Seeded {new_regs} registrations, {new_links} "
            f"registration courses, {new_history} status-history rows."
        )
    else:
        print("⚠️  Sample registrations already present — skipping.")


async def _seed_bulk_se_sem1_cohort(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
) -> None:
    """
    Seed ``SE_SEM1_BULK_COUNT`` Software-Engineering, semester-1,
    REGISTERED students for ``term``. Idempotent per-student.
    """
    se_sem1_courses = [courses_by_code[c] for c in SE_SEM1_BULK_COURSES]

    new_users = 0
    new_regs = 0
    new_links = 0
    for i in range(SE_SEM1_BULK_COUNT):
        seq = SE_SEM1_BULK_START_SEQ + i
        student_id_str = f"UGR/{seq:04d}/14"
        slug = student_id_str.lower().replace("/", "-")
        email = f"{slug}@aau.edu.et"

        sponsorship = _SELF if i % 3 == 0 else _GOV

        student = (
            await session.execute(
                select(Student).where(Student.student_id == student_id_str)
            )
        ).scalar_one_or_none()
        if student is None:
            user = await _ensure_user(
                session,
                email=email,
                first_name=f"Cohort{seq}",
                last_name="SeSem1Student",
                role=UserRole.STUDENT,
                user_uid=_uid("user", "student", student_id_str),
            )
            student = Student(
                id=_uid("student", student_id_str),
                user_id=user.id,
                student_id=student_id_str,
                full_name=f"Cohort{seq} SeSem1Student",
                current_semester=1,
                department="Software Engineering",
                sponsorship_type=sponsorship,
                enrollment_status=EnrollmentStatus.ACTIVE,
            )
            session.add(student)
            await session.flush()
            new_users += 1

        existing_reg = (
            await session.execute(
                select(Registration).where(
                    Registration.student_id == student.id,
                    Registration.term_id == term.id,
                )
            )
        ).scalar_one_or_none()
        if existing_reg is not None:
            continue

        reg = Registration(
            id=_uid(
                "registration", term.term_name, term.phase.value,
                student_id_str,
            ),
            student_id=student.id,
            term_id=term.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=sponsorship,
            payment_reference=(
                None if sponsorship == _GOV
                else f"MOCK-PAID-FALL2026-{slug.upper()}"
            ),
        )
        session.add(reg)
        await session.flush()
        new_regs += 1

        for course in se_sem1_courses:
            session.add(RegistrationCourse(
                id=_uid("regcourse", student_id_str, course.code),
                registration_id=reg.id,
                course_id=course.id,
                is_dropped=False,
            ))
            new_links += 1

        session.add(RegistrationStatusHistory(
            id=_uid("reghist", "init", student_id_str),
            registration_id=reg.id,
            previous_status=None,
            new_status=RegistrationStatus.REGISTERED,
            changed_by_id=None,
            agent_id="seed-bulk-se-sem1",
            trigger_reason="Seed: bulk SE-semester-1 cohort",
        ))

    await session.commit()
    if new_regs or new_users:
        print(
            f"✅ Seeded bulk SE-sem1 cohort: {new_users} new students, "
            f"{new_regs} registrations, {new_links} course links."
        )
    else:
        print("⚠️  Bulk SE-sem1 cohort already present — skipping.")


async def _seed_bulk_se_upper_year_cohorts(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
) -> None:
    """Seed 60 fully-registered SE students per upper-year cohort (3,5,7,9)."""
    new_users = 0
    new_regs = 0
    new_links = 0

    for batch_year, semester, count in SE_BULK_UPPER_COHORTS:
        course_codes = [
            f"SE{semester}01", f"SE{semester}02",
            f"SE{semester}03", f"SE{semester}04",
        ]
        try:
            target_courses = [courses_by_code[c] for c in course_codes]
        except KeyError as missing:
            raise RuntimeError(
                f"SE semester-{semester} curriculum incomplete: "
                f"course {missing.args[0]} not seeded. Did you skip "
                "the _seed_courses step?"
            ) from None

        for i in range(count):
            seq = i + 1
            student_id_str = f"UGR/{seq:04d}/{batch_year:02d}"
            slug = student_id_str.lower().replace("/", "-")
            email = f"{slug}@aau.edu.et"

            sponsorship = _SELF if i % 3 == 0 else _GOV

            student = (
                await session.execute(
                    select(Student).where(Student.student_id == student_id_str)
                )
            ).scalar_one_or_none()
            if student is None:
                user = await _ensure_user(
                    session,
                    email=email,
                    first_name=f"Year{semester // 2 + 1}Student{seq}",
                    last_name=f"Batch{batch_year:02d}",
                    role=UserRole.STUDENT,
                    user_uid=_uid("user", "student", student_id_str),
                )
                student = Student(
                    id=_uid("student", student_id_str),
                    user_id=user.id,
                    student_id=student_id_str,
                    full_name=(
                        f"Year{semester // 2 + 1}Student{seq} "
                        f"Batch{batch_year:02d}"
                    ),
                    current_semester=semester,
                    department="Software Engineering",
                    sponsorship_type=sponsorship,
                    enrollment_status=EnrollmentStatus.ACTIVE,
                )
                session.add(student)
                await session.flush()
                new_users += 1

            existing_reg = (
                await session.execute(
                    select(Registration).where(
                        Registration.student_id == student.id,
                        Registration.term_id == term.id,
                    )
                )
            ).scalar_one_or_none()
            if existing_reg is not None:
                continue

            reg = Registration(
                id=_uid(
                    "registration", term.term_name, term.phase.value,
                    student_id_str,
                ),
                student_id=student.id,
                term_id=term.id,
                status=RegistrationStatus.REGISTERED,
                sponsorship_type=sponsorship,
                payment_reference=(
                    None if sponsorship == _GOV
                    else f"MOCK-PAID-FALL2026-{slug.upper()}"
                ),
            )
            session.add(reg)
            await session.flush()
            new_regs += 1

            for course in target_courses:
                session.add(RegistrationCourse(
                    id=_uid("regcourse", student_id_str, course.code),
                    registration_id=reg.id,
                    course_id=course.id,
                    is_dropped=False,
                ))
                new_links += 1

            session.add(RegistrationStatusHistory(
                id=_uid("reghist", "init", student_id_str),
                registration_id=reg.id,
                previous_status=None,
                new_status=RegistrationStatus.REGISTERED,
                changed_by_id=None,
                agent_id=f"seed-bulk-se-sem{semester}",
                trigger_reason=(
                    f"Seed: bulk SE-semester-{semester} (year "
                    f"{semester // 2 + 1}) cohort"
                ),
            ))

    await session.commit()
    if new_regs or new_users:
        print(
            f"✅ Seeded bulk SE upper-year cohorts: {new_users} new "
            f"students, {new_regs} registrations, {new_links} course links."
        )
    else:
        print("⚠️  Bulk SE upper-year cohorts already present — skipping.")


# ══════════════════════════════════════════════════════════════
#  Grades — academic history backing the advisory consult flow
# ══════════════════════════════════════════════════════════════

_SEED_GRADE_CYCLE = (
    GradeLetter.A_PLUS,
    GradeLetter.A,
    GradeLetter.B_PLUS,
    GradeLetter.B,
    GradeLetter.B_MINUS,
    GradeLetter.C_PLUS,
    GradeLetter.A_MINUS,
    GradeLetter.B,
    GradeLetter.C,
)


def _seeded_letter_for(
    student_id: str, course_code: str,
) -> GradeLetter:
    """Deterministic grade picker — same input → same letter every run."""
    bucket = (hash(f"{student_id}:{course_code}") & 0xFFFF) % len(
        _SEED_GRADE_CYCLE
    )
    return _SEED_GRADE_CYCLE[bucket]


async def _seed_grades(
    session: AsyncSession,
    terms: list[AcademicTerm],
    courses_by_code: dict[str, Course],
) -> None:
    """
    Seed AUTHORISED grades for every seeded student's completed
    semesters. Each row is keyed by ``_uid("grade", student_id,
    course_code)`` so the seed is idempotent.
    """
    students = (await session.execute(select(Student))).scalars().all()
    instructors = (await session.execute(select(Instructor))).scalars().all()
    officers = (
        await session.execute(select(CourseManagementOfficer))
    ).scalars().all()
    if not students or not instructors or not officers:
        print("⚠️  Skipping grades — no students/instructors/officers seeded.")
        return

    instructor_user_id = instructors[0].user_id
    officer_user_id = officers[0].user_id
    history_term = min(terms, key=lambda t: t.start_date)

    existing = (await session.execute(select(Grade))).scalars().all()
    existing_pairs = {(g.student_id, g.course_id, g.term_id) for g in existing}

    new_count = 0
    for student in students:
        if student.current_semester <= 1:
            continue
        if not student.department:
            continue
        for sem in range(1, student.current_semester):
            for slot in range(1, 5):
                code = _course_code(student.department, sem, slot)
                course = courses_by_code.get(code)
                if course is None:
                    continue
                key = (student.id, course.id, history_term.id)
                if key in existing_pairs:
                    continue

                letter = _seeded_letter_for(student.student_id, code)
                pts = points_for(letter)
                grade_points = (
                    pts * course.credit_hours if pts is not None else None
                )
                numeric = {
                    GradeLetter.A_PLUS:   95.0,
                    GradeLetter.A:        86.0,
                    GradeLetter.A_MINUS:  81.0,
                    GradeLetter.B_PLUS:   77.0,
                    GradeLetter.B:        71.0,
                    GradeLetter.B_MINUS:  66.0,
                    GradeLetter.C_PLUS:   62.0,
                    GradeLetter.C:        55.0,
                    GradeLetter.C_MINUS:  47.0,
                    GradeLetter.D:        42.0,
                    GradeLetter.F:        30.0,
                }.get(letter)

                session.add(Grade(
                    id=_uid("grade", student.student_id, code),
                    student_id=student.id,
                    course_id=course.id,
                    term_id=history_term.id,
                    letter_grade=letter,
                    numeric_score=numeric,
                    credit_hours=course.credit_hours,
                    grade_points=grade_points,
                    status=GradeSubmissionStatus.AUTHORISED,
                    entered_by_id=instructor_user_id,
                    entered_at=datetime.now(timezone.utc),
                    authorised_by_id=officer_user_id,
                    authorised_at=datetime.now(timezone.utc),
                ))
                new_count += 1

    await session.commit()
    if new_count:
        print(
            f"✅ Seeded {new_count} AUTHORISED grade rows backfilling "
            f"{len([s for s in students if s.current_semester > 1])} "
            "students' prior-semester history."
        )
    else:
        print("⚠️  All backfill grades already present — skipping.")


# ══════════════════════════════════════════════════════════════
#  Track C — Standing demo cohort
# ══════════════════════════════════════════════════════════════


async def _seed_standing_demo_cohort(
    session: AsyncSession,
    terms: list[AcademicTerm],
) -> None:
    """Make ``history_term`` browsable in the Track C standing flow."""
    history_term = min(terms, key=lambda t: t.start_date)

    students = (
        await session.execute(
            select(Student).where(
                Student.is_deleted == False,  # noqa: E712
                Student.department.is_not(None),
                Student.current_semester > 1,
            )
        )
    ).scalars().all()
    if not students:
        print("⚠️  Skipping standing demo cohort — no eligible students.")
        return

    by_dept: dict[str, list[Student]] = {}
    for stu in students:
        by_dept.setdefault(stu.department, []).append(stu)
    for dept_list in by_dept.values():
        dept_list.sort(key=lambda s: s.student_id)

    new_sections = 0
    new_regs = 0
    for dept, dept_students in by_dept.items():
        cohort = dept_students[:5]
        if not cohort:
            continue

        section_id = _uid(
            "standing-demo-section", str(history_term.id), dept,
        )
        section = (await session.execute(
            select(Section).where(Section.id == section_id)
        )).scalar_one_or_none()

        if section is None:
            section = Section(
                id=section_id,
                term_id=history_term.id,
                department=dept,
                semester=1,
                section_code="A",
                capacity=max(30, len(cohort)),
                enrolled_count=0,
            )
            session.add(section)
            await session.flush()
            new_sections += 1

        for stu in cohort:
            existing = (await session.execute(
                select(Registration).where(
                    Registration.student_id == stu.id,
                    Registration.term_id == history_term.id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                if existing.section_id is None:
                    existing.section_id = section.id
                    existing.status = RegistrationStatus.REGISTERED
                continue
            reg = Registration(
                id=_uid(
                    "standing-demo-reg", str(stu.id), str(history_term.id),
                ),
                student_id=stu.id,
                term_id=history_term.id,
                status=RegistrationStatus.REGISTERED,
                sponsorship_type=(
                    stu.sponsorship_type or SponsorshipType.GOVERNMENT
                ),
                section_id=section.id,
                finalised_at=datetime.now(timezone.utc),
            )
            session.add(reg)
            new_regs += 1

        section.enrolled_count = min(section.capacity, len(cohort))

    await session.commit()
    if new_sections or new_regs:
        print(
            f"✅ Seeded standing demo cohort: {new_sections} sections, "
            f"{new_regs} registrations into '{history_term.term_name}'."
        )
    else:
        print(
            f"⚠️  Standing demo cohort already present in "
            f"'{history_term.term_name}' — skipping."
        )


# ══════════════════════════════════════════════════════════════
#  Track B PR 1 — roster demo (self-contained scenario)
# ══════════════════════════════════════════════════════════════
# A separate term ("Track-B-Demo-2026"), one instructor (Dr. Lemma)
# teaching CS101 in two sections A and B. Three add/drop edge cases
# the roster derivation must handle:
#
#   * 4 originals in A taking CS101
#   * 4 originals in B taking CS101
#   * 1 student from B who ADDED CS101 from A
#   * 1 student from A who DROPPED CS101 entirely
#   * 1 student in A whose registration is still REGISTRATION_OPEN
#
# Login credentials seeded for manual testing:
#   instructor    staff-9991-15@aau.edu.et   password123
#   dept head     reg-9999-15@aau.edu.et     password123

TB_TERM_NAME = "Track-B-Demo-2026"
TB_DEPARTMENT = "Software Engineering"
TB_SEMESTER = 1

TB_COURSE_CODE = "CS101"
TB_COURSE_TITLE = "Introduction to Programming"
TB_COURSE_CREDITS = 3

TB_SECONDARY_COURSE_CODE = "MATH101"
TB_SECONDARY_COURSE_TITLE = "Calculus I"
TB_SECONDARY_COURSE_CREDITS = 3

TB_INSTRUCTOR_STAFF_ID = "STAFF/9991/15"
TB_INSTRUCTOR_FIRST = "Lemma"
TB_INSTRUCTOR_LAST = "Bekele"

TB_DH_STAFF_ID = "REG/9999/15"
TB_DH_FIRST = "Almaz"
TB_DH_LAST = "Tilahun"

TB_SECTION_A_STUDENTS = [
    ("UGR/9001/15", "Abel Tesfaye"),
    ("UGR/9002/15", "Bethel Demissie"),
    ("UGR/9003/15", "Chala Worku"),
    ("UGR/9004/15", "Dawit Asefa"),
    ("UGR/9005/15", "Eyerusalem Hailu"),   # will DROP CS101
    ("UGR/9006/15", "Feven Mulu"),         # REGISTRATION_OPEN draft
]
_TB_DROPPER_INDEX = 4
_TB_DRAFT_INDEX = 5

TB_SECTION_B_STUDENTS = [
    ("UGR/9011/15", "Genet Aklilu"),
    ("UGR/9012/15", "Hana Yonas"),
    ("UGR/9013/15", "Ibrahim Mohammed"),
    ("UGR/9014/15", "Jemberu Kassa"),
    ("UGR/9015/15", "Kalkidan Tadesse"),   # will ADD CS101 from A
]
_TB_MOVER_INDEX = 4


async def _tb_ensure_term(session: AsyncSession) -> AcademicTerm:
    existing = (
        await session.execute(
            select(AcademicTerm).where(
                AcademicTerm.term_name == TB_TERM_NAME,
                AcademicTerm.phase == AcademicPhase.ONE,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    term = AcademicTerm(
        id=_uid_tb("term", TB_TERM_NAME),
        term_name=TB_TERM_NAME,
        phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
        description="Track B PR 1 manual-test demo term.",
    )
    session.add(term)
    await session.flush()
    return term


async def _tb_ensure_course(
    session: AsyncSession, *, code: str, title: str, credits: int,
) -> Course:
    existing = (
        await session.execute(select(Course).where(Course.code == code))
    ).scalar_one_or_none()
    if existing:
        return existing
    course = Course(
        id=_uid_tb("course", code),
        code=code,
        title=title,
        credit_hours=credits,
        semester=TB_SEMESTER,
        department=TB_DEPARTMENT,
        description=f"{title} — Track B demo course.",
    )
    session.add(course)
    await session.flush()
    return course


async def _tb_ensure_instructor(session: AsyncSession) -> Instructor:
    existing = (
        await session.execute(
            select(Instructor).where(
                Instructor.instructor_id == TB_INSTRUCTOR_STAFF_ID
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slug = TB_INSTRUCTOR_STAFF_ID.lower().replace("/", "-")
    user = await _ensure_user(
        session,
        email=f"{slug}@aau.edu.et",
        first_name=TB_INSTRUCTOR_FIRST,
        last_name=TB_INSTRUCTOR_LAST,
        role=UserRole.INSTRUCTOR,
        user_uid=_uid_tb("user", "instructor", TB_INSTRUCTOR_STAFF_ID),
    )
    instructor = Instructor(
        id=_uid_tb("instructor", TB_INSTRUCTOR_STAFF_ID),
        user_id=user.id,
        instructor_id=TB_INSTRUCTOR_STAFF_ID,
        department=TB_DEPARTMENT,
    )
    session.add(instructor)
    await session.flush()
    return instructor


async def _tb_ensure_department_head(
    session: AsyncSession,
) -> CourseManagementOfficer:
    existing = (
        await session.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.staff_id == TB_DH_STAFF_ID,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slug = TB_DH_STAFF_ID.lower().replace("/", "-")
    user = await _ensure_user(
        session,
        email=f"{slug}@aau.edu.et",
        first_name=TB_DH_FIRST,
        last_name=TB_DH_LAST,
        role=UserRole.REGISTRAR_OFFICER,
        user_uid=_uid_tb("user", "officer", TB_DH_STAFF_ID),
    )
    officer = CourseManagementOfficer(
        id=_uid_tb("officer", TB_DH_STAFF_ID),
        user_id=user.id,
        staff_id=TB_DH_STAFF_ID,
        role=OfficerRole.DEPARTMENT_HEAD,
        authorization_level=5,
    )
    session.add(officer)
    await session.flush()
    return officer


async def _tb_ensure_instructor_assignment(
    session: AsyncSession,
    *,
    instructor: Instructor,
    course: Course,
    term: AcademicTerm,
) -> InstructorAssignment:
    existing = (
        await session.execute(
            select(InstructorAssignment).where(
                InstructorAssignment.instructor_id == instructor.id,
                InstructorAssignment.course_id == course.id,
                InstructorAssignment.term_id == term.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    assignment = InstructorAssignment(
        id=_uid_tb(
            "assignment", str(instructor.id), str(course.id), str(term.id),
        ),
        instructor_id=instructor.id,
        course_id=course.id,
        term_id=term.id,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def _tb_ensure_section(
    session: AsyncSession,
    *,
    term: AcademicTerm,
    section_code: str,
    capacity: int = 30,
) -> Section:
    existing = (
        await session.execute(
            select(Section).where(
                Section.term_id == term.id,
                Section.department == TB_DEPARTMENT,
                Section.semester == TB_SEMESTER,
                Section.section_code == section_code,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    section = Section(
        id=_uid_tb(
            "section", str(term.id), TB_DEPARTMENT,
            str(TB_SEMESTER), section_code,
        ),
        term_id=term.id,
        department=TB_DEPARTMENT,
        semester=TB_SEMESTER,
        section_code=section_code,
        capacity=capacity,
        enrolled_count=0,
    )
    session.add(section)
    await session.flush()
    return section


async def _tb_ensure_slot(
    session: AsyncSession,
    *,
    section: Section,
    course: Course,
    instructor: Instructor,
    day_of_week: str,
    start_hour: int,
    end_hour: int,
    room: str,
) -> ClassScheduleSlot:
    existing = (
        await session.execute(
            select(ClassScheduleSlot).where(
                ClassScheduleSlot.section_id == section.id,
                ClassScheduleSlot.day_of_week == day_of_week,
                ClassScheduleSlot.start_time == time(start_hour, 0),
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slot = ClassScheduleSlot(
        id=_uid_tb(
            "slot",
            str(section.id), str(course.id),
            day_of_week, str(start_hour),
        ),
        section_id=section.id,
        course_id=course.id,
        instructor_id=instructor.id,
        day_of_week=day_of_week,
        start_time=time(start_hour, 0),
        end_time=time(end_hour, 0),
        room=room,
    )
    session.add(slot)
    await session.flush()
    return slot


async def _tb_ensure_student(
    session: AsyncSession,
    *,
    student_id: str,
    full_name: str,
) -> Student:
    existing = (
        await session.execute(
            select(Student).where(Student.student_id == student_id)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slug = student_id.lower().replace("/", "-")
    first, *rest = full_name.split(" ", 1)
    last = rest[0] if rest else "."
    user = await _ensure_user(
        session,
        email=f"{slug}@aau.edu.et",
        first_name=first,
        last_name=last,
        role=UserRole.STUDENT,
        user_uid=_uid_tb("user", "student", student_id),
    )
    student = Student(
        id=_uid_tb("student", student_id),
        user_id=user.id,
        student_id=student_id,
        full_name=full_name,
        current_semester=TB_SEMESTER,
        department=TB_DEPARTMENT,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    session.add(student)
    await session.flush()
    return student


async def _tb_ensure_registration(
    session: AsyncSession,
    *,
    student: Student,
    term: AcademicTerm,
    section: Section,
    status_: RegistrationStatus,
) -> Registration:
    existing = (
        await session.execute(
            select(Registration).where(
                Registration.student_id == student.id,
                Registration.term_id == term.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.section_id != section.id:
            existing.section_id = section.id
        if existing.status != status_:
            existing.status = status_
        await session.flush()
        return existing
    registration = Registration(
        id=_uid_tb("registration", str(student.id), str(term.id)),
        student_id=student.id,
        term_id=term.id,
        section_id=section.id,
        status=status_,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        finalised_at=datetime.now(timezone.utc)
        if status_ == RegistrationStatus.REGISTERED
        else None,
    )
    session.add(registration)
    await session.flush()
    return registration


async def _tb_ensure_registration_course(
    session: AsyncSession,
    *,
    registration: Registration,
    course: Course,
    is_dropped: bool,
) -> RegistrationCourse:
    existing = (
        await session.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration.id,
                RegistrationCourse.course_id == course.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.is_dropped != is_dropped:
            existing.is_dropped = is_dropped
            await session.flush()
        return existing
    rc = RegistrationCourse(
        id=_uid_tb(
            "registration-course",
            str(registration.id), str(course.id),
        ),
        registration_id=registration.id,
        course_id=course.id,
        is_dropped=is_dropped,
    )
    session.add(rc)
    await session.flush()
    return rc


async def _tb_ensure_schedule_addition(
    session: AsyncSession,
    *,
    registration: Registration,
    slot: ClassScheduleSlot,
    course: Course,
    source_section: Section,
) -> StudentScheduleAddition:
    existing = (
        await session.execute(
            select(StudentScheduleAddition).where(
                StudentScheduleAddition.registration_id == registration.id,
                StudentScheduleAddition.schedule_slot_id == slot.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    addition = StudentScheduleAddition(
        id=_uid_tb(
            "schedule-addition",
            str(registration.id), str(slot.id),
        ),
        registration_id=registration.id,
        schedule_slot_id=slot.id,
        course_id=course.id,
        source_section_id=source_section.id,
    )
    session.add(addition)
    await session.flush()
    return addition


async def _seed_track_b_roster(session: AsyncSession) -> None:
    """Build the Track B PR 1 demo scenario end-to-end. Idempotent."""
    print(">> Track B PR 1 — roster seed")

    term = await _tb_ensure_term(session)
    print(f"   term:          {term.term_name} ({term.id})")

    course = await _tb_ensure_course(
        session, code=TB_COURSE_CODE,
        title=TB_COURSE_TITLE, credits=TB_COURSE_CREDITS,
    )
    secondary_course = await _tb_ensure_course(
        session, code=TB_SECONDARY_COURSE_CODE,
        title=TB_SECONDARY_COURSE_TITLE, credits=TB_SECONDARY_COURSE_CREDITS,
    )

    instructor = await _tb_ensure_instructor(session)
    print(f"   instructor:    {TB_INSTRUCTOR_FIRST} {TB_INSTRUCTOR_LAST}")
    print(f"                  login: staff-9991-15@aau.edu.et / password123")

    dh = await _tb_ensure_department_head(session)
    print(f"   dept head:     {TB_DH_FIRST} {TB_DH_LAST}")
    print(f"                  login: reg-9999-15@aau.edu.et / password123")

    await _tb_ensure_instructor_assignment(
        session, instructor=instructor, course=course, term=term,
    )

    section_a = await _tb_ensure_section(
        session, term=term, section_code="A", capacity=30,
    )
    section_b = await _tb_ensure_section(
        session, term=term, section_code="B", capacity=30,
    )

    # CS101 slots: instructor teaches in both A and B.
    slot_a_cs101 = await _tb_ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="MON", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _tb_ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="WED", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _tb_ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="FRI", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _tb_ensure_slot(
        session,
        section=section_b, course=course, instructor=instructor,
        day_of_week="TUE", start_hour=11, end_hour=12, room="LAB-2",
    )
    await _tb_ensure_slot(
        session,
        section=section_b, course=course, instructor=instructor,
        day_of_week="THU", start_hour=11, end_hour=12, room="LAB-2",
    )
    # MATH101 slot in Section A — verifies GET /me/sections returns
    # two (section, course) pairs for Section A rather than collapsing.
    await _tb_ensure_slot(
        session,
        section=section_a, course=secondary_course, instructor=instructor,
        day_of_week="TUE", start_hour=14, end_hour=15, room="LAB-1",
    )

    # Section A students — one drops, one is draft, rest are originals.
    for i, (student_id, full_name) in enumerate(TB_SECTION_A_STUDENTS):
        student = await _tb_ensure_student(
            session, student_id=student_id, full_name=full_name,
        )
        if i == _TB_DRAFT_INDEX:
            reg = await _tb_ensure_registration(
                session, student=student, term=term, section=section_a,
                status_=RegistrationStatus.REGISTRATION_OPEN,
            )
            await _tb_ensure_registration_course(
                session, registration=reg, course=course, is_dropped=False,
            )
        else:
            reg = await _tb_ensure_registration(
                session, student=student, term=term, section=section_a,
                status_=RegistrationStatus.REGISTERED,
            )
            dropped = (i == _TB_DROPPER_INDEX)
            await _tb_ensure_registration_course(
                session, registration=reg, course=course, is_dropped=dropped,
            )

    # Section B students — one moves CS101 to Section A.
    for i, (student_id, full_name) in enumerate(TB_SECTION_B_STUDENTS):
        student = await _tb_ensure_student(
            session, student_id=student_id, full_name=full_name,
        )
        reg = await _tb_ensure_registration(
            session, student=student, term=term, section=section_b,
            status_=RegistrationStatus.REGISTERED,
        )
        if i == _TB_MOVER_INDEX:
            await _tb_ensure_registration_course(
                session, registration=reg, course=course, is_dropped=True,
            )
            await _tb_ensure_schedule_addition(
                session,
                registration=reg, slot=slot_a_cs101,
                course=course, source_section=section_a,
            )
        else:
            await _tb_ensure_registration_course(
                session, registration=reg, course=course, is_dropped=False,
            )

    await session.commit()
    print("✅ Track B PR 1 roster seeded (Section A: 4 originals + 1 added; "
          "Section B: 4 originals).")


# ══════════════════════════════════════════════════════════════
#  Final state summary (was: migrate_terms_to_ethiopian_phases.py)
# ══════════════════════════════════════════════════════════════


async def _print_term_state(session: AsyncSession) -> None:
    """Print the current set of AcademicTerm rows, ordered by start date."""
    print()
    print("─" * 58)
    print("Final academic-term state:")
    rows = (
        await session.execute(
            select(AcademicTerm)
            .where(AcademicTerm.is_deleted == False)  # noqa: E712
            .order_by(AcademicTerm.start_date.asc())
        )
    ).scalars().all()
    for r in rows:
        flag = "OPEN " if r.is_open else "     "
        print(
            f"  [{flag}] {r.term_name}  phase={r.phase.value:<3}  "
            f"{r.start_date.isoformat()} → {r.end_date.isoformat()}"
        )


# ══════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    async with async_session() as session:
        terms = await _seed_terms(session)
        courses_by_code = await _seed_courses(session)
        await _seed_extra_se_sem1_courses(session, courses_by_code)
        await _seed_prerequisites(session, courses_by_code)
        await _seed_classrooms(session)
        instructors = await _seed_instructors(session)
        for term in terms:
            await _seed_instructor_assignments(
                session, term, courses_by_code, instructors,
            )
        await _seed_students(session)
        await _seed_officers(session)

        open_term = next((t for t in terms if t.is_open), terms[0])
        await _seed_registrations(session, open_term, courses_by_code)
        await _seed_bulk_se_sem1_cohort(session, open_term, courses_by_code)
        await _seed_bulk_se_upper_year_cohorts(
            session, open_term, courses_by_code,
        )

        await _seed_grades(session, terms, courses_by_code)
        await _seed_standing_demo_cohort(session, terms)

        # Track B PR 1 demo — fully self-contained scenario in its own term.
        await _seed_track_b_roster(session)

        # Final summary so it's obvious which terms ended up in the DB.
        await _print_term_state(session)

    await engine.dispose()
    print(
        "\n🎉 Course Management seed complete "
        "(Phase 0 + Track A samples + Track B roster + extras)."
    )


if __name__ == "__main__":
    asyncio.run(seed())
