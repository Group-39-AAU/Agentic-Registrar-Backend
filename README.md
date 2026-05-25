# Agentic Registrar Backend

AI-powered university registrar automation system built with **FastAPI**, **LangGraph**, and **PostgreSQL**. The system uses autonomous AI agents to manage core registrar workflows — from students' admission and intake through course managment, grading, and transcript generation.

## Architecture

The project follows a **modular monolith** architecture. Each module is self-contained and can be extracted into a microservice if needed. Cross-module communication uses a lightweight in-process event bus to maintain loose coupling.

```
┌─────────────────────────────────────────────────────────┐
│                      FastAPI App                        │
│                                                         │
│  ┌─────────┐ ┌──────────┐ ┌─────┐ ┌────────────────┐  │
│  │  Auth   │ │ Programs │ │ MoE │ │ Testing Center │  │
│  └─────────┘ └──────────┘ └─────┘ └────────────────┘  │
│                                                         │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Undergraduate Admission               │ │
│  │  ┌──────────┐ ┌─────────┐ ┌────────┐ ┌─────────┐ │ │
│  │  │  Intake  │ │ Ranking │ │ Review │ │ Enroll  │ │ │
│  │  │  Agent   │ │  Agent  │ │(Human) │ │  Agent  │ │ │
│  │  └──────────┘ └─────────┘ └────────┘ └─────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌──────────┐ ┌────────────────────┐                   │
│  │ Graduate │ │ Course Management  │   (Planned)       │
│  └──────────┘ └────────────────────┘                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │          Shared (enums, audit, events, ai)      │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.115+ |
| Database | PostgreSQL 16, SQLAlchemy 2 (async), Alembic |
| AI Agents | LangGraph |
| Auth | JWT (python-jose), bcrypt |
| Runtime | Python 3.10+, Uvicorn |
| Container | Docker, Docker Compose |

## Project Structure

```
agentic-registrar-backend/
├── app/
│   ├── main.py                  # App factory, model registry, router mounts
│   ├── ai/                      # Shared AI infrastructure
│   │   ├── models.py            # AIEvaluation, AIExecutionTrace
│   │   ├── ocr.py               # Certificate OCR extraction
│   │   ├── base.py              # Base agent utilities
│   │   └── tracing.py           # Agent tracing helpers
│   ├── core/                    # Framework infrastructure
│   │   ├── config.py            # Settings (env-based)
│   │   ├── dependencies.py      # FastAPI dependencies (get_current_user)
│   │   ├── exceptions.py        # Custom HTTP exceptions
│   │   ├── logging.py           # Structured logging + audit log writer
│   │   └── security.py          # JWT encode/decode, password hashing
│   ├── database/
│   │   ├── base.py              # Base, SoftDeleteBase (SQLAlchemy bases)
│   │   └── session.py           # Async engine + get_db dependency
│   ├── modules/
│   │   ├── auth/                # User registration & JWT login
│   │   ├── programs/            # Academic programs CRUD
│   │   ├── moe/                 # Ministry of Education record lookup
│   │   ├── testing_center/      # UAT score callback (event-driven)
│   │   ├── undergraduate/       # Full admission lifecycle (see module README)
│   │   ├── graduate/            # (placeholder)
│   │   └── course/              # (placeholder)
│   └── shared/
│       ├── enums/               # All enums (ApplicationStatus, UserRole, etc.)
│       ├── audit/               # SystemAuditLog model
│       └── events/              # In-process event bus (pub/sub)
├── alembic/                     # Database migration scripts
├── scripts/
│   ├── reset_db.py              # Drops all tables
│   └── seed_undergraduate_admission.py  # Seeds programs, MoE records, quotas, officer + 200 ranking applicants
├── docker-compose.yml           # PostgreSQL + App + pgAdmin
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## Getting Started

### Prerequisites

- Python 3.10+
- Docker & Docker Compose (for PostgreSQL)
- Git

### 1. Clone & Setup Environment

```bash
git clone <repo-url> && cd Agentic-Registrar-Backend

# Create virtual environment
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# Install dependencies
pip install -e ".[dev]"
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your values

### 3. Start Database with Docker

```bash
docker compose up -d db pgadmin
```

This starts:
- **PostgreSQL** on `localhost:5432`
- **pgAdmin** on `localhost:5050`

#### Connecting pgAdmin to the Database

1. Open http://localhost:5050
2. Login with `admin@registrar.com` / `admin`
3. Right-click **Servers** → **Register** → **Server**
4. **General** tab: Name = `registrar`
5. **Connection** tab:
   - Host: `db` (Docker network name)
   - Port: `5432`
   - Database: `registrar_db`
   - Username: `postgres`
   - Password: `postgres`

### 4. Run Migrations

```bash
alembic upgrade head
```

### 5. Seed the Database

```bash
# Core data (programs, MoE records, stream quotas, officer account) +
# 200 diverse test applicants for ranking verification.
python scripts/seed_undergraduate_admission.py
```

### 6. Start the Server

```bash
uvicorn app.main:app --reload
```

The API is now available at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

### Running with Docker Compose (Full Stack)

To run everything in containers:

```bash
docker compose up --build
```

## Default Accounts

| Role | Email | Password |
|---|---|---|
| Registrar Officer | `officer@aau.edu.et` | `password123` |

Register additional applicant accounts via `POST /api/v1/auth/register`.

## Database Management

```bash
# Reset database (drops all tables)
python scripts/reset_db.py

# Re-run all migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "description"

# Downgrade one migration
alembic downgrade -1
```

## API Overview

| Module | Endpoints | Description |
|---|---|---|
| Auth | 3 | Register, login, profile |
| Programs | 2 | List / get academic programs |
| MoE | 1 | Grade 12 record lookup |
| Testing Center | 2 | UAT score callback + record lookup |
| Undergraduate | 25 | Full admission lifecycle |
| Health | 1 | System health check |
| **Total** | **34** | |

All module endpoints are prefixed with `/api/v1/`.

## License

This project is developed as part of the AAU Agentic Registrar initiative.
