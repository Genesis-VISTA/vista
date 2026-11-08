"""
Fernet encryption for sensitive settings token fields (testing roadmap Milestone C).

`EncryptedStr` encrypts on write and decrypts on read. These tests pin that
round-trip and that the raw DB value is ciphertext, not plaintext.
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import text
from sqlmodel import select

from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsTable
from vista_backend.services import local_settings
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
    stored = col.process_bind_param("globus-token", dialect=None)
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
# Settings token fields at rest
# ---------------------------------------------------------------------------

TOKEN_FIELDS = (
    "odo_s3m_token",
    "frontier_s3m_token",
    "nersc_iri_token",
    "globus_token",
)


async def _raw(session, columns: str):
    """The settings row's stored values, as SQLite holds them."""
    return (
        await session.execute(
            text(f"SELECT {columns} FROM local_settings WHERE id = 1")
        )
    ).one()


@pytest.mark.anyio
@pytest.mark.parametrize("field", TOKEN_FIELDS)
async def test_settings_token_is_encrypted_at_rest(ephemeral_key, session, field):
    plaintext = f"plaintext-{field}-value"
    row = await local_settings.get(session)
    setattr(row, field, plaintext)
    session.add(row)
    await session.flush()

    # ORM read returns plaintext.
    loaded = (await session.exec(select(LocalSettingsTable))).one()
    assert getattr(loaded, field) == plaintext

    # Raw SQL sees ciphertext — never the plaintext token.
    (raw,) = await _raw(session, field)
    assert raw is not None
    assert raw != plaintext
    assert plaintext not in raw
    # And the ciphertext decrypts back under the same key.
    assert Fernet(ephemeral_key.encode()).decrypt(raw.encode()).decode() == plaintext


@pytest.mark.anyio
async def test_null_token_fields_stay_null_at_rest(ephemeral_key, session):
    raw = await _raw(session, "odo_s3m_token, nersc_iri_token, globus_token")
    assert tuple(raw) == (None, None, None)


@pytest.mark.anyio
async def test_updating_a_token_rewrites_ciphertext(ephemeral_key, session):
    row = await local_settings.get(session)
    row.odo_s3m_token = "first-token"
    session.add(row)
    await session.flush()
    (first_cipher,) = await _raw(session, "odo_s3m_token")

    row.odo_s3m_token = "second-token"
    session.add(row)
    await session.flush()
    (second_cipher,) = await _raw(session, "odo_s3m_token")

    assert first_cipher != second_cipher
    assert (
        Fernet(ephemeral_key.encode()).decrypt(second_cipher.encode()).decode()
        == "second-token"
    )
