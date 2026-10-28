"""Check which Bedrock models this account can call in a region (costs a fraction of a cent).

    python scripts/check_bedrock.py                    # region from AWS_REGION or ap-southeast-2
    python scripts/check_bedrock.py --region us-east-1

Each chat model gets a 5-token "Say OK" request and the embedding model embeds one word,
so the result shows real access, not just what the docs list.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

CHAT_MODELS = [
    ("amazon.nova-2-lite-v1:0", "answer model (in-region)"),
    ("global.amazon.nova-2-lite-v1:0", "answer model (global profile)"),
    ("amazon.nova-micro-v1:0", "helper model (in-region)"),
    ("apac.amazon.nova-micro-v1:0", "helper model (APAC profile)"),
    ("amazon.nova-lite-v1:0", "old answer model (legacy)"),
    ("us.amazon.nova-lite-v1:0", "old answer model (US profile, old config)"),
]
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

HINTS = {
    "AccessDeniedException": "no access (model not enabled, or blocked for this account/plan)",
    "ValidationException": "not available with this ID in this region",
    "ResourceNotFoundException": "model not found (retired or wrong ID)",
    "ThrottlingException": "throttled: wait a minute and run again",
}


def explain(error: Exception) -> str:
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "Error")
        message = error.response.get("Error", {}).get("Message", "")
        hint = HINTS.get(code)
        return f"{code}: {message[:160]}" + (f"\n        -> {hint}" if hint else "")
    return f"{type(error).__name__}: {str(error)[:110]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-southeast-2"))
    args = parser.parse_args()
    # One attempt per model: we want to see the real error, not wait through retries.
    client = boto3.client(
        "bedrock-runtime", region_name=args.region, config=Config(retries={"max_attempts": 1})
    )
    print(f"Region: {args.region}\n")

    working: list[str] = []
    for model_id, role in CHAT_MODELS:
        started = time.perf_counter()
        try:
            client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
                inferenceConfig={"maxTokens": 5, "temperature": 0},
            )
            ms = int((time.perf_counter() - started) * 1000)
            print(f"  OK    {model_id:<36} {role} ({ms} ms)")
            working.append(model_id)
        except (ClientError, BotoCoreError) as error:
            print(f"  FAIL  {model_id:<36} {role}\n        {explain(error)}")

    try:
        response = client.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=json.dumps({"inputText": "fibre", "dimensions": 1024, "normalize": True}),
        )
        size = len(json.loads(response["body"].read())["embedding"])
        print(f"  OK    {EMBEDDING_MODEL:<36} embeddings ({size} dimensions)")
    except (ClientError, BotoCoreError) as error:
        print(f"  FAIL  {EMBEDDING_MODEL:<36} embeddings\n        {explain(error)}")

    print("\nWorking chat models:", ", ".join(working) or "none")


if __name__ == "__main__":
    main()
