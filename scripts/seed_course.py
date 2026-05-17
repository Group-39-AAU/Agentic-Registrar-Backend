"""
Course Management — Phase 0 seed data.

Populates the deterministic sandbox that every track (A/B/C) reuses
in its own tests so feature work in any one track can be demoed
without dependencies on the others.

Idempotent: running twice does not create duplicates.

Usage:
    cd /path/to/project
    source venv/bin/activate
    python scripts/seed_course.py

Phase 0 seeds (this script):
    1. Two AcademicTerms — Fall 2026 (open) and Spring 2027 (closed),
       i.e. one full academic year of two semesters
    2. 240 Courses across 6 engineering departments (4 per
       (department, semester) cell, 5-year programs = 10 semesters)
    3. ~28 intra-department prerequisite edges spanning 3+ depth levels
    4. 12 Instructors (2 per department) + InstructorAssignments per term
       (cohort Section rows are emitted by the scheduling agent at
       allocation time, not at seed time)
    5. 30 Students (5 per department) across semesters 1–8
    6. Registrar Officer + Department Head (Software Engineering) accounts
"""

import asyncio
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from datetime import datetime, timezone

from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, AdvisoryRecommendation, Classroom, Course,
    CoursePrerequisite, Grade, Instructor, InstructorAssignment, Registration,
    RegistrationCourse, RegistrationStatusHistory, Student,
    CourseManagementOfficer,
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
# Using uuid5 with a fixed namespace so every run of the seed gets
# the same IDs. This means tests can hard-code "Fall 2026" =
# COURSE_NS:term:fall-2026 and look it up reliably.

_NS = uuid.UUID("c0a3e000-0000-4000-8000-000000000000")  # "course phase 0"


def _uid(*parts: str) -> uuid.UUID:
    """Stable UUID derived from the slash-joined parts."""
    return uuid.uuid5(_NS, "/".join(parts))


# ══════════════════════════════════════════════════════════════
#  Academic Term
# ══════════════════════════════════════════════════════════════

# Ethiopian-context academic calendar — every academic year is split
# into two phases:
#
#   Phase One: September → end of January  (≈ Meskerem → Tir)
#   Phase Two: February  → end of June     (≈ Yekatit  → Sene)
#
# We seed the previous and the upcoming academic year (4 terms total).
# Whichever phase covers "today" is marked ``is_open=True`` so the
# registration portal is testable out of the box without an officer
# manually flipping a window. The other three stay closed; officers
# can exercise the open/close endpoints freely.
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
# 4 courses per (department, semester) cell. No cross-department
# course sharing — each department has its own version of any
# foundational subject (Calculus, Physics, etc.) so the curriculum
# filter can be a strict ``course.department == student.department``.
#
# 6 × 10 × 4 = 240 courses total.

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


# Per-(department, semester) course list as (title, credit_hours).
# 4 courses per cell, designed to look like a realistic AAIT
# 5-year engineering curriculum (foundation → core → advanced →
# capstone). Order within a semester is stable — slot 1, 2, 3, 4.
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


# Generated catalog: (code, title, credit_hours, semester, department).
# Order is dept-major, semester-minor, slot-tertiary so existing tests
# that assume a deterministic insertion order still see consistent UUIDs.
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


# ══════════════════════════════════════════════════════════════
#  Prerequisites (intra-department only — no cross-department FKs
#  since each department has its own version of every foundational
#  course like Calculus / Discrete Math / Engineering Mechanics).
# ══════════════════════════════════════════════════════════════
# (course_code, prerequisite_course_code)
# 4-6 chains per department, designed to span at least 3 depth
# levels so the compliance agent has non-trivial cases.

