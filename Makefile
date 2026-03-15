PYTHON ?= python
UVICORN ?= uvicorn
COMPOSE ?= docker compose

.PHONY: install format lint test up down \
	run-identity run-undergrad run-graduate run-course \
	run-documents run-notifications run-gateway

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e .[dev]

format:
	ruff format .

lint:
	ruff check .
	mypy .

test:
	pytest

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

run-identity:
	cd services/identity-service && $(UVICORN) app.main:app --reload --port 8001

run-undergrad:
	cd services/undergrad-admission-service && $(UVICORN) app.main:app --reload --port 8002

run-graduate:
	cd services/graduate-admission-service && $(UVICORN) app.main:app --reload --port 8003

run-course:
	cd services/course-management-service && $(UVICORN) app.main:app --reload --port 8004

run-documents:
	cd services/document-service && $(UVICORN) app.main:app --reload --port 8005

run-notifications:
	cd services/notification-service && $(UVICORN) app.main:app --reload --port 8006

run-gateway:
	cd services/api-gateway && $(UVICORN) app.main:app --reload --port 8000

