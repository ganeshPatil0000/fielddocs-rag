# FieldDocs RAG

A production-grade, document-grounded Q&A assistant for telecom field engineers.
Engineers ask questions in plain English; answers come **only** from indexed
manuals and runbooks, with numbered citations, or the assistant says it doesn't know.

**Stack:** LangChain · AWS Bedrock (Amazon Nova Lite / Nova Micro, Titan Text Embeddings V2) ·
Pinecone serverless (dense + sparse, hybrid search) · S3 · SQS · DynamoDB · Lambda (container image) · API Gateway HTTP API ·
Cognito · LangSmith · CloudWatch/X-Ray · Terraform · GitHub Actions

```
            ┌────────────── query path ───────────────┐
 client ──► API Gateway (JWT, throttling) ──► Lambda: FastAPI
                                                │  rewrite follow-up (Nova Micro)
                                                │  cache lookup (DynamoDB)
                                                │  semantic: embed (Titan V2) ──► Pinecone dense top-k
                                                │  keyword:  Pinecone sparse top-k (exact codes)
                                                │  fuse with RRF, drop weak chunks, cap context
                                                │  answer with citations (Nova Lite)
                                                └► validate citations / grounding ──► JSON + sources + trace_id

            ┌──────────── ingestion path ─────────────┐
 client ──► POST /v1/documents/upload-url ──► presigned POST ──► S3 raw/<doc_id>/<file>
 S3 ObjectCreated ──► SQS (retries, DLQ) ──► Lambda: parse ► clean ► chunk ► embed ► upsert (dense + sparse) ► registry
```

## Repository layout

| Path                   | What it is                                                                              |
| ---------------------- | --------------------------------------------------------------------------------------- |
| `app/api/`           | FastAPI app, auth (Cognito claims, admin role)                                          |
| `app/rag/`           | prompts, retriever (hybrid fusion, threshold, dedupe, rerank, budget), service, citations |
| `app/keyword_index.py` | keyword search for hybrid retrieval: Pinecone sparse index + in-memory BM25 twin      |
| `app/ingestion/`     | loaders (PDF/DOCX/TXT/MD), chunking, idempotent pipeline, SQS handler, local CLI        |
| `app/storage.py`     | DynamoDB single-table store (registry, chat history, answer cache) + in-memory twin     |
| `app/providers.py`   | Bedrock / Pinecone factories and a small vector-index interface                         |
| `app/fakes.py`       | offline embedder + extractive chat model for tests and laptop demos                     |
| `frontend/`          | web app: React + TypeScript + Vite (chat with cited sources, documents, Cognito sign-in) |
| `tests/`             | offline pytest suite (no AWS, no Pinecone needed)                                       |
| `evals/`             | golden question set + eval runner (retrieval hit, citation hit, refusal accuracy, cost) and RAGAS runner |
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

### Web app

Needs Node.js 20 or newer. With the API running (`make run-offline` or `make run-local`):

```bash
cd frontend
npm install          # first time only; commit the generated package-lock.json
npm run dev          # http://localhost:5173
npm test             # unit + component tests
npm run build        # production build in frontend/dist
```

With no `VITE_COGNITO_CLIENT_ID` the app runs in local mode (no sign-in), matching an API
started with `AUTH_MODE=none`. For the deployed API, copy `frontend/.env.example` to
`frontend/.env.local` and set `VITE_API_URL` and `VITE_COGNITO_CLIENT_ID` from the Terraform
outputs. Users sign in with their Cognito account; admins (Cognito group `admin`) can upload and
delete documents.

Design notes: sources are colour-coded with the standard 12-fibre colour sequence (source 1 blue,
2 orange, 3 green...), so a citation in the answer and its passage in the sources panel share a
colour. Answers are rendered from text, never inserted as HTML, so model output can't inject markup.

## 2. Run locally against Bedrock + Pinecone

