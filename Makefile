.PHONY: help up down logs build migrate revision seed test lint fmt shell psql radar pipeline clean

help:
	@echo "Project Radar"
	@echo ""
	@echo "  make up         поднять весь стенд"
	@echo "  make down       остановить"
	@echo "  make logs       логи всех сервисов"
	@echo "  make migrate    alembic upgrade head"
	@echo "  make revision m='описание'   новая миграция"
	@echo "  make seed       завести проекты"
	@echo "  make radar      прогнать сбор GitHub"
	@echo "  make pipeline   прогнать pipeline"
	@echo "  make report     построить Daily Radar принудительно"
	@echo "  make test       тесты (БД не нужна)"
	@echo "  make lint       ruff"
	@echo "  make psql       psql в контейнер БД"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

build:
	docker compose build

migrate:
	docker compose exec api alembic upgrade head

revision:
	docker compose exec api alembic revision --autogenerate -m "$(m)"

seed:
	docker compose exec api python scripts/seed_projects.py

radar:
	docker compose exec worker python -c "from app.tasks.jobs import collect_github; print(collect_github())"

pipeline:
	docker compose exec worker python -c "from app.tasks.jobs import process_pipeline; print(process_pipeline())"

report:
	docker compose exec worker python -c "from app.tasks.jobs import build_daily_radar; print(build_daily_radar(force=True))"

test:
	cd backend && pytest

lint:
	cd backend && ruff check app tests

fmt:
	cd backend && ruff check --fix app tests && ruff format app tests

shell:
	docker compose exec api bash

psql:
	docker compose exec postgres psql -U radar -d radar

clean:
	docker compose down -v
