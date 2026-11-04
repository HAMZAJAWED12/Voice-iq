"""The constrained JSON schemas and the canonical-JSON hash helper (bake-off harness H2).

Implements:

- S2 §3.8, structured output: a "constrained JSON schema in which `sourceId` and `segmentIds` are free strings, not an
  enum of the supplied ids". Both schemas are sent as `response_format.json_schema.schema`.
- S2 §3.10 and PHASE-0 §2.2, the extraction shape: `claims[]` with `segmentIds` and `nonCheckable[]` with `reasonKind`.
  The key sets equal `extraction.py`'s validator key sets exactly (H1 AMB-19). The validator stays stricter on values:
  non-blank text, segment membership and de-duplication are not grammar matters.
- S2 §4, the reasoning probe: it "measures only reasoning-call latency and citation ids", so the model writes a
  verdict, a reason and the cited source ids, and nothing else (`probe.py`).

Both schemas use only the keywords in `SCHEMA_KEYWORDS`, every one of which the pinned llama.cpp
`json-schema-to-grammar` supports. Keywords it ignores, such as `uniqueItems`, are never used.

Key order. The pinned grammar emits required properties first, then optional ones, each in the schema's `properties`
order, and the server keeps the request's key order. `canonical_json` sorts keys and the wire bytes are the canonical
bytes, so constrained output must use this order, and H5's prompts must show it:

- top level: `claims`, `nonCheckable`;
- claims item: `segmentIds`, `text`, then optional `checkability`, `claimId`, `speakerId`;
- checkability: `isCheckable`, then optional `reason`;
- nonCheckable item: `reasonKind`, `segmentIds`, then optional `claimId`, `reason`, `speakerId`, `text`;
- probe: `citations`, `reason`, `verdict`;
- citation: `sourceId`.

The hashes pin the bytes: a schema edit must change the constant deliberately. `canonical_sha256` is also the helper for
every other freeze hash, H5's included. This module imports nothing from the rest of the package.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "minItems", "minLength", "enum", "const"}
)
EXTRACTION_SCHEMA_SHA256 = "47a47e9853f514936e9f19bfa0af94004c6637e06f382647b8226cdb5456c2b6"
PROBE_SCHEMA_SHA256 = "f015f3085ac4aca81fb13f078a0441a13f3978af5061084e0b427712f891b527"

_STRING: dict[str, Any] = {"type": "string"}
_SEGMENT_IDS: dict[str, Any] = {"type": "array", "items": _STRING, "minItems": 1}

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims", "nonCheckable"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["segmentIds", "text"],
                "properties": {
                    "checkability": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["isCheckable"],
                        "properties": {
                            "isCheckable": {"type": "boolean", "const": True},
                            "reason": _STRING,
                        },
                    },
                    "claimId": _STRING,
                    "segmentIds": _SEGMENT_IDS,
                    "speakerId": _STRING,
                    "text": {"type": "string", "minLength": 1},
                },
            },
        },
        "nonCheckable": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["reasonKind", "segmentIds"],
                "properties": {
                    "claimId": _STRING,
                    "reason": _STRING,
                    "reasonKind": {
                        "type": "string",
                        "enum": ["opinion", "prediction", "request", "vague", "outOfScope"],
                    },
                    "segmentIds": _SEGMENT_IDS,
                    "speakerId": _STRING,
                    "text": _STRING,
                },
            },
        },
    },
}

# NEEDS-OWNER(AMB-26): the model writes only verdict, reason and citations[{sourceId}]
_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["citations", "reason", "verdict"],
    "properties": {
        # NEEDS-OWNER(AMB-27): an empty citations list is valid output and fails gate C only
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sourceId"],
                "properties": {"sourceId": _STRING},
            },
        },
        "reason": {"type": "string", "minLength": 1},
        "verdict": {
            "type": "string",
            "enum": ["SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE"],
        },
    },
}


def _check_keys(value: object) -> None:
    """`json.dumps` turns `{1: "x"}` into `{"1": "x"}`, so two different values could share a hash."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be strings, not {type(key).__name__}")
            _check_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_keys(item)


def canonical_json(value: object) -> bytes:
    """The one JSON form of `value`: UTF-8, sorted keys, no spaces. The client sends exactly these bytes.

    A non-str key or a non-JSON type (set, bytes, mapping proxy) raises `TypeError`, NaN and infinities raise
    `ValueError`, and a lone surrogate raises `UnicodeEncodeError`. Tuples serialize as arrays.
    """
    # NEEDS-OWNER(AMB-28): sorted keys fix the key order the grammar forces
    _check_keys(value)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")


def canonical_sha256(value: object) -> str:
    """The SHA-256 of `canonical_json(value)`, as 64 lowercase hex characters."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def extraction_schema() -> dict[str, Any]:
    """The extraction schema as a fresh plain copy, so nothing mutable escapes this module."""
    return json.loads(canonical_json(_EXTRACTION_SCHEMA))


def probe_schema() -> dict[str, Any]:
    """The reasoning-probe schema as a fresh plain copy, so nothing mutable escapes this module."""
    return json.loads(canonical_json(_PROBE_SCHEMA))
