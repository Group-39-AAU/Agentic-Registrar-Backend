"""
Undergraduate Admission Seed Script — merged baseline + ranking test data.

Combines the former scripts/seed.py (programs, MoE samples, stream quotas,
admission term, officer account) and scripts/seed_ranking_test.py (200 diverse
test applicants at UAT_COMPLETED) into a single entry point. The two phases
run sequentially and produce the same DB state and console output as running
the two original scripts back-to-back.

Usage:
    cd /path/to/project
    source venv/bin/activate
    python scripts/seed_undergraduate_admission.py

Seeds:
    Phase 1 — Baseline
        1. Academic Programs (15 programs — 9 Natural, 6 Social)
        2. MoE Student Records (sample Grade 12 results for testing)
        3. Stream Quotas (Natural: 2500, Social: 2500)
        4. Undergraduate Admission Term (2026/27, open)
        5. Default registrar officer account

    Phase 2 — Ranking Test Data
        - 200 test students (users), MoE records, applications at
          UAT_COMPLETED, UAT records, status history entries.
        - 20 hand-crafted students + 180 procedurally generated with
          reproducible randomness (random.Random(20260524)).
"""

import asyncio
import random
import uuid
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.modules.auth.models import User
from app.modules.moe.models import MoeStudentRecord
from app.modules.programs.models import AcademicProgram
from app.modules.testing_center.models import UATRecord
from app.modules.undergraduate.models import (
    ApplicationStatusHistory,
    UndergraduateAdmissionTerm,
    UndergraduateApplication,
)
from app.modules.undergraduate.ranking.models import StreamQuota
from app.shared.enums import (
    ApplicationStatus,
    PaymentStatus,
    SponsorshipType,
    StreamType,
    UserRole,
)

DATABASE_URL = str(settings.DATABASE_URL)


# ══════════════════════════════════════════════════════════════
#  Phase 1 — Baseline data (formerly scripts/seed.py)
# ══════════════════════════════════════════════════════════════

PROGRAMS = [
    # ── Natural Science ───────────────────────────────
    {"code": "CS", "name": "Computer Science", "department": "Computer Science", "stream": StreamType.NATURAL, "cut_off_score": 550.0, "max_capacity": 120},
    {"code": "SE", "name": "Software Engineering", "department": "Software Engineering", "stream": StreamType.NATURAL, "cut_off_score": 560.0, "max_capacity": 100},
    {"code": "EE", "name": "Electrical Engineering", "department": "Electrical & Computer Engineering", "stream": StreamType.NATURAL, "cut_off_score": 540.0, "max_capacity": 80},
    {"code": "ME", "name": "Mechanical Engineering", "department": "Mechanical Engineering", "stream": StreamType.NATURAL, "cut_off_score": 520.0, "max_capacity": 90},
    # Course-management engineering programs (the 6 covered by course_course seed)
    {"code": "ChE", "name": "Chemical Engineering", "department": "Chemical Engineering", "stream": StreamType.NATURAL, "cut_off_score": 510.0, "max_capacity": 70},
    {"code": "CE", "name": "Civil Engineering", "department": "Civil Engineering", "stream": StreamType.NATURAL, "cut_off_score": 515.0, "max_capacity": 90},
    {"code": "BME", "name": "Bio Medical Engineering", "department": "Bio Medical Engineering", "stream": StreamType.NATURAL, "cut_off_score": 540.0, "max_capacity": 60},
    {"code": "MED", "name": "Medicine", "department": "Medical Sciences", "stream": StreamType.NATURAL, "cut_off_score": 600.0, "max_capacity": 60},
    {"code": "BIO", "name": "Biology", "department": "Biological Sciences", "stream": StreamType.NATURAL, "cut_off_score": 480.0, "max_capacity": 100},
    # ── Social Science ────────────────────────────────
    {"code": "LAW", "name": "Law", "department": "Law", "stream": StreamType.SOCIAL, "cut_off_score": 530.0, "max_capacity": 80},
    {"code": "ECON", "name": "Economics", "department": "Economics", "stream": StreamType.SOCIAL, "cut_off_score": 500.0, "max_capacity": 100},
    {"code": "PSYCH", "name": "Psychology", "department": "Psychology", "stream": StreamType.SOCIAL, "cut_off_score": 470.0, "max_capacity": 80},
    {"code": "ACCT", "name": "Accounting & Finance", "department": "Accounting & Finance", "stream": StreamType.SOCIAL, "cut_off_score": 510.0, "max_capacity": 90},
    {"code": "MGMT", "name": "Management", "department": "Management", "stream": StreamType.SOCIAL, "cut_off_score": 490.0, "max_capacity": 100},
    {"code": "POLS", "name": "Political Science", "department": "Political Science & International Relations", "stream": StreamType.SOCIAL, "cut_off_score": 480.0, "max_capacity": 70},
]


