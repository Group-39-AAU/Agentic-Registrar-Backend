# Agentic Registrar Backend

AI-powered university registrar automation system built with FastAPI.

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 16+
- Docker & Docker Compose (optional)

### Option 1: Docker Compose (recommended)

```bash
cp .env.example .env
docker-compose up --build
```

The API will be available at `http://localhost:8000`.

### Option 2: Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Copy and edit environment file
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the dev server
uvicorn app.main:app --reload
```

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Project Structure

```
app/
├── main.py              # App factory + lifespan
├── core/                # Shared infrastructure
│   ├── config.py        # Environment-based settings
│   ├── security.py      # JWT + password hashing
│   ├── dependencies.py  # FastAPI Depends (DB, auth, RBAC)
│   ├── exceptions.py    # Custom error hierarchy
│   └── logging.py       # Structured JSON logging + audit
├── database/            # Database layer
│   ├── base.py          # SQLAlchemy Base model
│   └── session.py       # Async engine + session factory
├── modules/             # Domain modules
│   ├── auth/            # Authentication & user management
│   ├── undergraduate/   # Undergraduate admissions
│   ├── graduate/        # Graduate admissions
│   └── course/          # Course management + enrollment
└── agents/              # AI agent integration (placeholder)
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Framework | FastAPI |
| Database | PostgreSQL + JSONB |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Auth | JWT (python-jose) + bcrypt |
| Config | pydantic-settings |
| Testing | pytest + httpx |