1. Check that your account can call the models in your AWS Region (`ap-southeast-2` here):
   ```bash
   python scripts/check_bedrock.py   # each model should print OK
   ```
   Answers use `global.amazon.nova-2-lite-v1:0` (a *global* inference profile: AWS may serve a request
   from another Region), follow-ups use `amazon.nova-micro-v1:0` and embeddings use
   `amazon.titan-embed-text-v2:0` (both in-Region). A brand-new account can start with
   Bedrock quotas of 0 ("Too many tokens per day"); those are not adjustable in Service
   Quotas, so open an AWS Support case to have them activated.
2. Create a free Pinecone account and an API key, then create the index:
   ```bash
   PINECONE_API_KEY=pcsk_... python scripts/create_pinecone_index.py --name fielddocs --dimension 1024
   PINECONE_API_KEY=pcsk_... python scripts/create_pinecone_index.py --sparse --name fielddocs-sparse
   ```
   The second (sparse) index powers the keyword half of hybrid search. Skip it only
   if you set `ENABLE_HYBRID_SEARCH=false`.
3. `cp .env.example .env`, fill in `PINECONE_API_KEY`, make sure `aws sts get-caller-identity` works.
4. ```bash
   make ingest-local   # index sample_docs into Pinecone (namespace "local")
   make run-local      # http://localhost:8000/docs
   make eval           # golden-set metrics against the real models
   ```

### Without Bedrock: any OpenAI-compatible API

Until Bedrock is unlocked (or to compare providers), the same app can call an
OpenAI-compatible API such as an LLM router. With AIRouter, the same Amazon models
are available, so nothing else changes (the Pinecone index stays 1024-d):

```bash
# .env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://api.airouter.in/v1
OPENAI_API_KEY=...                      # your key, only in .env / SSM
LLM_MODEL_ID=amazon/nova-2-lite
UTILITY_MODEL_ID=amazon/nova-micro
EMBEDDING_MODEL_ID=amazon/titan-embed-text-v2
```

`python scripts/check_llm_api.py` sends three tiny requests and checks the vector size
before you ingest anything. For the AWS deploy, set the same values in `terraform.tfvars`
(see the example file) and store the key with `./scripts/put_secrets.sh`. A third-party
router sees your questions and document text, so use it with sample documents, not
confidential ones. Bedrock Guardrails apply only with `LLM_PROVIDER=bedrock`. When you
switch providers, re-ingest the documents so every vector comes from the same model.

## 3. Deploy to AWS