MOE_RECORDS = [
    {
        "admission_number": "2955397", "full_name": "Abenezer Seifu", "exam_year": 2024, "stream": StreamType.NATURAL,
        "subjects": {"Mathematics": 92, "Physics": 85, "Chemistry": 78, "Biology": 80, "English": 75, "Aptitude": 88},
        "total_score": 498.0,
    },
    {
        "admission_number": "3102845", "full_name": "Sara Tadesse", "exam_year": 2024, "stream": StreamType.NATURAL,
        "subjects": {"Mathematics": 95, "Physics": 90, "Chemistry": 88, "Biology": 85, "English": 82, "Aptitude": 93},
        "total_score": 533.0,
    },
    {
        "admission_number": "2871034", "full_name": "Dawit Haile", "exam_year": 2024, "stream": StreamType.SOCIAL,
        "subjects": {"History": 88, "Geography": 82, "Economics": 90, "Civics": 85, "English": 78, "Aptitude": 86},
        "total_score": 509.0,
    },
    {
        "admission_number": "3045612", "full_name": "Meron Alemu", "exam_year": 2024, "stream": StreamType.SOCIAL,
        "subjects": {"History": 75, "Geography": 70, "Economics": 80, "Civics": 72, "English": 68, "Aptitude": 74},
        "total_score": 439.0,
    },
    {
        "admission_number": "3198203", "full_name": "Yonas Bekele", "exam_year": 2024, "stream": StreamType.NATURAL,
        "subjects": {"Mathematics": 80, "Physics": 75, "Chemistry": 70, "Biology": 72, "English": 65, "Aptitude": 78},
        "total_score": 440.0,
    },
]


STREAM_QUOTAS = [
    {"stream": StreamType.NATURAL, "max_capacity": 2500},
    {"stream": StreamType.SOCIAL, "max_capacity": 2500},
]


async def seed():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # ── Seed Admission Terms ──
        existing_terms = (await session.execute(select(UndergraduateAdmissionTerm))).scalars().all()
        if existing_terms:
            active_term = existing_terms[0]
            print(f"⚠️  {len(existing_terms)} admission terms already exist — skipping term seeding.")
        else:
            active_term = UndergraduateAdmissionTerm(
                id=uuid.uuid4(),
                term_name="2026/27",
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
                is_open=True,
                description="Primary intake for 2026/27",
            )
            session.add(active_term)
            await session.commit()
            print("✅ Seeded undergraduate admission term: 2026/27.")

        # ── Seed Programs ──
        existing = (await session.execute(select(AcademicProgram))).scalars().all()
        if existing:
            print(f"⚠️  {len(existing)} programs already exist — skipping program seeding.")
        else:
            for p in PROGRAMS:
                session.add(AcademicProgram(id=uuid.uuid4(), **p))
            await session.commit()
            print(f"✅ Seeded {len(PROGRAMS)} academic programs.")

        # ── Seed MoE Records ──
        existing_moe = (await session.execute(select(MoeStudentRecord))).scalars().all()
        if existing_moe:
            print(f"⚠️  {len(existing_moe)} MoE records already exist — skipping MoE seeding.")
        else:
            for r in MOE_RECORDS:
                session.add(MoeStudentRecord(id=uuid.uuid4(), **r))
            await session.commit()
            print(f"✅ Seeded {len(MOE_RECORDS)} MoE student records.")

        # ── Seed Stream Quotas ──
        existing_quotas = (await session.execute(select(StreamQuota))).scalars().all()
        if existing_quotas:
            print(f"⚠️  {len(existing_quotas)} stream quotas already exist — skipping quota seeding.")
        else:
            for q in STREAM_QUOTAS:
                session.add(
                    StreamQuota(
                        id=uuid.uuid4(),
                        stream=q["stream"],
                        max_capacity=q["max_capacity"],
                        admission_term_id=active_term.id,
                    )
                )
            await session.commit()
            print(f"✅ Seeded {len(STREAM_QUOTAS)} stream quotas.")

        # ── Seed Officer Account ──
        officer = (await session.execute(
            select(User).where(User.email == "officer@aau.edu.et")
        )).scalar_one_or_none()

        if officer:
            print("⚠️  Officer account already exists.")
        else:
            session.add(User(
                id=uuid.uuid4(),
                email="officer@aau.edu.et",
                first_name="Registrar",
                last_name="Officer",
                hashed_password=hash_password("password123"),
                role=UserRole.REGISTRAR_OFFICER,
                is_active=True,
            ))
            await session.commit()
            print("✅ Seeded default officer account (officer@aau.edu.et).")

    await engine.dispose()
    print("\n🎉 Seeding complete!")


