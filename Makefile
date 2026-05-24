.PHONY: reset-seed

reset-seed:
	python3 scripts/reset_db.py
	alembic upgrade head
	python3 scripts/seed.py
	python3 scripts/seed_ranking_test.py
	python3 scripts/seed_course.py
	python3 scripts/seed_track_b_roster.py