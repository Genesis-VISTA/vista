"""
A packaged install encrypts stored secrets with its own key from the OS secret
store, not the public development key, and moves existing values to it once.
"""

import keyring
import pytest
from cryptography.fernet import Fernet
from keyring.backends import fail
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from vista_backend.config import settings
from vista_backend.db.schemas import UserTable
from vista_backend.utils import crypto, secret_store

pytestmark = pytest.mark.unit


@pytest.fixture
def packaged(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", SecretStr(crypto.DUMMY_KEY))
    monkeypatch.setattr(settings, "encryption_key_in_secret_store", True)
    crypto.get_fernet.cache_clear()
    yield
    crypto.get_fernet.cache_clear()


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'vista.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _raw_token(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT github_token FROM app_user")).scalar_one()


def test_a_packaged_install_creates_its_own_key(packaged):
    crypto.get_fernet()
    key = secret_store.get(secret_store.DB_ENCRYPTION_KEY)
    assert key and key != crypto.DUMMY_KEY


def test_values_written_under_the_dummy_key_are_moved_once(monkeypatch, tmp_path):
    # Written by an earlier version, on the development key.
    monkeypatch.setattr(settings, "encryption_key", SecretStr(crypto.DUMMY_KEY))
    monkeypatch.setattr(settings, "encryption_key_in_secret_store", False)
    crypto.get_fernet.cache_clear()
    engine = _db(tmp_path)
    with Session(engine) as s:
        user = UserTable(email="a@example.com", github_token="ghp-secret")
        s.add(user)
        s.commit()
        user_id = user.id

    # The new version starts.
    monkeypatch.setattr(settings, "encryption_key_in_secret_store", True)
    crypto.get_fernet.cache_clear()
    try:
        with engine.begin() as conn:
            assert crypto.reencrypt_legacy_values(conn) == 1
        with engine.begin() as conn:
            assert crypto.reencrypt_legacy_values(conn) == 0

        own = Fernet(secret_store.get(secret_store.DB_ENCRYPTION_KEY).encode())
        assert own.decrypt(_raw_token(engine).encode()) == b"ghp-secret"
        with Session(engine) as s:
            assert s.get(UserTable, user_id).github_token == "ghp-secret"
    finally:
        crypto.get_fernet.cache_clear()


def test_an_explicit_key_is_used_as_is(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", SecretStr(key))
    monkeypatch.setattr(settings, "encryption_key_in_secret_store", True)
    crypto.get_fernet.cache_clear()
    try:
        token = crypto.get_fernet().encrypt(b"x")
        assert Fernet(key.encode()).decrypt(token) == b"x"
        assert secret_store.get(secret_store.DB_ENCRYPTION_KEY) is None
    finally:
        crypto.get_fernet.cache_clear()


def test_without_a_secret_store_nothing_changes(packaged, tmp_path):
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    crypto.get_fernet.cache_clear()
    try:
        token = crypto.get_fernet().encrypt(b"x")
        assert Fernet(crypto.DUMMY_KEY.encode()).decrypt(token) == b"x"
        with _db(tmp_path).begin() as conn:
            assert crypto.reencrypt_legacy_values(conn) == 0
    finally:
        keyring.set_keyring(previous)
