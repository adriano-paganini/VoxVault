import base64
from config import settings
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)
from io import BytesIO

import tink
from tink import BinaryKeysetReader, cleartext_keyset_handle
from tink import hybrid, streaming_aead
from tink.proto import hpke_pb2, tink_pb2


hybrid.register()
streaming_aead.register()

FILEPATH = str(settings.private_key_path)


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
    return FILEPATH

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

def _load_hpke_private_keyset_handle():
    private_bytes = _key_path().read_bytes()
    private_key = x25519.X25519PrivateKey.from_private_bytes(private_bytes)
    public_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    hpke_private_key = hpke_pb2.HpkePrivateKey(
        version=0,
        public_key=hpke_pb2.HpkePublicKey(
            version=0,
            params=hpke_pb2.HpkeParams(
                kem=hpke_pb2.DHKEM_X25519_HKDF_SHA256,
                kdf=hpke_pb2.HKDF_SHA256,
                aead=hpke_pb2.AES_256_GCM,
            ),
            public_key=public_bytes,
        ),
        private_key=private_bytes,
    )

    keyset = tink_pb2.Keyset(
        primary_key_id=1,
        key=[
            tink_pb2.Keyset.Key(
                key_id=1,
                status=tink_pb2.ENABLED,
                output_prefix_type=tink_pb2.RAW,
                key_data=tink_pb2.KeyData(
                    type_url="type.googleapis.com/google.crypto.tink.HpkePrivateKey",
                    value=hpke_private_key.SerializeToString(),
                    key_material_type=tink_pb2.KeyData.ASYMMETRIC_PRIVATE,
                ),
            )
        ],
    )

    return cleartext_keyset_handle.from_keyset(keyset)

def decrypt_symmetric_key(symmetric_key):
    hpke_handle = _load_hpke_private_keyset_handle()
    hybrid_decrypt = hpke_handle.primitive(hybrid.HybridDecrypt)

    decoded_symmetric_encryption_key = base64.b64decode(symmetric_key)

    serialized_symmetric_keyset = hybrid_decrypt.decrypt(
        decoded_symmetric_encryption_key,
        b"",
    )
    return serialized_symmetric_keyset

def decrypt_data(decoded_encrypted_data, serialized_symmetric_keyset):

    symmetric_handle = cleartext_keyset_handle.read(
        BinaryKeysetReader(serialized_symmetric_keyset)
    )
    streaming_decrypt = symmetric_handle.primitive(streaming_aead.StreamingAead)

    with streaming_decrypt.new_decrypting_stream(
        BytesIO(decoded_encrypted_data),
        b"",
    ) as plaintext_stream:
        return plaintext_stream.read()
