"""
crypto_fields.py
AES-256-GCM column-level encryption for SQLAlchemy models (MedFlow).

Key source(s):
    CLINICFLOW_FIELD_KEY        -> base64, 32 bytes. "current" key (id 0), used for
                                    ALL new encryptions.
    CLINICFLOW_FIELD_KEY_MAP    -> optional. JSON, base64, or "id:b64key,id:b64key,..."
                                    listing OLDER keys by numeric id, so already-encrypted
                                    rows can still be decrypted after rotation.

Storage layout per cell (all base64-encoded in the DB column):
    b64( 1-byte key_id || 12-byte nonce || ciphertext || 16-byte GCM tag )

Generate a key once:
    python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"

--------------------------------------------------------------------
KEY ROTATION
--------------------------------------------------------------------
1. Generate a new key, set it as CLINICFLOW_FIELD_KEY (new key_id = current_id + 1
   is picked automatically — see KeyRing).
2. Move the OLD key into CLINICFLOW_FIELD_KEY_MAP so old rows still decrypt:
       CLINICFLOW_FIELD_KEY_MAP='{"0": "<old_base64_key>"}'
3. Deploy. New writes use the new key; old rows still readable via the map.
4. (Optional) run `rewrap_all_rows()` / the provided migration helper to
   re-encrypt every row with the new key, then drop the old key from the map.
--------------------------------------------------------------------
"""

import os
import json
import base64
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.types import TypeDecorator, String

logger = logging.getLogger("medflow.security.crypto_fields")

NONCE_SIZE = 12  # 96-bit nonce, standard for GCM
KEY_ID_SIZE = 1  # supports up to 256 historical key versions


class FieldEncryptionError(RuntimeError):
    """Raised on key configuration or crypto failures for encrypted fields."""


class KeyRing:
    """
    Holds the current signing/encryption key plus any older keys needed to
    decrypt previously-written rows after a rotation.

    Loaded lazily (first use), not at import time, so this module can be
    imported before env vars are configured (e.g. Alembic autogenerate).
    """

    _current_id: int | None = None
    _current_key: bytes | None = None
    _old_keys: dict[int, bytes] = {}
    _loaded: bool = False

    @classmethod
    def _decode_key(cls, raw: str, label: str) -> bytes:
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise FieldEncryptionError(f"{label} is not valid base64.") from exc
        if len(key) != 32:
            raise FieldEncryptionError(
                f"{label} must decode to 32 bytes (AES-256), got {len(key)}."
            )
        return key

    @classmethod
    def load(cls, force: bool = False):
        if cls._loaded and not force:
            return

        raw_current = os.environ.get("CLINICFLOW_FIELD_KEY")
        if not raw_current:
            raise FieldEncryptionError(
                "CLINICFLOW_FIELD_KEY is not set. Column-level encryption cannot start."
            )
        cls._current_key = cls._decode_key(raw_current, "CLINICFLOW_FIELD_KEY")

        old_keys: dict[int, bytes] = {}
        raw_map = os.environ.get("CLINICFLOW_FIELD_KEY_MAP")
        if raw_map:
            try:
                parsed = json.loads(raw_map)
            except json.JSONDecodeError:
                # fallback: "0:b64key,1:b64key"
                parsed = {}
                for pair in raw_map.split(","):
                    if not pair.strip():
                        continue
                    k, v = pair.split(":", 1)
                    parsed[k.strip()] = v.strip()
            for k, v in parsed.items():
                key_id = int(k)
                old_keys[key_id] = cls._decode_key(v, f"CLINICFLOW_FIELD_KEY_MAP[{k}]")

        # current key id = 1 + max old id seen (defaults to 0 if no history yet)
        cls._current_id = (max(old_keys.keys()) + 1) if old_keys else 0
        cls._old_keys = old_keys
        cls._loaded = True

    @classmethod
    def current(cls) -> tuple[int, bytes]:
        cls.load()
        return cls._current_id, cls._current_key

    @classmethod
    def get(cls, key_id: int) -> bytes:
        cls.load()
        if key_id == cls._current_id:
            return cls._current_key
        if key_id in cls._old_keys:
            return cls._old_keys[key_id]
        raise FieldEncryptionError(
            f"No key available for key_id={key_id}. "
            "Add it to CLINICFLOW_FIELD_KEY_MAP to decrypt legacy rows."
        )

    @classmethod
    def reset(cls):
        """Test helper — forces re-read of env vars on next use."""
        cls._loaded = False
        cls._current_id = None
        cls._current_key = None
        cls._old_keys = {}


def _load_key() -> bytes:
    # kept for backward compatibility with earlier version of this module
    return KeyRing.current()[1]