# ══════════════════════════════════════════════════════════════
#  Phase 2 — Ranking test data (formerly scripts/seed_ranking_test.py)
# ══════════════════════════════════════════════════════════════

SENTINEL_EMAIL = "ranking_test_student_01@aau.edu.et"

# Each entry: (email, first_name, last_name, admission_number, stream,
#              sponsorship, grade12_total (out of 600), uat_score (out of 100),
#              subjects_dict)
TEST_STUDENTS = [
    # ── Self-Sponsored NATURAL (6) — varied scores, high to low ──
    ("ranking_test_student_01@aau.edu.et", "Amanuel", "Gebremariam", "9000001", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     580, 95, {"Mathematics": 98, "Physics": 97, "Chemistry": 96, "Biology": 95, "English": 97, "Aptitude": 97}),
    ("ranking_test_student_02@aau.edu.et", "Bethlehem", "Tessema", "9000002", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     552, 88, {"Mathematics": 95, "Physics": 92, "Chemistry": 90, "Biology": 88, "English": 92, "Aptitude": 95}),
    ("ranking_test_student_03@aau.edu.et", "Caleb", "Worku", "9000003", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     510, 82, {"Mathematics": 88, "Physics": 85, "Chemistry": 82, "Biology": 84, "English": 85, "Aptitude": 86}),
    ("ranking_test_student_04@aau.edu.et", "Dagmawit", "Assefa", "9000004", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     475, 76, {"Mathematics": 80, "Physics": 78, "Chemistry": 79, "Biology": 78, "English": 80, "Aptitude": 80}),
    ("ranking_test_student_05@aau.edu.et", "Elias", "Mekonnen", "9000005", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     420, 70, {"Mathematics": 72, "Physics": 68, "Chemistry": 70, "Biology": 70, "English": 70, "Aptitude": 70}),
    ("ranking_test_student_06@aau.edu.et", "Feven", "Tadesse", "9000006", StreamType.NATURAL, SponsorshipType.SELF_SPONSORED,
     380, 60, {"Mathematics": 65, "Physics": 62, "Chemistry": 63, "Biology": 64, "English": 63, "Aptitude": 63}),

    # ── Self-Sponsored SOCIAL (6) — varied scores ──
    ("ranking_test_student_07@aau.edu.et", "Girma", "Hailemariam", "9000007", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     570, 92, {"History": 96, "Geography": 94, "Economics": 97, "Civics": 95, "English": 94, "Aptitude": 94}),
    ("ranking_test_student_08@aau.edu.et", "Hana", "Kebede", "9000008", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     540, 85, {"History": 92, "Geography": 90, "Economics": 88, "Civics": 90, "English": 88, "Aptitude": 92}),
    ("ranking_test_student_09@aau.edu.et", "Ibrahim", "Ahmed", "9000009", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     500, 80, {"History": 85, "Geography": 82, "Economics": 84, "Civics": 83, "English": 82, "Aptitude": 84}),
    ("ranking_test_student_10@aau.edu.et", "Jemila", "Mohammed", "9000010", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     460, 72, {"History": 78, "Geography": 75, "Economics": 77, "Civics": 76, "English": 78, "Aptitude": 76}),
    ("ranking_test_student_11@aau.edu.et", "Kidus", "Solomon", "9000011", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     430, 65, {"History": 72, "Geography": 70, "Economics": 73, "Civics": 71, "English": 72, "Aptitude": 72}),
    ("ranking_test_student_12@aau.edu.et", "Liya", "Bekele", "9000012", StreamType.SOCIAL, SponsorshipType.SELF_SPONSORED,
     390, 58, {"History": 66, "Geography": 64, "Economics": 65, "Civics": 65, "English": 65, "Aptitude": 65}),

    # ── Government NATURAL (4) — varied scores ──
    ("ranking_test_student_13@aau.edu.et", "Nahom", "Tesfaye", "9000013", StreamType.NATURAL, SponsorshipType.GOVERNMENT,
     590, 94, {"Mathematics": 99, "Physics": 98, "Chemistry": 98, "Biology": 98, "English": 99, "Aptitude": 98}),
    ("ranking_test_student_14@aau.edu.et", "Olana", "Debebe", "9000014", StreamType.NATURAL, SponsorshipType.GOVERNMENT,
     530, 83, {"Mathematics": 90, "Physics": 88, "Chemistry": 87, "Biology": 88, "English": 88, "Aptitude": 89}),
    ("ranking_test_student_15@aau.edu.et", "Paulos", "Girma", "9000015", StreamType.NATURAL, SponsorshipType.GOVERNMENT,
     480, 75, {"Mathematics": 82, "Physics": 80, "Chemistry": 78, "Biology": 79, "English": 80, "Aptitude": 81}),
    ("ranking_test_student_16@aau.edu.et", "Ruth", "Abebe", "9000016", StreamType.NATURAL, SponsorshipType.GOVERNMENT,
     410, 68, {"Mathematics": 70, "Physics": 66, "Chemistry": 68, "Biology": 68, "English": 69, "Aptitude": 69}),

    # ── Government SOCIAL (4) — varied scores ──
    ("ranking_test_student_17@aau.edu.et", "Samuel", "Hailu", "9000017", StreamType.SOCIAL, SponsorshipType.GOVERNMENT,
     560, 90, {"History": 95, "Geography": 92, "Economics": 94, "Civics": 93, "English": 93, "Aptitude": 93}),
    ("ranking_test_student_18@aau.edu.et", "Tigist", "Alemu", "9000018", StreamType.SOCIAL, SponsorshipType.GOVERNMENT,
     520, 81, {"History": 88, "Geography": 86, "Economics": 87, "Civics": 86, "English": 87, "Aptitude": 86}),
    ("ranking_test_student_19@aau.edu.et", "Urgessa", "Biru", "9000019", StreamType.SOCIAL, SponsorshipType.GOVERNMENT,
     470, 73, {"History": 80, "Geography": 78, "Economics": 79, "Civics": 78, "English": 78, "Aptitude": 77}),
    ("ranking_test_student_20@aau.edu.et", "Winta", "Negash", "9000020", StreamType.SOCIAL, SponsorshipType.GOVERNMENT,
     400, 62, {"History": 68, "Geography": 66, "Economics": 67, "Civics": 66, "English": 67, "Aptitude": 66}),
]

