PYTHON ?= .venv/bin/python

.PHONY: report

report:
	$(PYTHON) run_report.py --executor local --general --pdf-mode auto --purge-stale-images