Prerequisites: AWS CLI v2, Terraform ≥ 1.10, Docker, an AWS account with Bedrock access (step 2.1).
Everything deploys to one Region (`ap-southeast-2`, set in `variables.tf`). The free Pinecone
plan runs in AWS `us-east-1`, so each search crosses Regions over HTTPS (roughly 0.2 s).

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars   # set alarm_email
./scripts/deploy.sh                        # builds image, pushes to ECR, terraform apply
./scripts/put_secrets.sh                   # Pinecone, LangSmith, router keys -> SSM (prompted, not echoed)
./scripts/deploy_web.sh                    # build the web app, upload to S3, refresh CloudFront
EMAIL=you@example.com PASSWORD='ChangeMe-12345' ADMIN=1 ./scripts/create_user.sh
export TOKEN=$(EMAIL=you@example.com PASSWORD='ChangeMe-12345' ./scripts/get_token.sh)
for f in sample_docs/*.md; do ./scripts/upload_doc.sh "$f"; done
./scripts/smoke_test.sh
python -m evals.run_eval --url "$(terraform -chdir=infra/terraform output -raw api_url)" --token "$TOKEN"
```

On Windows, run the scripts in Git Bash. The deploy scripts already handle its quirks
(ECR login tokens too long for Windows Credential Manager, `/`-paths rewritten by Git Bash).

The web app is served from a private S3 bucket through CloudFront (`terraform output web_url`):
HTTPS only, hashed assets cached for a year, `index.html` re-checked on every visit, and security
headers including a Content Security Policy that only allows calls to the API, Cognito and the
documents bucket. CORS on the API and the upload bucket allows only that origin (plus the Vite dev
server outside prod); `smoke_test.sh` checks both, and that other sites are refused.

Confirm the SNS subscription email so alarms reach you. `terraform -chdir=infra/terraform output dashboard_url`
gives the CloudWatch dashboard (questions, refusals, tokens, estimated cost, latency, errors, DLQ).

**Tear down:** `terraform -chdir=infra/terraform destroy -var image_tag=x` (non-prod buckets and repos are force-deleted).

### CI/CD

> On an AWS **Free plan** project (new sign-up experience) the account's service control policy
> denies `iam:*Provider*`, so Terraform can't create the GitHub OIDC provider. Deploy from your
> machine with `./scripts/deploy.sh` until the account is upgraded.

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
| POST   | `/v1/documents/upload-url` | admin  | presigned S3 POST (size-limited); 409 `duplicate` / `same_name` with the existing `doc_id`        |
| POST   | `/v1/documents/{id}/replace-url` | admin | presigned S3 POST for a new version of an existing document (409 while one is indexing)    |
| DELETE | `/v1/documents/{id}`       | admin  | remove vectors, file and registry entry                                                           |

## Configuration

All settings are environment variables, see `app/config.py`. The ones you will touch:

| Variable                                 | Default                       | Notes                                                    |
| ---------------------------------------- | ----------------------------- | -------------------------------------------------------- |
| `LLM_PROVIDER`                         | `bedrock`                   | `bedrock`, `openai` (OpenAI-compatible API) or `fake` (offline) |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | unset                         | only for `LLM_PROVIDER=openai`                           |
| `LLM_MODEL_ID`                         | `global.amazon.nova-2-lite-v1:0` | answer model ($0.30 / $2.50 per 1M tokens in / out)      |
| `UTILITY_MODEL_ID`                     | `amazon.nova-micro-v1:0`    | follow-up rewriting, grounding judge (cheapest)          |
| `MIN_RELEVANCE_SCORE`                  | `0.35`                      | below this a chunk is ignored; tune with `make eval`   |
| `RETRIEVAL_K`                          | `5`                         | chunks sent to the model                                 |
| `ENABLE_HYBRID_SEARCH`                 | `true`                      | semantic + keyword search, merged with RRF               |
| `PINECONE_SPARSE_INDEX`                | `fielddocs-sparse`          | sparse index for keyword search                          |
| `HYBRID_KEYWORD_WEIGHT`                | `1.0`                       | keyword list's weight in the fusion (0 = off)            |
| `HYBRID_MIN_KEYWORD_COVERAGE`          | `0.5`                       | keyword-only chunks must contain this share of the question's keywords |
| `RATE_LIMIT_PER_MINUTE`                | `10`                        | questions per user per minute (0 = off); 429 with `Retry-After` |
| `RATE_LIMIT_PER_DAY`                   | `200`                       | questions per user per UTC day: caps LLM spend per account |
| `ENABLE_GROUNDING_CHECK`               | `false`                     | second LLM call that verifies the answer                 |
| `STRICT_GROUNDING`                     | `true`                      | uncited/ungrounded answers become "I couldn't find this" |
| `ENABLE_RERANK` / `RERANK_MODEL_ARN` | off                           | Bedrock rerank model (extra cost per query)              |
| `BEDROCK_GUARDRAIL_ID`                 | unset                         | attach a Bedrock Guardrail (PII, denied topics)          |

## Evaluation

Two layers, because they answer different questions:

| Runner | How it scores | Cost | When |
| ------ | ------------- | ---- | ---- |
| `make eval` (`evals/run_eval.py`) | exact checks: right document retrieved and cited, key fact in the answer, refusals correct, tokens, latency | ~free | every change; offline version gates CI |
| `make eval-ragas` (`evals/ragas_eval.py`) | [RAGAS](https://docs.ragas.io) metrics judged by a Bedrock LLM: **faithfulness** (hallucination), **answer relevancy**, **context precision**, **context recall** | a few cents | before a release, after prompt / retrieval / model changes |

```bash
pip install -e ".[eval]" -c requirements.lock    # ragas, pinned; stays out of the Lambda image
python -m evals.ragas_eval --seed sample_docs     # judge = LLM_MODEL_ID by default
python -m evals.ragas_eval --judge-model amazon.nova-micro-v1:0 --limit 5
```

Context precision and recall compare against the `reference` answer stored with each golden
question. Results print per question and are saved to `evals/reports/ragas-<time>.json`;
the run exits non-zero below the thresholds in `evals/ragas_eval.py`. A judge from a stronger or
different model family than the answering model gives less biased scores.

## How hybrid search works

Embeddings match *meaning* but blur exact tokens such as fault codes (`LOS-NET`),
alarm names (`MAINS FAIL`) and readings. So every question runs two searches:

1. **Semantic**: Titan embedding → dense Pinecone index → chunks above `MIN_RELEVANCE_SCORE`.
2. **Keyword**: question text → sparse Pinecone index (`pinecone-sparse-english-v0`, Pinecone embeds it).
3. **Fusion**: Reciprocal Rank Fusion, `score = Σ weight / (60 + rank)`. It uses only the rank
   order, so cosine and sparse scores never need normalising; chunks both searches agree on rise.
4. **Noise gate**: a chunk found *only* by keyword search must contain at least half of the
   question's keywords, so one shared word ("router") can't pull in an unrelated chunk.

Each source in the API response has `match`: `semantic`, `keyword` or `both`. Both indexes use the
same chunk ids, so ingestion and deletes keep them in step. If the sparse index fails, answers
fall back to semantic-only and the `KeywordSearchErrors` alarm fires.

## How document updates work

Manuals change. When an admin clicks **Replace**, the new file gets the same `doc_id` and the
next version number, and answers move from the old version to the new one without ever mixing
the two:

1. The new version's chunks are written under new ids (`<doc_id>#v2#<n>`; version 1 keeps
   `<doc_id>#<n>`, so older documents need no migration). The old version keeps answering.
2. When every chunk is stored, one registry write moves `version` forward and the answer cache
   is invalidated (the cache key includes a corpus version).
3. The old chunks and the old file in S3 are deleted.

At query time the retriever reads the registry (one DynamoDB `BatchGetItem`) and drops any
chunk whose version is not the current one, before ranking. So a crash between steps, a
half-written new version, or a cleanup that failed can never put old and new text in one
answer. If the new file can't be used (for example a scanned PDF with no text), it is rejected,
the old version keeps answering, and the Documents page shows why.

### Duplicates

Two live copies of one manual drift apart and give conflicting answers, so uploads are checked:

* **Same content.** The browser computes the file's SHA-256 and the API refuses an exact
  duplicate before anything is uploaded (409 `duplicate`). Ingestion hashes the real bytes again
  and claims the hash with a DynamoDB conditional write (`HASH / SHA#<sha256>`), so even two
  uploads at the same moment can't both be indexed; the loser is marked `DUPLICATE`. File times
  (ctime/mtime) are not used: browsers don't send ctime, and mtime changes on every copy.
* **Same name.** A new upload named like an existing document is almost always its new
  version, so the API answers 409 `same_name` and the web app offers **Replace** instead.
* **Conflicting sources.** If two different documents still disagree (say 4 hours vs 6 hours),
  the prompt tells the model to show both values with their citations, never to blend them.

## Rate limits

Two layers, so one person can't slow the service down or run up the bill:

1. **API Gateway** throttles the whole API (`api_throttle_rate` = 10 requests/second, burst 20)
   before Lambda runs. That protects capacity, but it is shared by everyone.
2. **Per user** (`app/ratelimit.py`): a fixed-window counter per Cognito user in DynamoDB
   (`RATE#<user>`, one atomic `ADD` per question, TTL cleanup) allows 10 questions a minute and
   200 a day. Over the limit the API answers 429 with `Retry-After`, and the web app shows
   when to try again. If the counter itself fails, questions are allowed (fail open) and the
   `RateLimitErrors` metric records it.

Cached answers count too: the limit is about requests, and a cached answer still costs a
Lambda run. A paid WAF rate rule per IP would add protection before sign-in; it is left out
to stay on the free tier.

## Known limitations

* Scanned PDFs have no text layer and are marked FAILED; add Amazon Textract for OCR.
* No response streaming (API Gateway HTTP API + Lambda buffer responses).
* DOCX tables are appended after body text rather than kept in place.
* Dependency ranges are not pinned to exact versions; run `pip freeze > requirements.lock` before a real release.
