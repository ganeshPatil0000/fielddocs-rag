# FieldDocs RAG

A production-grade, document-grounded Q&A assistant for telecom field engineers.
Engineers ask questions in plain English; answers come **only** from indexed
manuals and runbooks, with numbered citations, or the assistant says it doesn't know.

**Stack:** LangChain · AWS Bedrock (Amazon Nova Lite / Nova Micro, Titan Text Embeddings V2) ·
Pinecone serverless · S3 · SQS · DynamoDB · Lambda (container image) · API Gateway HTTP API ·
Cognito · LangSmith · CloudWatch/X-Ray · Terraform · GitHub Actions

```
            ┌────────────── query path ───────────────┐
 client ──► API Gateway (JWT, throttling) ──► Lambda: FastAPI
                                                │  rewrite follow-up (Nova Micro)
                                                │  cache lookup (DynamoDB)
                                                │  embed (Titan V2) ──► Pinecone top-k
                                                │  drop low-score chunks, cap context
                                                │  answer with citations (Nova Lite)
                                                └► validate citations / grounding ──► JSON + sources + trace_id

            ┌──────────── ingestion path ─────────────┐
 client ──► POST /v1/documents/upload-url ──► presigned POST ──► S3 raw/<doc_id>/<file>
 S3 ObjectCreated ──► SQS (retries, DLQ) ──► Lambda: parse ► clean ► chunk ► embed ► upsert ► registry
```

## Repository layout

| Path                   | What it is                                                                              |
| ---------------------- | --------------------------------------------------------------------------------------- |
| `app/api/`           | FastAPI app, auth (Cognito claims, admin role)                                          |
| `app/rag/`           | prompts, retriever (threshold, dedupe, rerank, budget), service, citation validation    |
| `app/ingestion/`     | loaders (PDF/DOCX/TXT/MD), chunking, idempotent pipeline, SQS handler, local CLI        |
| `app/storage.py`     | DynamoDB single-table store (registry, chat history, answer cache) + in-memory twin     |
| `app/providers.py`   | Bedrock / Pinecone factories and a small vector-index interface                         |
| `app/fakes.py`       | offline embedder + extractive chat model for tests and laptop demos                     |
| `tests/`             | offline pytest suite (no AWS, no Pinecone needed)                                       |
| `evals/`             | golden question set + eval runner (retrieval hit, citation hit, refusal accuracy, cost) |
| `infra/terraform/`   | all AWS infrastructure                                                                  |
| `scripts/`           | deploy, secrets, users, tokens, uploads, smoke test                                     |
| `.github/workflows/` | CI (lint, tests, eval, terraform validate, docker build) and OIDC deploy                |

## 1. Run it on your laptop in 2 minutes (no cloud)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check --fix . && ruff format .   # first run: normalise import order / formatting
pytest                                # offline test suite
make run-offline                      # API on http://localhost:8000/docs, seeded with sample_docs/
```

```bash
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"question":"What received power is healthy at the ONT?"}' | python -m json.tool
```

Offline mode uses a hashing embedder and an extractive fake model, so answers are
crude, but every code path (chunking, retrieval, citations, cache, history, API) is real.

## 2. Run locally against Bedrock + Pinecone

1. In the AWS console, open **Bedrock → Model access** in `us-east-1` and enable
   *Amazon Nova Lite*, *Amazon Nova Micro* and *Titan Text Embeddings V2*.
2. Create a free Pinecone account and an API key, then create the index:
   ```bash
   PINECONE_API_KEY=pcsk_... python scripts/create_pinecone_index.py --name fielddocs --dimension 1024
   ```
3. `cp .env.example .env`, fill in `PINECONE_API_KEY`, make sure `aws sts get-caller-identity` works.
4. ```bash
   make ingest-local   # index sample_docs into Pinecone (namespace "local")
   make run-local      # http://localhost:8000/docs
   make eval           # golden-set metrics against the real models
   ```

## 3. Deploy to AWS

Prerequisites: AWS CLI v2, Terraform ≥ 1.10, Docker, an AWS account with Bedrock model access (step 2.1).

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars   # set alarm_email
./scripts/deploy.sh                        # builds image, pushes to ECR, terraform apply
./scripts/put_secrets.sh                   # Pinecone + LangSmith keys -> SSM (prompted, not echoed)
EMAIL=you@example.com PASSWORD='ChangeMe-12345' ADMIN=1 ./scripts/create_user.sh
export TOKEN=$(EMAIL=you@example.com PASSWORD='ChangeMe-12345' ./scripts/get_token.sh)
for f in sample_docs/*.md; do ./scripts/upload_doc.sh "$f"; done
./scripts/smoke_test.sh
python -m evals.run_eval --url "$(terraform -chdir=infra/terraform output -raw api_url)" --token "$TOKEN"
```

