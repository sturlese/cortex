# cortex — convenience targets.
.DEFAULT_GOAL := help
PACKAGES := pipeline/fetch pipeline/clean pipeline/corpus pipeline/graph
PY ?= python3        # stock macOS / Debian ship python3, not `python`; override with `make PY=python`

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

demo: ## Run the end-to-end demo over examples/demo-corpus (no API keys needed)
	bash examples/run-demo.sh

eval: ## Offline golden evals: curation, placement, trust layer, graph (no API keys)
	bash evals/run-evals.sh

test: ## Run every package's test suite (coverage gate 75%)
	@for pkg in $(PACKAGES); do \
		echo "===== $$pkg ====="; \
		(cd $$pkg && $(PY) -m pytest -q --cov-fail-under=75) || exit 1; \
	done

lint: ## Ruff over all Python packages
	ruff check pipeline evals

.PHONY: help demo eval test lint
