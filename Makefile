PYTHON ?= python3

.PHONY: bootstrap setup preflight test validate inventory-check ingest ask cli serve launch-smoke release certify fresh-clone-cert backend frontend dev-backend dev-frontend dev refs clean

bootstrap:
	bash scripts/bootstrap_macos.sh

setup:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

preflight:
	PYTHONPATH=.:backend $(PYTHON) scripts/preflight.py --strict-imports

test:
	PYTHONPATH=.:backend $(PYTHON) -m pytest backend/tests -q

validate:
	PYTHONPATH=.:backend $(PYTHON) scripts/validate_json.py

inventory-check:
	PYTHONPATH=.:backend $(PYTHON) scripts/mucaro.py flagpole inventory verify --format text

ingest:
	PYTHONPATH=.:backend $(PYTHON) scripts/ingest_folder.py data/input

ask:
	PYTHONPATH=.:backend $(PYTHON) scripts/ask.py "How does source lineage work?"

cli:
	PYTHONPATH=.:backend $(PYTHON) scripts/mucaro.py ask "How does source lineage work?" --format text

serve:
	PYTHONPATH=.:backend $(PYTHON) scripts/mucaro.py serve --reload

launch-smoke:
	PYTHONPATH=.:backend $(PYTHON) scripts/launch_app.py --smoke-test --no-browser

release:
	PYTHONPATH=.:backend $(PYTHON) scripts/release_check.py

certify:
	PYTHONPATH=.:backend $(PYTHON) scripts/certify_release.py

fresh-clone-cert:
	$(PYTHON) scripts/certify_fresh_clone.py

backend:
	PYTHONPATH=.:backend uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm ci && npm run dev

dev-backend:
	PYTHONPATH=.:backend $(PYTHON) scripts/dev_start.py --backend-only

dev-frontend:
	PYTHONPATH=.:backend $(PYTHON) scripts/dev_start.py --frontend-only

dev:
	PYTHONPATH=.:backend $(PYTHON) scripts/dev_start.py

refs:
	bash scripts/clone_references.sh
	$(PYTHON) scripts/extract_reference_patterns.py

clean:
	rm -rf .pytest_cache backend/.pytest_cache backend/app/__pycache__ backend/tests/__pycache__
