.PHONY: bootstrap setup preflight test validate ingest ask cli serve release backend frontend dev-backend dev-frontend dev refs clean

bootstrap:
	bash scripts/bootstrap_macos.sh

setup:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

preflight:
	PYTHONPATH=.:backend python scripts/preflight.py --strict-imports

test:
	PYTHONPATH=.:backend python -m pytest backend/tests -q

validate:
	PYTHONPATH=.:backend python scripts/validate_json.py

ingest:
	PYTHONPATH=.:backend python scripts/ingest_folder.py data/input

ask:
	PYTHONPATH=.:backend python scripts/ask.py "How does source lineage work?"

cli:
	PYTHONPATH=.:backend python scripts/mucaro.py ask "How does source lineage work?" --format text

serve:
	PYTHONPATH=.:backend python scripts/mucaro.py serve --reload

release:
	PYTHONPATH=.:backend python scripts/release_check.py

backend:
	PYTHONPATH=.:backend uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm ci && npm run dev

dev-backend:
	PYTHONPATH=.:backend python scripts/dev_start.py --backend-only

dev-frontend:
	PYTHONPATH=.:backend python scripts/dev_start.py --frontend-only

dev:
	PYTHONPATH=.:backend python scripts/dev_start.py

refs:
	bash scripts/clone_references.sh
	python scripts/extract_reference_patterns.py

clean:
	rm -rf .pytest_cache backend/.pytest_cache backend/app/__pycache__ backend/tests/__pycache__
