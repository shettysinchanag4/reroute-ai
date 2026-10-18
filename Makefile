.PHONY: install playground run test lint

# ── RerouteAI Makefile ──────────────────────────────────────────────────────
# agent_dir = app  (the folder containing agent.py)
# NOTE on Windows: use the 'playground' target via PowerShell only.
#   If make is unavailable, run the uv command directly (see README).

AGENT_DIR := app

install:
	uv sync

playground:
	@echo "Starting RerouteAI playground at http://localhost:18081 ..."
	@echo "On Windows, if this crashes with 'unexpected extra arguments', run:"
	@echo "  uv run adk web $(AGENT_DIR) --host 127.0.0.1 --port 18081 --reload_agents"
	uv run adk web $(AGENT_DIR) --host 127.0.0.1 --port 18081 --reload_agents

run:
	@echo "Starting RerouteAI FastAPI server ..."
	uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8090 --reload

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check app/ tests/
	uv run ruff format --check app/ tests/
