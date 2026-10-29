"""Text cleaning and chunking.

Chunking choices (and why):
* ~1000 characters (~250 tokens) with 150 overlap: small enough that five
  chunks fit comfortably in a cheap model's prompt, big enough to keep a full
  procedure step together.
* Markdown headings are split on first, and the heading path is prepended to
  every chunk ("Manual > Section > Subsection"). A chunk that says "torque to
  12 Nm" is useless without knowing *which* component - the header supplies it
  and measurably improves retrieval for short chunks.
* Deterministic chunk ids make re-ingestion idempotent. Each version of a
  document has its own ids (``<doc_id>#<n>`` for version 1, ``<doc_id>#v2#<n>``
  after the first replace), so a new version is written next to the old one
  and the switch happens only when it is complete.
"""

from __future__ import annotations

import hashlib
import re

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join hyphenated line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _has_markdown_headings(text: str) -> bool:
    return bool(re.search(r"^#{1,3} \S", text, re.M))


def chunk_documents(
    docs: list[Document],
    doc_id: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
    version: int = 1,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS, strip_headers=True)

    chunks: list[Document] = []
    for doc in docs:
        text = clean_text(doc.page_content)
        if not text:
            continue
        sections = (
            header_splitter.split_text(text)
            if _has_markdown_headings(text)
            else [Document(page_content=text)]
        )
        for section in sections:
            heading_path = " > ".join(
                section.metadata[k] for k in ("h1", "h2", "h3") if section.metadata.get(k)
            )
            for piece in splitter.split_text(section.page_content):
                if len(piece.strip()) < 20:  # drop stray fragments
                    continue
                header = f"{doc.metadata.get('source', '')}"
                if heading_path:
                    header += f" > {heading_path}"
                meta = {
                    **doc.metadata,
                    "doc_id": doc_id,
                    "doc_version": version,
                    "section": heading_path or None,
                }
                chunks.append(Document(page_content=f"[{header}]\n{piece.strip()}", metadata=meta))

    for idx, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = idx
        chunk.metadata["chunk_hash"] = hashlib.sha256(chunk.page_content.encode()).hexdigest()[:16]
    return chunks


def chunk_ids(doc_id: str, count: int, start: int = 0, version: int = 1) -> list[str]:
    # Version 1 keeps the original "<doc_id>#<n>" ids, so documents indexed before
    # versioning existed need no migration.
    prefix = doc_id if version <= 1 else f"{doc_id}#v{version}"
    return [f"{prefix}#{i}" for i in range(start, count)]


def chunk_version(metadata: dict) -> int:
    """Document version a stored chunk belongs to (chunks from before versioning are 1).

    Pinecone returns numbers as floats, so 2.0 is read back as 2.
    """
    try:
        return int(float(metadata.get("doc_version", 1)))
    except (TypeError, ValueError):
        return 1
