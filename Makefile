.PHONY: reset-seed

reset-seed:
	python3 scripts/reset_db.py
	alembic upgrade head
	python3 scripts/seed_undergraduate_admission.py
	python3 scripts/seed_course.py
	python3 scripts/seed_track_b_roster.py