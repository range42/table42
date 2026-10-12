.PHONY: setup run test browser check

setup:
	uv sync --locked

run:
	./start.sh

test:
	uv run --locked pytest -q

browser:
	uv run --locked --with playwright==1.58.0 python tests/browser_flow.py
	uv run --locked --with playwright==1.58.0 python tests/browser_ux_flow.py

check: test browser
