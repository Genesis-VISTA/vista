"""
Fernet encryption for sensitive user token fields (testing roadmap Milestone C).

`EncryptedStr` encrypts on write and decrypts on read. These tests pin that
round-trip and that the raw DB value is ciphertext, not plaintext.
"""

import uuid

import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import text
from sqlmodel import select

from vista_backend.config import settings
from vista_backend.db.schemas import UserTable
from vista_backend.utils.crypto import EncryptedStr, get_fernet

pytestmark = pytest.mark.unit


@pytest.fixture
def ephemeral_key(monkeypatch):
    """
    An ephemeral Fernet key for the duration of the test.

    `get_fernet` is `@functools.cache`d, so the cache must be cleared after
    patching settings — otherwise a prior call keeps the old key.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", SecretStr(key))
    get_fernet.cache_clear()
    yield key
    get_fernet.cache_clear()


# ---------------------------------------------------------------------------
# Column type round-trip
# ---------------------------------------------------------------------------


def test_encrypted_str_round_trips_plaintext(ephemeral_key):
    col = EncryptedStr()
    stored = col.process_bind_param("s3m-secret-token", dialect=None)
    assert stored is not None
    assert stored != "s3m-secret-token"
    assert col.process_result_value(stored, dialect=None) == "s3m-secret-token"


def test_encrypted_str_passes_none_through(ephemeral_key):
    col = EncryptedStr()
    assert col.process_bind_param(None, dialect=None) is None
    assert col.process_result_value(None, dialect=None) is None


def test_encrypted_str_ciphertext_is_fernet(ephemeral_key):
    """Stored value is decryptable by the same Fernet key the column uses."""
    col = EncryptedStr()
    stored = col.process_bind_param("nersc-iri-token", dialect=None)
    assert Fernet(ephemeral_key.encode()).decrypt(stored.encode()).decode() == (
        "nersc-iri-token"
    )


def test_encrypted_str_rejects_a_different_key(ephemeral_key):
    col = EncryptedStr()
    stored = col.process_bind_param("s3m-token", dialect=None)
    other = Fernet(Fernet.generate_key())
    with pytest.raises(InvalidToken):
        other.decrypt(stored.encode())


def test_get_fernet_is_cached_until_cleared(ephemeral_key, monkeypatch):
    first = get_fernet()
    monkeypatch.setattr(
        settings, "encryption_key", SecretStr(Fernet.generate_key().decode())
    )
    # Without cache_clear the old instance is still returned.
    assert get_fernet() is first
    get_fernet.cache_clear()
    assert get_fernet() is not first


# ---------------------------------------------------------------------------
# User token fields at rest
# ---------------------------------------------------------------------------

TOKEN_FIELDS = ("s3m_token", "nersc_iri_token")


@pytest.mark.anyio
@pytest.mark.parametrize("field", TOKEN_FIELDS)
async def test_user_token_is_encrypted_at_rest(ephemeral_key, session, field):
    plaintext = f"plaintext-{field}-value"
    row = UserTable(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@ornl.gov",
        **{field: plaintext},
    )
    session.add(row)
    await session.flush()

    # ORM read returns plaintext.
    loaded = (await session.exec(select(UserTable).where(UserTable.id == row.id))).one()
    assert getattr(loaded, field) == plaintext

    # Raw SQL sees ciphertext — never the plaintext token.
    # SQLite stores UUIDs as 32-char hex (no hyphens).
    raw = (
        await session.execute(
            text(f"SELECT {field} FROM app_user WHERE id = :id"),
            {"id": row.id.hex},
        )
    ).scalar_one()
    assert raw is not None
    assert raw != plaintext
    assert plaintext not in raw
    # And the ciphertext decrypts back under the same key.
    assert Fernet(ephemeral_key.encode()).decrypt(raw.encode()).decode() == plaintext


@pytest.mark.anyio
async def test_null_token_fields_stay_null_at_rest(ephemeral_key, session):
    row = UserTable(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@ornl.gov",
        s3m_token=None,
        nersc_iri_token=None,
    )
    session.add(row)
    await session.flush()

    raw = (
        await session.execute(
            text("SELECT s3m_token, nersc_iri_token FROM app_user WHERE id = :id"),
            {"id": row.id.hex},
        )
    ).one()
    assert raw == (None, None)


@pytest.mark.anyio
async def test_updating_a_token_rewrites_ciphertext(ephemeral_key, session):
    row = UserTable(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@ornl.gov",
        s3m_token="first-token",
    )
    session.add(row)
    await session.flush()

    first_cipher = (
        await session.execute(
            text("SELECT s3m_token FROM app_user WHERE id = :id"),
            {"id": row.id.hex},
        )
    ).scalar_one()

    row.s3m_token = "second-token"
    session.add(row)
    await session.flush()

    second_cipher = (
        await session.execute(
            text("SELECT s3m_token FROM app_user WHERE id = :id"),
            {"id": row.id.hex},
        )
    ).scalar_one()
    assert first_cipher != second_cipher
    assert (
        Fernet(ephemeral_key.encode()).decrypt(second_cipher.encode()).decode()
        == "second-token"
    )
