"""Create the serverless Pinecone index (idempotent).

    PINECONE_API_KEY=... python scripts/create_pinecone_index.py --name fielddocs --dimension 1024

The dimension must equal EMBEDDING_DIMENSIONS (Titan V2: 256 / 512 / 1024).
Pinecone's free Starter plan only offers AWS us-east-1.
"""

import argparse
import os
import time

from pinecone import Pinecone, ServerlessSpec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default=os.environ.get("PINECONE_INDEX", "fielddocs"))
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    if pc.has_index(args.name):
        print(f"index '{args.name}' already exists:", pc.describe_index(args.name))
        return
    pc.create_index(
        name=args.name,
        dimension=args.dimension,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region=args.region),
        deletion_protection="enabled",
    )
    while not pc.describe_index(args.name).status["ready"]:
        time.sleep(2)
    print(f"created index '{args.name}' ({args.dimension} dims, cosine, aws/{args.region})")


if __name__ == "__main__":
    main()
