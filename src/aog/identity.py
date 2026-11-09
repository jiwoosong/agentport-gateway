"""Worker trust uses public keys: native agent processes never receive a signing secret."""

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def signing_key() -> Ed25519PrivateKey:
    path = Path(os.environ.get("AOG_WORKER_SIGNING_KEY_PATH", "data/worker-signing.key"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                output.write(key.private_bytes_raw())
        except FileExistsError:
            pass
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def public_key_base64() -> str:
    return base64.b64encode(signing_key().public_key().public_bytes_raw()).decode()


def verification_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(
        base64.b64decode(os.environ["AOG_WORKER_PUBLIC_KEY"], validate=True)
    )