PREREQUISITES = [
    # ── Software Engineering ────────────────────────────────────
    ("SE201", "SE101"),     # Calculus II  ← Calculus I
    ("SE303", "SE102"),     # Data Structures ← Programming Fundamentals
    ("SE402", "SE303"),     # Algorithms ← Data Structures
    ("SE403", "SE303"),     # Database Systems ← Data Structures
    ("SE601", "SE503"),     # Software Architecture ← SE Principles

    # ── Electrical Engineering ──────────────────────────────────
    ("EE201", "EE101"),     # Calculus II ← Calculus I
    ("EE402", "EE303"),     # Circuit Theory II ← Circuit Theory I
    ("EE403", "EE304"),     # Electronics II ← Electronics I
    ("EE503", "EE404"),     # Microprocessors ← Digital Logic Design
    ("EE602", "EE502"),     # Electromagnetics II ← Electromagnetics I

    # ── Chemical Engineering ────────────────────────────────────
    ("ChE201", "ChE101"),   # Calculus II ← Calculus I
    ("ChE403", "ChE201"),   # Thermodynamics I ← Calculus II
    ("ChE503", "ChE403"),   # Reaction Engineering I ← Thermodynamics I
    ("ChE603", "ChE503"),   # Reaction Engineering II ← Reaction Engineering I

    # ── Civil Engineering ───────────────────────────────────────
    ("CE201", "CE101"),     # Calculus II ← Calculus I
    ("CE402", "CE303"),     # Surveying II ← Surveying I
    ("CE501", "CE403"),     # Theory of Structures I ← Strength of Materials
    ("CE601", "CE501"),     # Theory of Structures II ← Theory of Structures I
    ("CE502", "CE304"),     # Soil Mechanics I ← Engineering Geology

    # ── Mechanical Engineering ──────────────────────────────────
    ("ME201", "ME101"),     # Calculus II ← Calculus I
    ("ME402", "ME303"),     # Strength of Materials II ← Strength of Materials I
    ("ME403", "ME201"),     # Thermodynamics I ← Calculus II
    ("ME502", "ME403"),     # Thermodynamics II ← Thermodynamics I
    ("ME601", "ME501"),     # Heat Transfer ← Fluid Mechanics

    # ── Bio Medical Engineering ─────────────────────────────────
    ("BME201", "BME101"),   # Calculus II ← Calculus I
    ("BME402", "BME303"),   # Anatomy & Physiology II ← Anatomy & Physiology I
    ("BME501", "BME402"),   # Medical Instrumentation I ← Anatomy & Physiology II
    ("BME601", "BME501"),   # Medical Instrumentation II ← Medical Instrumentation I
]


# ══════════════════════════════════════════════════════════════
#  Instructors (12 across 6 departments — 2 per department)
# ══════════════════════════════════════════════════════════════
# (instructor_id, first_name, last_name, department)

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
# Three rooms per department: a big lecture hall, a mid-size room,
# and a small lab. Software Engineering's biggest hall (SE-101) is
# 80 seats so the bulk SE-sem-1 cohort (72 students) fits in one
# room rather than splitting. Other departments don't have that
# volume yet so 60-seat lectures suffice.

# (name, capacity, department)
CLASSROOMS = [
    # ── Software Engineering ────────────────────────────
    ("SE-101",   80, "Software Engineering"),
    ("SE-201",   60, "Software Engineering"),
    ("SE-LAB-1", 30, "Software Engineering"),

    # ── Electrical Engineering ──────────────────────────
    ("EE-101",   60, "Electrical Engineering"),
    ("EE-201",   50, "Electrical Engineering"),
    ("EE-LAB-1", 30, "Electrical Engineering"),

    # ── Chemical Engineering ────────────────────────────
    ("ChE-101",   60, "Chemical Engineering"),
    ("ChE-201",   50, "Chemical Engineering"),
    ("ChE-LAB-1", 30, "Chemical Engineering"),

    # ── Civil Engineering ───────────────────────────────
    ("CE-101",   60, "Civil Engineering"),
    ("CE-201",   50, "Civil Engineering"),
    ("CE-LAB-1", 30, "Civil Engineering"),

    # ── Mechanical Engineering ──────────────────────────
    ("ME-101",   60, "Mechanical Engineering"),
    ("ME-201",   50, "Mechanical Engineering"),
    ("ME-LAB-1", 30, "Mechanical Engineering"),

    # ── Bio Medical Engineering ─────────────────────────
    ("BME-101",   60, "Bio Medical Engineering"),
    ("BME-201",   50, "Bio Medical Engineering"),
    ("BME-LAB-1", 30, "Bio Medical Engineering"),
]