class EncryptedString(TypeDecorator):
    """
    SQLAlchemy TypeDecorator that transparently encrypts/decrypts a string
    column using AES-256-GCM.

    Usage:
        phone = Column(EncryptedString(255))

    Notes:
    - AAD (associated data) binds the ciphertext to the table+column name,
      so a ciphertext copied into a different column/table will fail to
      decrypt (mitigates cut-and-paste / substitution attacks).
    - Key is loaded lazily on first use (not at import time), so the module
      can be imported before the environment variable is configured
      (e.g. during Alembic autogenerate).
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 255, aad_context: str | None = None, *args, **kwargs):
        # Encrypted output is longer than plaintext (nonce+tag+base64 overhead),
        # so the underlying DB column is sized generously.
        self.aad_context = aad_context  # e.g. "patient.phone", set via bind below
        super().__init__(*args, **kwargs)
        self.impl = String(length=max(length * 2, 512))

    def _aad(self) -> bytes:
        ctx = self.aad_context or "medflow.field"
        return ctx.encode("utf-8")

    def process_bind_param(self, value, dialect):
        """Python value -> encrypted value stored in DB."""
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)

        key_id, key = KeyRing.current()
        aesgcm = AESGCM(key)
        nonce = os.urandom(NONCE_SIZE)
        plaintext = value.encode("utf-8")

        try:
            ciphertext = aesgcm.encrypt(nonce, plaintext, self._aad())
        except Exception as exc:
            logger.error("Field encryption failed for context=%s", self.aad_context)
            raise FieldEncryptionError("Encryption failed.") from exc

        payload = key_id.to_bytes(KEY_ID_SIZE, "big") + nonce + ciphertext
        return base64.b64encode(payload).decode("ascii")

    def process_result_value(self, value, dialect):
        """Encrypted DB value -> Python value."""
        if value is None:
            return None

        try:
            raw = base64.b64decode(value)
            key_id = int.from_bytes(raw[:KEY_ID_SIZE], "big")
            nonce = raw[KEY_ID_SIZE:KEY_ID_SIZE + NONCE_SIZE]
            ciphertext = raw[KEY_ID_SIZE + NONCE_SIZE:]
            key = KeyRing.get(key_id)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, self._aad())
        except FieldEncryptionError:
            raise
        except Exception as exc:
            logger.error("Field decryption failed for context=%s", self.aad_context)
            raise FieldEncryptionError(
                "Decryption failed — wrong key, tampered data, or AAD mismatch."
            ) from exc

        return plaintext.decode("utf-8")

    def rewrap(self, value, dialect):
        """
        Decrypt a stored value (possibly under an old key) and re-encrypt it
        under the current key. Used by the rotation migration script.
        Returns the new DB-ready string, or None if value is None.
        """
        plain = self.process_result_value(value, dialect)
        if plain is None:
            return None
        return self.process_bind_param(plain, dialect)


class EncryptedText(EncryptedString):
    """Same as EncryptedString but for long free-text fields (e.g. medical_notes)."""

    def __init__(self, aad_context: str | None = None, *args, **kwargs):
        super().__init__(length=4000, aad_context=aad_context, *args, **kwargs)
        self.impl = String(8000)


# ---------------------------------------------------------------------------
# Blind index (deterministic HMAC) for exact-match search on encrypted fields
# ---------------------------------------------------------------------------
# AES-GCM is randomized (different nonce every time), so you CANNOT do
# `WHERE phone = ?` against an EncryptedString column. To allow exact-match
# lookup (e.g. "find patient by phone") without storing plaintext, store a
# second column holding an HMAC-SHA256 of the normalized value, computed with
# a SEPARATE key. Only equality search is possible this way — never do
# partial/LIKE search on a blind index, it leaks structure.
#
# Usage in models.py:
#
#   from crypto_fields import EncryptedString, blind_index
#
#   phone = Column(EncryptedString(32, aad_context="patient.phone"))
#   phone_bidx = Column(String(64), index=True)
#
#   # on write:
#   patient.phone = "+998901234567"
#   patient.phone_bidx = blind_index("+998901234567")
#
#   # on search:
#   session.query(Patient).filter(Patient.phone_bidx == blind_index(query_phone))
#
import hmac
import hashlib


def _load_bidx_key() -> bytes:
    raw = os.environ.get("CLINICFLOW_BLIND_INDEX_KEY")
    if not raw:
        raise FieldEncryptionError(
            "CLINICFLOW_BLIND_INDEX_KEY is not set. "
            "Generate with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise FieldEncryptionError("CLINICFLOW_BLIND_INDEX_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise FieldEncryptionError("CLINICFLOW_BLIND_INDEX_KEY must decode to 32 bytes.")
    return key


def blind_index(value: str, normalize: bool = True) -> str:
    """
    Deterministic HMAC-SHA256 of `value`, hex-encoded, for exact-match search
    on an otherwise-encrypted field. Uses CLINICFLOW_BLIND_INDEX_KEY (separate
    from the field-encryption key, so a leak of one doesn't compromise both).
    """
    if value is None:
        return None
    if normalize:
        value = value.strip().lower()
    key = _load_bidx_key()
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
