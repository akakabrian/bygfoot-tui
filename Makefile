VENDOR := engine

.PHONY: all bootstrap venv run clean test test-only

all: bootstrap venv

bootstrap: $(VENDOR)/.git
$(VENDOR)/.git:
	@echo "==> fetching bygfoot mirror into engine/ (one time)"
	git clone --depth=1 https://github.com/kashifsoofi/bygfoot.git $(VENDOR)
	@echo "==> bootstrap complete — run 'make venv' next"

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .

run: venv
	.venv/bin/python bygfoot.py

test: venv
	.venv/bin/python -m tests.qa

test-only: venv
	.venv/bin/python -m tests.qa $(PAT)

test-api: venv
	.venv/bin/python -m tests.api_qa

test-perf: venv
	.venv/bin/python -m tests.perf

test-all: test test-api test-perf

clean:
	rm -rf .venv __pycache__ bygfoot_tui/__pycache__ tests/__pycache__

fullclean: clean
	rm -rf $(VENDOR)