# Program preferences for self-sponsored students (indices into NATURAL / SOCIAL programs)
# These will be resolved to actual program IDs at runtime.
# Format: (choice_1_code, choice_2_code, choice_3_code)
SELF_SPONSORED_PREFS = {
    # Natural self-sponsored
    "9000001": ("SE", "CS", "MED"),    # Top scorer — wants SE first
    "9000002": ("CS", "SE", "EE"),     # Strong — wants CS
    "9000003": ("MED", "CS", "SE"),    # Mid — reaches for MED
    "9000004": ("EE", "ME", "BIO"),    # Lower — engineering choices
    "9000005": ("CS", "BIO", "ME"),    # Low — CS is a reach
    "9000006": ("BIO", "ME", "EE"),    # Lowest natural — safer picks

    # Social self-sponsored
    "9000007": ("LAW", "ECON", "ACCT"),    # Top social — wants Law
    "9000008": ("ECON", "LAW", "MGMT"),    # Strong — Economics
    "9000009": ("ACCT", "ECON", "MGMT"),   # Mid — Accounting
    "9000010": ("MGMT", "PSYCH", "POLS"),  # Lower — Management
    "9000011": ("PSYCH", "POLS", "MGMT"),  # Low — Psychology
    "9000012": ("POLS", "MGMT", "PSYCH"),  # Lowest — Political Science
}