# ══════════════════════════════════════════════════════════════
#  Students (30 across semesters 1–8, distributed across the 6 depts)
# ══════════════════════════════════════════════════════════════
# (student_id, first_name, last_name, current_semester, department, sponsorship)
# 5 students per department × 6 departments = 30 students. Semesters
# are spread 1-8 within each department so Track A has students at
# multiple curriculum depths. Sponsorship is mixed (~70% government,
# ~30% self-sponsored) to reflect the typical AAU intake mix.

_GOV = SponsorshipType.GOVERNMENT
_SELF = SponsorshipType.SELF_SPONSORED

STUDENTS = [
    # ── Software Engineering (5 students) ───────────────────────
    ("UGR/0001/14", "Abel",      "Tesfaye",     1, "Software Engineering",   _GOV),
    ("UGR/0002/14", "Beza",      "Worku",       2, "Software Engineering",   _SELF),
    ("UGR/0003/14", "Caleb",     "Mulugeta",    3, "Software Engineering",   _GOV),
    ("UGR/0004/14", "Dina",      "Hailemariam", 5, "Software Engineering",   _GOV),
    ("UGR/0005/14", "Ermias",    "Bekele",      7, "Software Engineering",   _SELF),

    # ── Electrical Engineering (5 students) ─────────────────────
    ("UGR/0006/14", "Frehiwot",  "Asrat",       1, "Electrical Engineering", _GOV),
    ("UGR/0007/14", "Gemechu",   "Olana",       2, "Electrical Engineering", _GOV),
    ("UGR/0008/14", "Helen",     "Yohannes",    4, "Electrical Engineering", _SELF),
    ("UGR/0009/14", "Isaac",     "Demeke",      6, "Electrical Engineering", _GOV),
    ("UGR/0010/14", "Jerusalem", "Tilahun",     8, "Electrical Engineering", _GOV),

    # ── Chemical Engineering (5 students) ───────────────────────
    ("UGR/0011/14", "Kalkidan",  "Sisay",       1, "Chemical Engineering",   _GOV),
    ("UGR/0012/14", "Lidya",     "Abebe",       3, "Chemical Engineering",   _SELF),
    ("UGR/0013/14", "Marcos",    "Negash",      4, "Chemical Engineering",   _GOV),
    ("UGR/0014/14", "Nardos",    "Birhanu",     5, "Chemical Engineering",   _GOV),
    ("UGR/0015/14", "Obse",      "Tariku",      7, "Chemical Engineering",   _GOV),

    # ── Civil Engineering (5 students) ──────────────────────────
    ("UGR/0016/14", "Petros",    "Selam",       2, "Civil Engineering",      _GOV),
    ("UGR/0017/14", "Rahel",     "Yilma",       3, "Civil Engineering",      _SELF),
    ("UGR/0018/14", "Samuel",    "Habte",       5, "Civil Engineering",      _GOV),
    ("UGR/0019/14", "Tigist",    "Mekuria",     6, "Civil Engineering",      _GOV),
    ("UGR/0020/14", "Ujulu",     "Gobena",      8, "Civil Engineering",      _SELF),

    # ── Mechanical Engineering (5 students) ─────────────────────
    ("UGR/0021/14", "Veronica",  "Eshete",      1, "Mechanical Engineering", _GOV),
    ("UGR/0022/14", "Wondwossen","Aklilu",      2, "Mechanical Engineering", _GOV),
    ("UGR/0023/14", "Xavier",    "Birru",       4, "Mechanical Engineering", _SELF),
    ("UGR/0024/14", "Yared",     "Lemessa",     6, "Mechanical Engineering", _GOV),
    ("UGR/0025/14", "Zewditu",   "Asfaw",       7, "Mechanical Engineering", _GOV),

    # ── Bio Medical Engineering (5 students) ────────────────────
    ("UGR/0026/14", "Amanuel",   "Getaneh",     1, "Bio Medical Engineering", _GOV),
    ("UGR/0027/14", "Bisrat",    "Kebede",      3, "Bio Medical Engineering", _SELF),
    ("UGR/0028/14", "Christian", "Wolde",       5, "Bio Medical Engineering", _GOV),
    ("UGR/0029/14", "Daniel",    "Tamirat",     6, "Bio Medical Engineering", _GOV),
    ("UGR/0030/14", "Eleni",     "Berhanu",     8, "Bio Medical Engineering", _GOV),
]


