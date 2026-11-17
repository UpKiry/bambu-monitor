PYTHON ?= python3

.PHONY: check build preview pack glyphs
check build preview pack glyphs:
	$(PYTHON) tools/project.py $@ $(ARGS)
