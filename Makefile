PY ?= python3
STACK_DIR ?= deploy

.PHONY: verify lint test stack-up stack-down stack-status backup drill sync status

verify: lint test

lint:
	@(command -v ruff >/dev/null 2>&1 && ruff check . || $(PY) -m ruff check .) && echo "lint: clean"

test:
	@$(PY) -m pytest

stack-up:
	@cd $(STACK_DIR) && docker compose up -d && echo "stack: up"

stack-down:
	@cd $(STACK_DIR) && docker compose down && echo "stack: down"

stack-status:
	@cd $(STACK_DIR) && docker compose ps

backup:
	@bash scripts/backup.sh

drill:
	@bash scripts/restore_drill.sh

sync:
	@$(PY) -m glucoseledger sync

status:
	@$(PY) -m glucoseledger status