# The 20 hand-crafted students above cover the canonical happy/edge
# cases. The block below extends the roster to 200 with reproducible
# variety so the ranking pipeline gets stressed across many programs,
# streams, score bands, and capacities.

# Program code pools — must match codes seeded by Phase 1.
_NATURAL_PROGRAM_CODES = ["CS", "SE", "EE", "ME", "ChE", "CE", "BME", "MED", "BIO"]
_SOCIAL_PROGRAM_CODES = ["LAW", "ECON", "PSYCH", "ACCT", "MGMT", "POLS"]

_FIRST_NAMES = [
    "Abel", "Abenezer", "Abeba", "Addis", "Adugna", "Aklilu", "Alemayehu",
    "Almaz", "Amare", "Amha", "Aregawi", "Aron", "Aster", "Ayele", "Bekele",
    "Belaynesh", "Bemnet", "Berhanu", "Beza", "Birhane", "Birtukan", "Bisrat",
    "Dagim", "Daniel", "Dawit", "Desta", "Eden", "Eskedar", "Eyob", "Fasika",
    "Filagot", "Frehiwot", "Fitsum", "Genet", "Getachew", "Gizachew", "Helen",
    "Helina", "Henok", "Hewan", "Hilina", "Hiwot", "Israel", "Iyasu", "Kalkidan",
    "Kaleb", "Lemlem", "Lidya", "Mahder", "Mahlet", "Mathias", "Mebrat", "Mehari",
    "Mekdes", "Melat", "Melaku", "Mengistu", "Meron", "Meskerem", "Mikiyas",
    "Mulu", "Muluken", "Nardos", "Natnael", "Nebiat", "Nuhamin", "Rahel",
    "Robel", "Robera", "Roman", "Samrawit", "Selam", "Selamawit", "Semira",
    "Sintayehu", "Sirak", "Solomon", "Solyana", "Surafel", "Tarekegn",
    "Tewodros", "Tinsae", "Tomas", "Tsadkan", "Yeshi", "Yidnekachew", "Yohannes",
    "Yonas", "Yordanos", "Yosef", "Zara", "Zewdie", "Zinash",
]

_LAST_NAMES = [
    "Abebe", "Abera", "Abraham", "Abrha", "Aklilu", "Alemayehu", "Alemu",
    "Amare", "Asfaw", "Assefa", "Ayele", "Bekele", "Belay", "Berhane", "Berhe",
    "Birhanu", "Bogale", "Demissie", "Desta", "Eshetu", "Fekadu", "Gebre",
    "Gebremariam", "Gebrehiwot", "Genene", "Getachew", "Girma", "Habte",
    "Hailu", "Haftom", "Kassa", "Kebede", "Lemma", "Mamo", "Mekonnen",
    "Mengistu", "Molla", "Mulugeta", "Nigatu", "Reda", "Shiferaw", "Solomon",
    "Tadesse", "Taye", "Tekle", "Teklemariam", "Teshome", "Tilahun", "Tola",
    "Tsegaye", "Wolde", "Woldemariam", "Workneh", "Worku", "Yohannes",
    "Zelalem", "Zewde", "Zewdu",
]


