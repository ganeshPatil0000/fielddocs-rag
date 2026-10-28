"""Post-generation checks that catch ungrounded answers before users see them."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.rag.prompts import NO_ANSWER

_CITATION = re.compile(r"\[(\d{1,2})\]")


@dataclass
class ValidationResult:
    answer: str
    cited: list[int] = field(default_factory=list)
    invalid_citations: list[int] = field(default_factory=list)
    is_refusal: bool = False
    grounded: bool = True
    reason: str = ""


def is_refusal(answer: str) -> bool:
    return NO_ANSWER[:40].lower() in answer.lower()


def validate_citations(answer: str, num_sources: int, strict: bool = True) -> ValidationResult:
    """Check that the answer cites only sources that exist, and cites at least one.

    * Out-of-range citations (``[7]`` when 5 sources were given) are stripped -
      they are the classic sign of a model inventing a reference.
    * An answer with no valid citation is treated as ungrounded; in strict mode
      it is replaced with the safe NO_ANSWER message.
    """
    answer = answer.strip()
    if is_refusal(answer):
        return ValidationResult(answer=NO_ANSWER, is_refusal=True)

    numbers = [int(n) for n in _CITATION.findall(answer)]
    valid = sorted({n for n in numbers if 1 <= n <= num_sources})
    invalid = sorted({n for n in numbers if not 1 <= n <= num_sources})
    cleaned = answer
    for n in invalid:
        cleaned = cleaned.replace(f"[{n}]", "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    if not valid:
        if strict:
            return ValidationResult(
                answer=NO_ANSWER,
                invalid_citations=invalid,
                is_refusal=True,
                grounded=False,
                reason="answer had no valid citations",
            )
        return ValidationResult(
            answer=cleaned, invalid_citations=invalid, grounded=False, reason="no citations"
        )
    return ValidationResult(answer=cleaned, cited=valid, invalid_citations=invalid)


def parse_grounding_verdict(raw: str) -> tuple[bool, str]:
    """Parse the judge model's JSON; fail closed on anything unparseable."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return False, "unparseable grounding verdict"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return False, "unparseable grounding verdict"
    return bool(data.get("grounded")), str(data.get("reason", ""))[:200]
