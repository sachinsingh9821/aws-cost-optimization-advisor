# Thin wrapper around tasks.py (Windows users: run `python tasks.py <target>` directly).
PY ?= python

.PHONY: install data lint test evaluate app check run

install:
	$(PY) -m pip install -r requirements-dev.txt

data lint test evaluate app check:
	$(PY) tasks.py $@

run: app