def _generate_extra_students(count: int, start_index: int) -> tuple[list, dict]:
    """
    Build `count` extra (student, preferences) entries with reproducible
    randomness. Returns (students_list, prefs_dict) ready to be appended to
    TEST_STUDENTS / SELF_SPONSORED_PREFS.

    The fixed seed (20260524) ensures the roster is identical across runs,
    so test expectations remain stable.
    """
    rng = random.Random(20260524)
    extras: list = []
    prefs_map: dict = {}

    for offset in range(count):
        idx = start_index + offset                       # 21, 22, …
        adm = f"9{idx:06d}"                              # 9000021, 9000022, …
        email = f"ranking_test_student_{idx:03d}@aau.edu.et"
        first_name = rng.choice(_FIRST_NAMES)
        last_name = rng.choice(_LAST_NAMES)

        stream = rng.choices(
            [StreamType.NATURAL, StreamType.SOCIAL],
            weights=[60, 40],
        )[0]
        sponsorship = rng.choices(
            [SponsorshipType.SELF_SPONSORED, SponsorshipType.GOVERNMENT],
            weights=[55, 45],
        )[0]

        band = rng.choices(["high", "mid", "low"], weights=[25, 50, 25])[0]
        if band == "high":
            g12 = rng.randint(540, 600)
            uat = rng.randint(85, 100)
        elif band == "mid":
            g12 = rng.randint(420, 540)
            uat = rng.randint(65, 88)
        else:
            g12 = rng.randint(330, 430)
            uat = rng.randint(45, 70)

        # Derive subject scores around the per-subject average with jitter,
        # clamped to [40, 100].
        per_subject = g12 / 6
        subject_names = (
            ("Mathematics", "Physics", "Chemistry", "Biology", "English", "Aptitude")
            if stream == StreamType.NATURAL
            else ("History", "Geography", "Economics", "Civics", "English", "Aptitude")
        )
        subjects = {
            name: max(40, min(100, int(per_subject + rng.randint(-6, 6))))
            for name in subject_names
        }

        extras.append(
            (email, first_name, last_name, adm, stream, sponsorship, g12, uat, subjects)
        )

        if sponsorship == SponsorshipType.SELF_SPONSORED:
            pool = _NATURAL_PROGRAM_CODES if stream == StreamType.NATURAL else _SOCIAL_PROGRAM_CODES
            prefs_map[adm] = tuple(rng.sample(pool, 3))

    return extras, prefs_map


_EXTRA_STUDENTS, _EXTRA_PREFS = _generate_extra_students(count=180, start_index=21)
TEST_STUDENTS.extend(_EXTRA_STUDENTS)
SELF_SPONSORED_PREFS.update(_EXTRA_PREFS)


