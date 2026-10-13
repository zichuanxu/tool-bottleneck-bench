PYTHON ?= .venv/bin/python
# Named providers first, then every *_API_KEY / *_TOKEN / *_CREDENTIALS variable
# that is actually set, so a new provider variable cannot leak in unnoticed.
OFFLINE_KEYS := OPENROUTER_API_KEY ZAI_API_KEY GOOGLE_CLOUD_API_KEY AGNES_API_KEY \
	OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY GEMINI_API_KEY \
	GOOGLE_APPLICATION_CREDENTIALS
OFFLINE_ENV := env $(foreach key,$(OFFLINE_KEYS),-u $(key)) \
	$(shell env | sed -nE 's/^([A-Za-z0-9_]*(_API_KEY|_TOKEN|_CREDENTIALS))=.*/-u \1/p' | sort -u)

.PHONY: verify test public-check

verify: public-check test

test:
	@$(OFFLINE_ENV) PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py'

public-check:
	python3 scripts/check_public_release.py
