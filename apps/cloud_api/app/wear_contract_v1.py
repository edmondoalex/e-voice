"""Wear service contract v1; identical vendored copy in each component."""

import hashlib
import hmac
import re
from uuid import UUID


def import_proof(key: bytes, installation: UUID, generation: int, revision: str) -> str:
    if (
        not isinstance(key, bytes)
        or len(key) < 32
        or not isinstance(installation, UUID)
        or type(generation) is not int
        or not 0 <= generation < 2**63 - 1
        or not isinstance(revision, str)
        or not re.fullmatch(r"[0-9a-f]{64}", revision)
    ):
        raise ValueError("invalid_proof_input")
    message = (
        b"ekonex-wear-import-v1\0"
        + installation.bytes
        + generation.to_bytes(8, "big")
        + revision.encode()
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()
