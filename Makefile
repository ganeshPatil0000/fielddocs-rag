.PHONY: install test lint run-offline run-local ingest-local eval eval-offline eval-ragas docker deploy smoke

install:            ## dev install (Python 3.12)
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:               ## unit + API tests, fully offline
	pytest

lint:
	ruff check . && ruff format --check .

run-offline:        ## API on :8000 with fake LLM + in-memory index seeded from sample_docs
	APP_ENV=local LLM_PROVIDER=fake VECTOR_STORE=memory STORAGE_BACKEND=memory AUTH_MODE=none \
	MIN_RELEVANCE_SCORE=0.18 EMBEDDING_DIMENSIONS=512 LOCAL_SEED_DIR=sample_docs \
	uvicorn app.api.main:app --reload --port 8000

run-local:          ## API on :8000 against real Bedrock + Pinecone (reads .env)
	uvicorn app.api.main:app --reload --port 8000

ingest-local:       ## index sample_docs into the Pinecone index from .env
	python -m app.ingestion.cli sample_docs

eval-offline:
	APP_ENV=test LLM_PROVIDER=fake VECTOR_STORE=memory STORAGE_BACKEND=memory AUTH_MODE=none \
	MIN_RELEVANCE_SCORE=0.18 EMBEDDING_DIMENSIONS=512 python -m evals.run_eval --seed sample_docs --no-gate

eval:               ## golden-set eval against real Bedrock + Pinecone (reads .env)
	python -m evals.run_eval

eval-ragas:         ## RAGAS LLM-judged metrics (pip install -e ".[eval]" -c requirements.lock)
	python -m evals.ragas_eval

docker:
	docker build --platform linux/amd64 --provenance=false -t fielddocs-rag:local .

deploy:
	./scripts/deploy.sh

smoke:
	./scripts/smoke_test.sh
