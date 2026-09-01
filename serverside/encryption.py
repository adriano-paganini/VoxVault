import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)


FILEPATH = os.getenv("VOXVAULT_PRIVATE_KEY_PATH", "voxvault_private.key")
HOST_FILEPATH = os.getenv("VOXVAULT_PRIVATE_KEY_HOST_PATH", FILEPATH)


def _key_path():
    path = Path(FILEPATH)

    if path.is_dir():
        raise IsADirectoryError(
            f"Configured key path is a directory, expected a file: {FILEPATH}"
        )

    return path


def key_exists():
    return _key_path().is_file()


def private_key_path():
    return FILEPATH


def private_key_host_path():
    return HOST_FILEPATH


def _encode_key(raw_key):
    return base64.b64encode(raw_key).decode("ascii")


def _private_key_bytes(private_key):
    return private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )


def _public_key_text(public_key):
    public_bytes = public_key.public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return _encode_key(public_bytes)


def _load_private_key():
    private_bytes = _key_path().read_bytes()
    return x25519.X25519PrivateKey.from_private_bytes(private_bytes)


def create_encryption_keys():
    if key_exists():
        return {
            "created": False,
            "privateKeyPath": private_key_path(),
            "privateKeyHostPath": private_key_host_path(),
            "publicKey": get_public_key(),
        }

    private_key = x25519.X25519PrivateKey.generate()
    private_bytes = _private_key_bytes(private_key)
    public_key = private_key.public_key()
    key_path = _key_path()

    if key_path.parent != Path("."):
        key_path.parent.mkdir(parents=True, exist_ok=True)

    key_path.write_bytes(private_bytes)

    return {
        "created": True,
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
        "privateKey": _encode_key(private_bytes),
        "publicKey": _public_key_text(public_key),
    }


def get_public_key():
    private_key = _load_private_key()
    return _public_key_text(private_key.public_key())
