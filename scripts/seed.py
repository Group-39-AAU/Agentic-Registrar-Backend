"""
Seed Script — Populate the database with initial data.

Usage:
    cd /path/to/project
    source venv/bin/activate
    python scripts/seed.py

Seeds:
    1. Academic Programs (12 programs — 6 Natural, 6 Social)
    2. MoE Student Records (sample Grade 12 results for testing)
    3. Stream Quotas (Natural: 2500, Social: 2500)
"""

import asyncio
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── Bootstrap the app models so SQLAlchemy knows the tables ──
from app.core.config import settings
from app.modules.programs.models import AcademicProgram
from app.modules.moe.models import MoeStudentRecord
from app.modules.undergraduate.ranking.models import StreamQuota
from app.modules.undergraduate.models import UndergraduateAdmissionTerm
from app.modules.auth.models import User
from app.core.security import hash_password
from app.shared.enums import StreamType, UserRole

DATABASE_URL = str(settings.DATABASE_URL)


# ══════════════════════════════════════════════════════════════
#  Academic Programs
# ══════════════════════════════════════════════════════════════

PROGRAMS = [
    # ── Natural Science ───────────────────────────────
    {"code": "CS", "name": "Computer Science", "department": "Computer Science", "stream": StreamType.NATURAL, "cut_off_score": 550.0, "max_capacity": 120},
    {"code": "SE", "name": "Software Engineering", "department": "Software Engineering", "stream": StreamType.NATURAL, "cut_off_score": 560.0, "max_capacity": 100},
    {"code": "EE", "name": "Electrical Engineering", "department": "Electrical & Computer Engineering", "stream": StreamType.NATURAL, "cut_off_score": 540.0, "max_capacity": 80},
    {"code": "ME", "name": "Mechanical Engineering", "department": "Mechanical Engineering", "stream": StreamType.NATURAL, "cut_off_score": 520.0, "max_capacity": 90},
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


# ══════════════════════════════════════════════════════════════
#  MoE Student Records (simulated Ministry of Education data)
# ══════════════════════════════════════════════════════════════

MOE_RECORDS = [
    {
        "admission_number": "2955397", "full_name": "Abebe Kebede", "exam_year": 2024, "stream": StreamType.NATURAL,
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


# ══════════════════════════════════════════════════════════════
#  Stream Quotas (government-sponsored capacity per stream)
# ══════════════════════════════════════════════════════════════

STREAM_QUOTAS = [
    {"stream": StreamType.NATURAL, "max_capacity": 2500},
    {"stream": StreamType.SOCIAL, "max_capacity": 2500},
]


# ══════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════

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
                term_name="Fall 2026",
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
                is_open=True,
                description="Primary intake for 2026/27",
            )
            session.add(active_term)
            await session.commit()
            print("✅ Seeded undergraduate admission term: Fall 2026.")

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


if __name__ == "__main__":
    asyncio.run(seed())
