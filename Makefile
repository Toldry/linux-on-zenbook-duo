# linux-on-zenbook-duo — top-level targets. `./install.sh --help` lists the flags.

PYTHON ?= python3

# What `make lint` checks; CI runs `make lint` too, so this is the one list.
SH_FILES = bin/duo-cli helper/zenduo-helper install.sh uninstall.sh lib/conf.sh system/*.sh tests/*.sh
PY_FILES = bin/duo lib/*.py tests/*.py tools/*.py windows/*.py

.PHONY: help install install-system install-user uninstall dry-run doctor status features screen test lint preset changelog

help:
	@echo "targets:"
	@echo "  make install         - ./install.sh (system + user; asks for sudo once)"
	@echo "  make install-system  - the root half only (sudo)"
	@echo "  make install-user    - the user half only (config + systemd user units)"
	@echo "  make dry-run         - preview what install would change"
	@echo "  make uninstall       - ./uninstall.sh"
	@echo "  make doctor          - duo doctor (read-only hardware probe, live-USB safe)"
	@echo "  make status          - duo status"
	@echo "  make features        - duo features"
	@echo "  make screen          - duo, the screen (bin/duo --snapshot VIEW prints one frame as text)"
	@echo "  make test            - shell + python unit tests (sudo make test covers root-only cases)"
	@echo "  make lint            - bash -n, py_compile, changelog fragments; shellcheck and ruff if installed"
	@echo "  make changelog       - preview the next release's CHANGELOG section (changelog.d/README.md)"
	@echo "  make preset          - regenerate presets/easyeffects/duo-speakers.json from lib/speaker_dsp.py"

install:
	./install.sh

install-system:
	sudo bash system/run.sh --user "$$USER"

install-user:
	./install.sh --user

dry-run:
	./install.sh --dry-run

uninstall:
	./uninstall.sh

doctor:
	bin/duo-cli doctor

status:
	bin/duo-cli status

features:
	bin/duo-cli features

screen:
	bin/duo

test:
	bash tests/test-lib.sh
	$(PYTHON) -m unittest discover -s tests -v

lint:
	bash -n $(SH_FILES)
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck -x $(SH_FILES); \
	else echo "shellcheck not installed — skipped (CI runs it)"; fi
	$(PYTHON) -m py_compile $(PY_FILES)
	@# pyflakes' checks only (unused names, undefined names), not style.
	@if command -v ruff >/dev/null 2>&1; then ruff check --quiet --select F $(PY_FILES); \
	else echo "ruff not installed — skipped (CI runs it)"; fi
	$(PYTHON) tools/release.py check
	@# The trap that shipped three times: a pipe into grep -q under pipefail.
	@if grep -nE '\|[[:space:]]*grep[[:space:]]+-[a-zA-Z]*q' bin/duo-cli install.sh uninstall.sh lib/conf.sh system/*.sh | grep -v '^[^:]*:[0-9]*:[[:space:]]*#'; then \
	  echo "^ never pipe into grep -q (see docs/DESIGN.md) — capture, then match" >&2; exit 1; fi

preset:
	$(PYTHON) lib/speaker_dsp.py preset > presets/easyeffects/duo-speakers.json

changelog:
	$(PYTHON) tools/release.py draft
