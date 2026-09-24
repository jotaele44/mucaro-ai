PYTHON ?= python3

.PHONY: bootstrap setup preflight test validate ingest ask cli serve launch-smoke desktop macos-app macos-app-verify security-gates release-grade release-status release certify fresh-clone-cert backend frontend dev-backend dev-frontend dev refs clean

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

# Desktop shell from source (needs the `desktop` extra for the native window).
desktop:
	PYTHONPATH=. $(PYTHON) -m backend.app.desktop_shell

# Mucaro AI.app (macOS arm64; see docs/release/MACOS_APP.md).
macos-app:
	$(PYTHON) scripts/build_macos_app.py --build-frontend

macos-app-verify:
	$(PYTHON) scripts/verify_macos_app.py --zip "$$(ls -t data/exports/macos_app/*/Mucaro-AI-*-macos-arm64.zip | head -1)" --output data/exports/macos_app/e2e_receipt.json

# Measured security gates, SBOMs and receipt (docs/release/SECURITY_GATES.md).
security-gates:
	PYTHONPATH=.:backend $(PYTHON) scripts/certify_security_gates.py

# Fail-closed release-grade verdict. Pass the macOS workflow receipts for this head, e.g.
#   make release-grade GRADE_ARGS="--macos-build-receipt build_receipt.json --macos-e2e-receipt e2e_receipt.json"
release-grade:
	PYTHONPATH=.:backend $(PYTHON) scripts/certify_release_grade.py $(GRADE_ARGS)

# Regenerate the computed block in docs/RELEASE_STATUS.md from the release ledgers.
release-status:
	PYTHONPATH=.:backend $(PYTHON) scripts/mucaro.py release scope status --write-doc --format text

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
