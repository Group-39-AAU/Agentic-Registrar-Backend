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
from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, AdvisoryRecommendation, Course, CoursePrerequisite,
    Instructor, InstructorAssignment, Registration, RegistrationCourse,
    RegistrationStatusHistory, Student, CourseManagementOfficer,
)
from app.shared.enums import (
    EnrollmentStatus, OfficerRole, RegistrationStatus, RiskStatus,
    SponsorshipType, UserRole,
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

# Two academic terms per year (one academic year = Fall + Spring).
# Fall is opened by default so registration is testable out of the box;
# Spring stays closed so the officer can exercise the open/close
# endpoint without interfering with the in-progress Fall registration.
TERMS = [
    {
        "id": _uid("term", "fall-2026"),
        "term_name": "Fall 2026",
        "start_date": date(2026, 9, 1),
        "end_date": date(2027, 1, 31),
        "is_open": True,
        "description": "Fall semester of the 2026/2027 academic year.",
    },
    {
        "id": _uid("term", "spring-2027"),
        "term_name": "Spring 2027",
        "start_date": date(2027, 2, 1),
        "end_date": date(2027, 6, 30),
        "is_open": False,
        "description": "Spring semester of the 2026/2027 academic year.",
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


# Per-course classroom inventory was previously seeded here as
# OFFERING_CAPACITY / SECTIONS_PER_COURSE / TIME_SLOTS / ROOMS, but
# none of that survives the cohort migration: rooms + time slots are
# the AcademicSchedulingAgent's responsibility, and section count is
# determined at allocation time by enrolment volume vs. room size.


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
                select(AcademicTerm).where(AcademicTerm.term_name == spec["term_name"])
            )
        ).scalar_one_or_none()
        if existing:
            print(f"⚠️  Academic term '{spec['term_name']}' already exists — skipping.")
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
    teacher onto every ClassScheduleSlot it emits. Each (course, term)
    pair gets the first instructor in the course's department.

    Cohort Section rows are NOT seeded — they are created on demand by
    the AcademicSchedulingAgent when an officer hits
    ``POST /courses/officer/schedule/generate`` (the agent reads the
    term's REGISTERED students, groups by (department, semester), and
    spins up sections sized to the room inventory). Likewise the
    per-class ClassScheduleSlot rows are emitted at that time.
    """
    instructors_by_dept: dict[str, list[Instructor]] = {}
    for ins in instructors_by_staff_id.values():
        instructors_by_dept.setdefault(ins.department, []).append(ins)

    assignment_count = 0
    for code, course in courses_by_code.items():
        # Pin the department's first instructor as the canonical
        # teacher for this (course, term). The scheduling agent reads
        # InstructorAssignment to populate ClassScheduleSlot.
        dept_instructors = instructors_by_dept.get(course.department, [])
        instructor = dept_instructors[0] if dept_instructors else None
        if instructor is None:
            continue
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
                        "assn", term.term_name,
                        instructor.instructor_id, code,
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
            id=_uid("registration", term.term_name, sample["student_id"]),
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
            id=_uid("registration", term.term_name, student_id_str),
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


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        terms = await _seed_terms(session)
        courses_by_code = await _seed_courses(session)
        await _seed_prerequisites(session, courses_by_code)
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

    await engine.dispose()
    print("\n🎉 Course Management seed complete (Phase 0 + Track A samples).")


if __name__ == "__main__":
    asyncio.run(seed())