# ══════════════════════════════════════════════════════════════
#  Officers
# ══════════════════════════════════════════════════════════════
# (staff_id, first_name, last_name, role, authorization_level, email)
# A registrar officer plus a department head — the latter is the
# *only* role allowed to override prerequisite blocks per SRS §3.5.

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
#  Runner


# ══════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════

async def _seed_terms(session: AsyncSession) -> list[AcademicTerm]:
    """
    Seed both terms of the academic year. Idempotent per term: an
    existing row (matched by term_name) is reused unchanged.
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
        print(f"✅ Seeded {new_count} prerequisite edges (catalog total: {len(existing) + new_count}).")
    else:
        print(f"⚠️  All {len(existing)} prerequisite edges already present — skipping.")


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
    Seed the per-department classroom inventory. Idempotent:
    re-running reuses any classroom whose ``name`` already exists,
    even if its capacity or owning department drifts (those are
    operational decisions the registrar makes by hand).
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
        # The staff_id contains '/' which is not legal in an email
        # local-part, so derive a slug for the email instead.
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
        print(f"✅ Seeded {new_count} instructors (catalog total: {len(by_staff_id)}).")
    else:
        print(f"⚠️  All {len(by_staff_id)} instructors already present — skipping.")
    return by_staff_id


async def _seed_instructor_assignments(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
    instructors_by_staff_id: dict[str, Instructor],
) -> None:
    """
    Seed per-term InstructorAssignments only.

    The cohort scheduling agent reads InstructorAssignment to pin a
    teacher onto every ClassScheduleSlot it emits. Course→instructor
    is **round-robin within each department**, so every instructor in
    a dept actually teaches something instead of the first instructor
    grabbing all 40 courses.

    Cohort Section rows are NOT seeded — they are created on demand by
    the AcademicSchedulingAgent when an officer hits
    ``POST /courses/officer/schedule/generate`` (the agent reads the
    term's REGISTERED students, groups by (department, semester), and
    spins up sections sized to the room inventory). Likewise the
    per-class ClassScheduleSlot rows are emitted at that time.
    """
    # Reseeding redistributes via round-robin even on an existing DB.
    # We wipe the term's assignments first so the round-robin pattern
    # actually replaces the old "first instructor wins" allocation.
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
    # Sort per-dept instructor lists by staff_id so the round-robin
    # is deterministic across reseed runs.
    for ins_list in instructors_by_dept.values():
        ins_list.sort(key=lambda i: i.instructor_id)

    # Group courses by department, sorted by code so the assignment
    # order is stable across reseed runs.
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
            # Round-robin: course #0 → instructor #0, course #1 →
            # instructor #1, course #2 → instructor #0, etc. With 2
            # instructors per department and 40 courses, each
            # instructor ends up teaching 20.
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
            # Idempotent reconcile: any denormalised admission attribute
            # that drifts from the seed spec gets fixed on a re-run.
            # Without this, rows seeded before a column existed stay
            # NULL forever and fail the curriculum / sponsorship paths.
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
        print(f"✅ Seeded {new_count} students (catalog total: {len(existing) + new_count}).")
    elif reconciled:
        print(f"↻  Reconciled department/sponsorship on {reconciled} existing students.")
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


# ══════════════════════════════════════════════════════════════
#  Track A — sample registrations & advisory verdicts
# ══════════════════════════════════════════════════════════════
# Demo data so the registration / scheduling / advisory journeys
# can be poked at via Swagger without the engineer first having to
# walk through draft → submit → pay → register manually.
#
# Three sample registrations, all under "Fall 2026":
#   - UGR/0001/14 (semester 1) REGISTERED with two CS courses
#       (sponsorship: SELF_SPONSORED, payment_reference seeded)
#   - UGR/0005/14 (semester 2) draft REGISTRATION_OPEN with one CS
#       course (no payment yet — useful for testing the
#       PAYMENT_HOLD branch via the API)
#   - UGR/0017/14 (semester 5) REGISTERED with one CS course;
#       carries one LOW-risk AdvisoryRecommendation so the officer
#       queue tests have a non-HIGH baseline to filter against.

# (student_id, courses, status, sponsorship, payment_reference, advisory)
# All sample students are referenced by UGR id; the courses must
# belong to that student's department + current semester.
#
#   UGR/0001/14 → SE,    sem 1 → SE101, SE102 (REGISTERED)
#   UGR/0007/14 → EE,    sem 2 → EE201      (RegistrationOpen draft)
#   UGR/0019/14 → CE,    sem 6 → CE601      (REGISTERED, LOW-risk advisory)
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
    # ── First-semester cohort: every sem-1 student fully registered
    #    in their department's 4-course semester-1 curriculum so the
    #    AcademicSchedulingAgent has one (department, semester=1)
    #    cohort per department to allocate. All four are government-
    #    sponsored (matches Student.sponsorship_type), so they don't
    #    need a payment_reference — the cost-sharing form is the
    #    payment surrogate for these students.
    {
        "student_id": "UGR/0006/14",          # Electrical Engineering
        "course_codes": ["EE101", "EE102", "EE103", "EE104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0011/14",          # Chemical Engineering
        "course_codes": ["ChE101", "ChE102", "ChE103", "ChE104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0021/14",          # Mechanical Engineering
        "course_codes": ["ME101", "ME102", "ME103", "ME104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
    {
        "student_id": "UGR/0026/14",          # Bio Medical Engineering
        "course_codes": ["BME101", "BME102", "BME103", "BME104"],
        "status": RegistrationStatus.REGISTERED,
        "sponsorship": SponsorshipType.GOVERNMENT,
        "payment_reference": None,
        "advisory": None,
    },
]


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
            id=_uid("registration", term.term_name, term.phase.value, sample["student_id"]),
            student_id=student.id,
            term_id=term.id,
            status=sample["status"],
            sponsorship_type=sample["sponsorship"],
            payment_reference=sample["payment_reference"],
        )
        session.add(reg)
        await session.flush()
        new_regs += 1

        # Initial-state history row, mirroring what the service does.
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
                        str(courses_by_code[c].id) for c in sample["course_codes"]
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
            f"✅ Seeded {new_regs} registrations, {new_links} registration "
            f"courses, {new_history} status-history rows."
        )
    else:
        print("⚠️  Sample registrations already present — skipping.")


# ── Bulk SE-semester-1 cohort ──────────────────────────────────
#
# 70 fresh students all enrolled in Software Engineering / semester 1
# / REGISTERED in the open term, each carrying the full 4-course
# SE-sem1 curriculum (SE101–SE104). The Software Engineering /
# semester-1 cohort then has enough volume that the
# AcademicSchedulingAgent's room-capacity split actually does work —
# the largest seeded room is FBE-12/14 at 80 seats, so all 70 fit
# in one cohort A. Drop the largest-room capacity in the agent's
# inventory below 70 to exercise the spill-into-cohort-B branch.
#
# IDs run UGR/0100/14 .. UGR/0169/14 so they don't collide with the
# named demo students (UGR/0001..0030/14) or Yohannes (UGR/9999/14).
# Names are programmatic ("Cohort101 SeSem1Student") because the
# point of this block is volume, not realism — the named students
# remain the source of truth for any test that asserts on a
# specific person.

SE_SEM1_BULK_COUNT = 70
SE_SEM1_BULK_START_SEQ = 100      # → UGR/0100/14 .. UGR/0169/14
SE_SEM1_BULK_COURSES = ["SE101", "SE102", "SE103", "SE104"]


async def _seed_bulk_se_sem1_cohort(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
) -> None:
    """
    Seed ``SE_SEM1_BULK_COUNT`` Software-Engineering, semester-1,
    REGISTERED students for ``term``. Idempotent per-student: a
    student row whose student_id already exists is skipped, and a
    registration that already exists for (student, term) is reused.
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

        # Mix sponsorship — every third student is self-sponsored so
        # both payment paths (cost-sharing form vs. payment-callback)
        # have non-trivial coverage.
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

        # Idempotent: skip if a registration already exists for this
        # (student, term) pair.
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
            id=_uid("registration", term.term_name, term.phase.value, student_id_str),
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

        # Initial-state history row (None → REGISTERED) so the audit
        # trail is consistent with what the service would produce.
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


