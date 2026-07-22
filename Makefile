PY ?= python3

.PHONY: all prefs fetch extract curate script tts publish feedback test-short clean

all: prefs fetch extract curate script tts publish feedback

prefs:
	$(PY) -m src.update_preferences

fetch:
	$(PY) -m src.fetch

extract:
	$(PY) -m src.extract

curate:
	$(PY) -m src.curate

script:
	$(PY) -m src.write_script

tts:
	$(PY) -m src.tts

publish:
	$(PY) -m src.publish

feedback:
	$(PY) -m src.feedback

test-short:
	TEST_SHORT=true $(MAKE) all

clean:
	rm -rf out
