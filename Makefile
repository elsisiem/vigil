# Vigil — one-command targets. Works with GNU make on macOS/Linux and Git Bash on Windows.
PY ?= $(shell test -x .venv/Scripts/python.exe && echo .venv/Scripts/python.exe || echo .venv/bin/python)
PORT ?= 8787

.PHONY: setup serve demo offline run feed eval test replay clean

setup:            ## create the venv and install dependencies
	python -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "Now fill in .env, then: make serve"

serve:            ## start the command station on http://127.0.0.1:$(PORT)
	$(PY) -m vigil serve --port $(PORT)

demo:             ## serve and auto-run the two-tick smoke incident in the browser
	@echo "open http://127.0.0.1:$(PORT)/?fixture=kitchen-smoke"
	$(PY) -m vigil serve --port $(PORT)

offline:          ## serve with no network: replays recorded runs only
	@echo "open http://127.0.0.1:$(PORT)/?replay=organizers-outbreak-level-5-tick-142"
	VIGIL_OFFLINE=1 $(PY) -m vigil serve --port $(PORT)

run:              ## run one fixture in the terminal: make run F=kitchen-smoke
	$(PY) -m vigil run --fixture $(F) --record

feed:             ## play the live scenario feed for your team (dry-run unless VIGIL_DRY_RUN=0)
	$(PY) -m vigil feed --scenario outbreak-level-5 --act --record
	$(PY) -m vigil feed --scenario control-tick --act --record

eval:             ## decision accuracy, false alarms, latency, schema validity, citation coverage
	$(PY) -m vigil eval

test:             ## offline unit tests
	$(PY) -m pytest -q

replay:           ## print a recorded run: make replay R=kitchen-smoke-tick-2
	$(PY) -m vigil replay $(R)

clean:
	rm -rf cache/*.sqlite cache/memory .pytest_cache
