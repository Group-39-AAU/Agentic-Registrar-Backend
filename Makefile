.PHONY: reset-seed

reset-seed:
	python3 scripts/reset_db.py
	alembic upgrade head
	python3 scripts/seed_undergraduate_admission.py
	python3 scripts/seed_course_management.py