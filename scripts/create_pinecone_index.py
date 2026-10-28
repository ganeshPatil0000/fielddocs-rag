"""Create the serverless Pinecone indexes (idempotent).

Dense index (semantic search, vectors from Bedrock Titan):

    PINECONE_API_KEY=... python scripts/create_pinecone_index.py --name fielddocs --dimension 1024

Sparse index (keyword half of hybrid search; Pinecone embeds the text itself
with its hosted pinecone-sparse-english-v0 model):

    PINECONE_API_KEY=... python scripts/create_pinecone_index.py --sparse --name fielddocs-sparse

The dense dimension must equal EMBEDDING_DIMENSIONS (Titan V2: 256 / 512 / 1024).
Pinecone's free Starter plan only offers AWS us-east-1.
"""

import argparse
import os
import time

from pinecone import Pinecone, ServerlessSpec

SPARSE_MODEL = "pinecone-sparse-english-v0"
SPARSE_TEXT_FIELD = "chunk_text"  # must match app.keyword_index.TEXT_FIELD


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default=None)
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--sparse", action="store_true", help="create the keyword (sparse) index for hybrid search"
    )
    args = parser.parse_args()
    if args.name is None:
        args.name = (
            os.environ.get("PINECONE_SPARSE_INDEX", "fielddocs-sparse")
            if args.sparse
            else os.environ.get("PINECONE_INDEX", "fielddocs")
        )

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    if pc.has_index(args.name):
        print(f"index '{args.name}' already exists:", pc.describe_index(args.name))
        return

    if args.sparse:
        pc.create_index_for_model(
            name=args.name,
            cloud="aws",
            region=args.region,
            embed={"model": SPARSE_MODEL, "field_map": {"text": SPARSE_TEXT_FIELD}},
            deletion_protection="enabled",
        )
        kind = f"sparse, {SPARSE_MODEL}"
    else:
        pc.create_index(
            name=args.name,
            dimension=args.dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region=args.region),
            deletion_protection="enabled",
        )
        kind = f"{args.dimension} dims, cosine"
    while not pc.describe_index(args.name).status["ready"]:
        time.sleep(2)
    print(f"created index '{args.name}' ({kind}, aws/{args.region})")


if __name__ == "__main__":
    main()
