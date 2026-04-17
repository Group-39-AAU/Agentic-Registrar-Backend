"""
Ranking Test Seed Script — Standalone, Reproducible.

Creates diverse test data for end-to-end ranking agent verification.
This script is idempotent: it checks for a sentinel email before seeding.

Run:
    cd /path/to/project
    source venv/bin/activate
    python scripts/seed_ranking_test.py

Creates:
    - 20 test students (users)
    - 20 MoE student records with varied scores
    - 20 undergraduate applications at UAT_COMPLETED status
    - 20 completed UAT records with varied scores
    - Status history entries for each application

Mix:
    - 12 self-sponsored  (6 Natural, 6 Social) with program preferences
    - 8  government       (4 Natural, 4 Social)
    - Scores range from low to high to test cutoffs and capacity limits
"""

import asyncio
import uuid
import random

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.moe.models import MoeStudentRecord
from app.modules.programs.models import AcademicProgram
from app.modules.testing_center.models import UATRecord
from app.modules.undergraduate.models import (
    ApplicationStatusHistory,
    UndergraduateAdmissionTerm,
    UndergraduateApplication,
)
from app.shared.enums import (
    ApplicationStatus,
    PaymentStatus,
    SponsorshipType,
    StreamType,
    UserRole,
)

DATABASE_URL = str(settings.DATABASE_URL)

SENTINEL_EMAIL = "ranking_test_student_01@aau.edu.et"

# ══════════════════════════════════════════════════════════════
#  Test Students
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════

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
            print("❌ No programs found! Run `python scripts/seed.py` first.")
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
            print("❌ No open undergraduate admission term found! Run `python scripts/seed.py` first.")
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


if __name__ == "__main__":
    asyncio.run(seed_ranking_test())