# ── Bulk SE upper-year cohorts (years 2–5) ─────────────────────
#
# Fills out the SE pyramid: 60 students per year level beyond first
# year, each fully registered for that year's Fall-semester
# curriculum in the open term. Realistic batch years are used so
# UGR ids encode "when did you start" — a year-2 student is in
# batch 13 (started one year before the current intake), a year-5
# in batch 10. Each batch year owns its own sequence 0001-0060.
#
#   Year 2 → Fall = semester 3 → batch 13 → UGR/0001/13 .. UGR/0060/13
#   Year 3 → Fall = semester 5 → batch 12 → UGR/0001/12 .. UGR/0060/12
#   Year 4 → Fall = semester 7 → batch 11 → UGR/0001/11 .. UGR/0060/11
#   Year 5 → Fall = semester 9 → batch 10 → UGR/0001/10 .. UGR/0060/10
#
# Total new students: 4 × 60 = 240. Combined with the 72 SE-sem1
# students already seeded, the Software Engineering pyramid then
# holds 312 registered students across 5 year levels.

# (batch_year, target_semester, count)
SE_BULK_UPPER_COHORTS: list[tuple[int, int, int]] = [
    (13, 3, 60),   # Year 2
    (12, 5, 60),   # Year 3
    (11, 7, 60),   # Year 4
    (10, 9, 60),   # Year 5
]


