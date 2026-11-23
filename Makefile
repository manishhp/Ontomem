PY ?= python3
RESULTS ?= results

.PHONY: help install dev test lint ontology prefrev validate crosscheck bench bench-llm sweep figures reproduce gate-g2 backend frontend-install frontend demo docker clean

help:
	@echo "make install        install the package (+dev tools)"
	@echo "make test           run the test suite"
	@echo "make reproduce      regenerate PrefRev, run every experiment, rebuild all figures and tables"
	@echo "make bench-llm      run the LLM baselines S2/S2b (needs ONTOMEM_LLM, e.g. ollama:qwen2.5:7b-instruct)"
	@echo "make dev            start backend (:8000) and frontend (:5173) for development"
	@echo "make docker         build and start everything with docker compose"

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

ontology:
	$(PY) scripts/build_ontology.py

prefrev:
	$(PY) -m ontomem.prefrev.generator --out src/ontomem/data/prefrev --seed 7

validate:
	$(PY) -m ontomem.prefrev.validate src/ontomem/data/prefrev

crosscheck:
	$(PY) -m ontomem.core.asp.crosscheck --n 3000 --seed 1

bench:
	$(PY) -m ontomem.evaluation.runner --mode A --out $(RESULTS)/modeA
	$(PY) -m ontomem.evaluation.runner --mode B --out $(RESULTS)/modeB
	$(PY) -m ontomem.evaluation.runner --mode A --noise 0.15 --systems shacl --out $(RESULTS)/noise
	$(PY) -m ontomem.evaluation.evolution --out $(RESULTS)/evolution
	$(PY) -m ontomem.evaluation.scale --out $(RESULTS)/scale

bench-llm:
	$(PY) -m ontomem.evaluation.runner --mode A --systems S2,S2b,S3,S4 --out $(RESULTS)/llmA
	$(PY) -m ontomem.evaluation.runner --mode B --systems S2,S2b,S3,S4 --out $(RESULTS)/llmB

sweep:
	$(PY) -m ontomem.evaluation.analytic --out $(RESULTS)/sweep

figures:
	$(PY) -m ontomem.evaluation.figures --results $(RESULTS)

reproduce: ontology prefrev validate crosscheck bench sweep figures
	@echo "All results in $(RESULTS)/ (tables: */table.tex, figures: figures/)"

gate-g2:
	$(PY) -m ontomem.evaluation.gate_g2

backend:
	$(PY) -m uvicorn ontomem.backend.app:app --reload --port 8000

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

dev:
	@echo "starting backend on :8000 and frontend on :5173"
	$(PY) -m uvicorn ontomem.backend.app:app --port 8000 & cd frontend && npm run dev

docker:
	docker compose up --build

clean:
	rm -rf .pytest_cache .hypothesis .ruff_cache build *.egg-info