Confirm the SNS subscription email so alarms reach you. `terraform -chdir=infra/terraform output dashboard_url`
gives the CloudWatch dashboard (questions, refusals, tokens, estimated cost, latency, errors, DLQ).

**Tear down:** `terraform -chdir=infra/terraform destroy -var image_tag=x` (non-prod buckets and repos are force-deleted).

### CI/CD

1. Enable the S3 backend block in `infra/terraform/versions.tf` (state must be shared with CI).
2. Set `github_repository = "you/fielddocs-rag"` in tfvars and apply; copy the `github_deploy_role_arn` output.
3. In GitHub: add secret `AWS_DEPLOY_ROLE_ARN`, create environments `dev` and `prod` (add required reviewers to `prod`).
4. Pushes to `main` run CI then deploy to dev; prod is a manual `workflow_dispatch`.

## API

| Method | Path                         | Who    | Purpose                                                                                           |
| ------ | ---------------------------- | ------ | ------------------------------------------------------------------------------------------------- |
| GET    | `/health`                  | public | liveness                                                                                          |
| POST   | `/v1/chat`                 | user   | `{question, session_id?, doc_ids?}` → answer, sources, grounded/refused flags, usage, trace_id |
| POST   | `/v1/feedback`             | user   | thumbs up/down on a `trace_id` (sent to LangSmith)                                              |
| GET    | `/v1/documents`            | user   | list documents and ingestion status                                                               |
| GET    | `/v1/documents/{id}`       | user   | one document                                                                                      |
| POST   | `/v1/documents/upload-url` | admin  | presigned S3 POST (size-limited)                                                                  |
| DELETE | `/v1/documents/{id}`       | admin  | remove vectors, file and registry entry                                                           |

## Configuration

All settings are environment variables, see `app/config.py`. The ones you will touch:

| Variable                                 | Default                       | Notes                                                    |
| ---------------------------------------- | ----------------------------- | -------------------------------------------------------- |
| `LLM_MODEL_ID`                         | `us.amazon.nova-lite-v1:0`  | answer model                                             |
| `UTILITY_MODEL_ID`                     | `us.amazon.nova-micro-v1:0` | follow-up rewriting, grounding judge                     |
| `MIN_RELEVANCE_SCORE`                  | `0.35`                      | below this a chunk is ignored; tune with `make eval`   |
| `RETRIEVAL_K`                          | `5`                         | chunks sent to the model                                 |
| `ENABLE_GROUNDING_CHECK`               | `false`                     | second LLM call that verifies the answer                 |
| `STRICT_GROUNDING`                     | `true`                      | uncited/ungrounded answers become "I couldn't find this" |
| `ENABLE_RERANK` / `RERANK_MODEL_ARN` | off                           | Bedrock rerank model (extra cost per query)              |
| `BEDROCK_GUARDRAIL_ID`                 | unset                         | attach a Bedrock Guardrail (PII, denied topics)          |

## Known limitations

* Scanned PDFs have no text layer and are marked FAILED; add Amazon Textract for OCR.
* No response streaming (API Gateway HTTP API + Lambda buffer responses).
* DOCX tables are appended after body text rather than kept in place.
* Dependency ranges are not pinned to exact versions; run `pip freeze > requirements.lock` before a real release.
