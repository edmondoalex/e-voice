"""Staged secret providers; caller injects secret manager mapping, never logs secrets."""

import base64
import binascii
import hashlib
import hmac
from uuid import UUID


class KeyUnavailable(ValueError):
    pass


def _decode(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != 32:
            raise ValueError()
        return decoded
    except (ValueError, binascii.Error):
        raise KeyUnavailable("invalid_bridge_secret") from None


def _secrets(config, current_name, previous_name):
    current = _decode(config.get(current_name))
    previous = config.get(previous_name)
    if previous is None:
        return (current,)
    old = _decode(previous)
    if hmac.compare_digest(current, old):
        raise KeyUnavailable("duplicate_bridge_secret")
    return (current, old)


def cloud_provider(secret_config):
    masters = _secrets(secret_config, "wear_bridge_master_b64", "wear_bridge_previous_master_b64")

    async def provider(installation):
        if not isinstance(installation, UUID):
            return None
        keys = tuple(
            hmac.new(
                master, b"ekonex-wear-bridge-v1\0" + installation.bytes, hashlib.sha256
            ).digest()
            for master in masters
        )
        return keys if len(keys) > 1 else keys[0]

    return provider


def econtrol_provider(secret_config):
    keys = _secrets(
        secret_config, "wear_bridge_installation_key_b64", "wear_bridge_previous_key_b64"
    )
    try:
        installation = UUID(secret_config["wear_bridge_installation"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KeyUnavailable("invalid_bridge_installation") from None

    async def provider(requested):
        return (keys if len(keys) > 1 else keys[0]) if requested == installation else None

    return provider
