VENV   = .venv
PYTHON = $(VENV)/bin/python
PYTEST = $(VENV)/bin/pytest
PLIST  = $(HOME)/Library/LaunchAgents/com.claude-assistant.watcher.plist

.PHONY: bootstrap build test start stop restart logs clean

## First-time setup: create venv, install deps, configure, register launchd agent.
bootstrap:
	sh bootstrap.sh

## Install/update Python and Node dependencies (skips config prompts).
build: $(VENV)
	$(VENV)/bin/pip install -q -r requirements.txt
	npm install --silent

$(VENV):
	python3 -m venv $(VENV)

## Run the test suite.
test: $(VENV)
	$(PYTEST) tests/ --cov=mcp_server --cov=watcher --cov-report=term-missing

## Load the launchd agent (start the watcher as a background service).
start:
	launchctl load $(PLIST)

## Unload the launchd agent (stop the watcher service).
stop:
	launchctl unload $(PLIST)

## Restart the watcher service.
restart: stop start

## Tail both watcher log files.
logs:
	tail -f logs/watcher.stdout.log logs/watcher.stderr.log

## Remove generated artifacts (venv, node_modules, logs, generated configs).
clean:
	rm -rf $(VENV) node_modules logs __pycache__ .pytest_cache
	find . -name '*.pyc' -delete
