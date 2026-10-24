PYTHON ?= python3
NPM ?= npm

.PHONY: demo test dashboard-build dashboard-dev dashboard release-check release

demo:
	$(PYTHON) -m india analyze DEMO.NS --csv tests/fixtures/india/synthetic.csv --as-of 2026-10-19T09:35:00+05:30 --tick 0.05 --capital 100000 --risk 500

test:
	$(PYTHON) -m unittest discover -s tests

dashboard-build:
	$(NPM) ci --prefix dashboard
	$(NPM) run build --prefix dashboard

dashboard-dev:
	$(NPM) run dev --prefix dashboard

dashboard:
	$(PYTHON) -m dashboard

release-check:
	$(PYTHON) scripts/build_release.py --check

release:
	$(PYTHON) scripts/build_release.py --archive