async def _seed_bulk_se_upper_year_cohorts(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
) -> None:
    """
    Seed 60 fully-registered SE students at each of semesters 3, 5,
    7, and 9 (years 2–5). Idempotent per-student: a student whose
    student_id already exists is skipped, and a registration that
    already exists for (student, term) is reused.

    Sponsorship is mixed (~33% self-sponsored, ~67% government)
    matching the SE-sem1 bulk seed so the cost-sharing and bursar
    paths have non-trivial coverage at every year level.
    """
    new_users = 0
    new_regs = 0
    new_links = 0

    for batch_year, semester, count in SE_BULK_UPPER_COHORTS:
        # Build the per-(SE, semester) curriculum: SE<sem><01-04>.
        course_codes = [
            f"SE{semester}01", f"SE{semester}02",
            f"SE{semester}03", f"SE{semester}04",
        ]
        # If any expected course is missing the seed is in an
        # inconsistent state — fail loudly rather than silently
        # producing empty registrations.
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

            # Idempotent: skip if a registration already exists for
            # this (student, term).
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
                id=_uid("registration", term.term_name, term.phase.value, student_id_str),
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
            f"students, {new_regs} registrations, {new_links} course "
            "links."
        )
    else:
        print("⚠️  Bulk SE upper-year cohorts already present — skipping.")