async def seed_ranking_test():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # ── Idempotency check ──
        existing = (await session.execute(
            select(User).where(User.email == SENTINEL_EMAIL)
        )).scalar_one_or_none()

        if existing:
            print("⚠️  Ranking test data already exists — skipping.")
            await engine.dispose()
            return

        # ── Fetch program IDs by code ──
        prog_result = await session.execute(select(AcademicProgram))
        programs = {p.code: p.id for p in prog_result.scalars().all()}
        print(f"📋 Found {len(programs)} programs: {list(programs.keys())}")

        if not programs:
            print("❌ No programs found! Run `python scripts/seed_undergraduate_admission.py` (Phase 1) first.")
            await engine.dispose()
            return

        # ── Fetch an open admission term ──
        term = (await session.execute(
            select(UndergraduateAdmissionTerm).where(
                UndergraduateAdmissionTerm.is_open == True,  # noqa: E712
                UndergraduateAdmissionTerm.is_deleted == False,  # noqa: E712
            ).order_by(UndergraduateAdmissionTerm.start_date.asc())
        )).scalars().first()
        if term is None:
            print("❌ No open undergraduate admission term found! Run `python scripts/seed_undergraduate_admission.py` (Phase 1) first.")
            await engine.dispose()
            return

        # ── Create users, MoE records, applications, UAT records ──
        users = []
        moe_records = []
        applications = []
        uat_records = []
        history_records = []

        for (
            email, first_name, last_name, adm_num, stream,
            sponsorship, grade12_total, uat_score, subjects
        ) in TEST_STUDENTS:
            user_id = uuid.uuid4()
            app_id = uuid.uuid4()
            uat_record_id = uuid.uuid4()

            # 1. User
            users.append(User(
                id=user_id,
                email=email,
                hashed_password="$2b$12$RANKING_TEST_PLACEHOLDER_HASH_NOT_FOR_LOGIN",
                first_name=first_name,
                last_name=last_name,
                role=UserRole.STUDENT,
                is_active=True,
            ))

            # 2. MoE Record
            moe_records.append(MoeStudentRecord(
                id=uuid.uuid4(),
                admission_number=adm_num,
                full_name=f"{first_name} {last_name}",
                exam_year=2024,
                stream=stream,
                subjects=subjects,
                total_score=float(grade12_total),
            ))

            # 3. Resolve program preferences (self-sponsored only)
            prefs = SELF_SPONSORED_PREFS.get(adm_num)
            p1_id = programs.get(prefs[0]) if prefs else None
            p2_id = programs.get(prefs[1]) if prefs else None
            p3_id = programs.get(prefs[2]) if prefs else None

            # 4. Application (directly at UAT_COMPLETED)
            applications.append(UndergraduateApplication(
                id=app_id,
                applicant_id=user_id,
                sponsorship_type=sponsorship,
                stream=stream,
                admission_number=adm_num,
                program_choice_1_id=p1_id,
                program_choice_2_id=p2_id,
                program_choice_3_id=p3_id,
                admission_term_id=term.id,
                current_status=ApplicationStatus.UAT_COMPLETED,
                payment_status=PaymentStatus.COMPLETED,
            ))

            # 5. UAT Record (completed)
            uat_id = f"UAT-2024-{random.randint(100000, 999999)}"
            uat_records.append(UATRecord(
                id=uat_record_id,
                uat_id=uat_id,
                application_id=app_id,
                student_name=f"{first_name} {last_name}",
                score=float(uat_score),
                is_completed=True,
            ))

            # 6. Status history
            history_records.append(ApplicationStatusHistory(
                id=uuid.uuid4(),
                application_id=app_id,
                previous_status=None,
                new_status=ApplicationStatus.UAT_COMPLETED,
                changed_by_id=user_id,
                trigger_reason="Ranking test seed — placed directly at UAT_COMPLETED",
            ))

            label = f"{'SS' if sponsorship == SponsorshipType.SELF_SPONSORED else 'GOV'}/{stream.value}"
            print(
                f"  ✅ {first_name:12s} {last_name:15s}  {label:12s}  "
                f"G12={grade12_total:3d}  UAT={uat_score:2d}  "
                f"Prefs={prefs or 'N/A'}"
            )

        # Insert in FK-safe order: users → MoE → apps → UAT + history
        session.add_all(users)
        session.add_all(moe_records)
        await session.flush()

        session.add_all(applications)
        await session.flush()

        session.add_all(uat_records)
        session.add_all(history_records)
        await session.commit()


    await engine.dispose()
    print(f"\n🎉 Seeded {len(TEST_STUDENTS)} ranking test applicants!")

    # ── Print expected score calculations ──
    print("\n📊 Expected Final Scores (for verification):")
    print(f"   {'Name':<28s} {'Category':<15s} {'G12':>4s} {'UAT':>4s} {'Final':>6s}")
    print("   " + "-" * 65)
    for (
        _email, first, last, adm, stream, spons, g12, uat, _subj
    ) in sorted(TEST_STUDENTS, key=lambda s: ((s[6] / 600) * 50 + (s[7] / 100) * 50), reverse=True):
        final = round((g12 / 600) * 50 + (uat / 100) * 50, 2)
        cat = "SELF" if spons == SponsorshipType.SELF_SPONSORED else "GOV"
        print(f"   {first + ' ' + last:<28s} {cat + '/' + stream.value:<15s} {g12:>4d} {uat:>4d} {final:>6.2f}")


# ══════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════

async def main():
    await seed()
    await seed_ranking_test()


if __name__ == "__main__":
    asyncio.run(main())