# ══════════════════════════════════════════════════════════════
#  Grades — academic history backing the advisory consult flow
# ══════════════════════════════════════════════════════════════
# The Academic Advisory Agent's demand-driven consult endpoints
# resolve a student's CGPA + completed-course set from the Grade
# ledger. To make the consult flow demoable end-to-end we backfill
# AUTHORISED grades for every prior semester of every seeded student
# whose ``current_semester`` is greater than 1.
#
# The grade for a given (student, course) is deterministic from the
# student id + course code so re-running the seed produces the same
# CGPA every time. The distribution is biased mildly toward B's so
# the average student lands around CGPA 3.0 — comfortably above the
# Warning floor (2.0) but not so high that everyone looks like a
# Distinction candidate.

# Cycle of letter grades used by the deterministic per-student
# distribution. Skipping I, NG, W, DO, P because they are
# administrative marks that don't count toward CGPA (Senate Art
# 90.7.4 / 90.7.6) — irrelevant for the advisory baseline. A+ is
# included so Track C standing tests can hit the "top of scale" path.
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

    Track-B-aligned shape: status=AUTHORISED, instructor entered,
    officer authorised, grade_points cached. Track B's grade-entry
    pipeline will use the same fields when it ships.
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
    # Anchor every seeded grade to the earliest term so completed
    # semesters predate the currently-open registration term.
    history_term = min(terms, key=lambda t: t.start_date)

    existing = (await session.execute(select(Grade))).scalars().all()
    existing_pairs = {(g.student_id, g.course_id, g.term_id) for g in existing}

    new_count = 0
    for student in students:
        if student.current_semester <= 1:
            continue   # nothing to backfill — they have no prior terms
        if not student.department:
            continue   # legacy row without denormalised department
        for sem in range(1, student.current_semester):
            # Use SLOT 1 only — backfilling all 4 slots × every prior
            # semester explodes the row count and a single course per
            # semester is enough to give the agent a CGPA + a few
            # completed courses to reason against.
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
                # Roughly map letters to underlying scores per AAU
                # Senate Art 90.1 cutoffs so Track B's anomaly
                # detector has plausible numeric_score data and the
                # numeric → letter round-trip is consistent.
                numeric = {
                    GradeLetter.A_PLUS:   95.0,   # [90, 100]
                    GradeLetter.A:        86.0,   # [83, 90)
                    GradeLetter.A_MINUS:  81.0,   # [80, 83)
                    GradeLetter.B_PLUS:   77.0,   # [75, 80)
                    GradeLetter.B:        71.0,   # [68, 75)
                    GradeLetter.B_MINUS:  66.0,   # [65, 68)
                    GradeLetter.C_PLUS:   62.0,   # [60, 65)
                    GradeLetter.C:        55.0,   # [50, 60)
                    GradeLetter.C_MINUS:  47.0,   # [45, 50)
                    GradeLetter.D:        42.0,   # [40, 45)
                    GradeLetter.F:        30.0,   # < 40
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


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        terms = await _seed_terms(session)
        courses_by_code = await _seed_courses(session)
        await _seed_prerequisites(session, courses_by_code)
        await _seed_classrooms(session)
        instructors = await _seed_instructors(session)
        # Each term gets its own offerings + sections — a student
        # registered in Fall 2026 cannot accidentally sit in a Spring
        # 2027 section.
        for term in terms:
            await _seed_instructor_assignments(
                session, term, courses_by_code, instructors,
            )
        await _seed_students(session)
        await _seed_officers(session)
        # Sample registrations live under the currently-open term so
        # the demo data is reachable from /courses/me/* without an
        # officer first opening a window.
        open_term = next((t for t in terms if t.is_open), terms[0])
        await _seed_registrations(session, open_term, courses_by_code)
        await _seed_bulk_se_sem1_cohort(session, open_term, courses_by_code)
        await _seed_bulk_se_upper_year_cohorts(
            session, open_term, courses_by_code,
        )
        # Backfill prior-semester grades so the advisory consult
        # endpoints have CGPA + completed-course history to reason
        # over without the caller providing anything.
        await _seed_grades(session, terms, courses_by_code)

    await engine.dispose()
    print("\n🎉 Course Management seed complete (Phase 0 + Track A samples).")


if __name__ == "__main__":
    asyncio.run(seed())
