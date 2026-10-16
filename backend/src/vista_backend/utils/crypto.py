from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Connection, select, type_coerce, update
from sqlalchemy.types import TypeDecorator, String
from sqlmodel import SQLModel
import functools
import logging

log = logging.getLogger(__name__)

# The development default for `settings.encryption_key`. It is public, so a
# database encrypted with it is only obfuscated.
DUMMY_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _install_key() -> str | None:
    """
    This install's own key, from the OS secret store, created on first use.

    None where there is no secret store, which leaves the database on the
    development key exactly as before.
    """
    from . import secret_store

    ok, why = secret_store.available()
    if not ok:
        log.warning("Database secrets use the development key: %s.", why)
        return None
    key = secret_store.get(secret_store.DB_ENCRYPTION_KEY)
    if key:
        return key
    key = Fernet.generate_key().decode()
    try:
        secret_store.put(secret_store.DB_ENCRYPTION_KEY, key)
    except secret_store.SecretStoreUnavailable as exc:
        log.warning("Database secrets use the development key: %s.", exc)
        return None
    log.info("Created this install's database encryption key in the OS secret store.")
    return key


def _uses_install_key() -> bool:
    from ..config import settings

    return (
        settings.encryption_key.get_secret_value() == DUMMY_KEY
        and settings.encryption_key_in_secret_store
    )


@functools.cache
def get_fernet() -> MultiFernet:
    """
    The cipher for `EncryptedStr`.

    A key set in the environment is used as is. A packaged install, which sets
    none, uses its own key from the OS secret store
    (`settings.encryption_key_in_secret_store`), and keeps the development key
    only to read values written before it had one (see `reencrypt_legacy_values`).
    """
    from ..config import settings

    if _uses_install_key():
        own = _install_key()
        if own is not None:
            return MultiFernet([Fernet(own.encode()), Fernet(DUMMY_KEY.encode())])
    return MultiFernet([Fernet(settings.encryption_key.get_secret_value().encode())])


class EncryptedStr(TypeDecorator):
    """SQLAlchemy column type that transparently Fernet-encrypts on write and decrypts on read."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return get_fernet().decrypt(value.encode()).decode()


def reencrypt_legacy_values(conn: Connection) -> int:
    """
    Re-encrypt every value still readable with the development key under the
    current key, so none stays on disk under a public one. Returns how many.

    Runs in `init_db`'s transaction: it all happens or none of it does.
    """
    from . import secret_store

    if not _uses_install_key():
        return 0
    current = get_fernet()  # creates the install key on first start
    if not secret_store.get(secret_store.DB_ENCRYPTION_KEY):
        return 0  # No secret store: still on the key the values were written with.
    legacy = Fernet(DUMMY_KEY.encode())
    count = 0
    for table in SQLModel.metadata.sorted_tables:
        columns = [c for c in table.columns if isinstance(c.type, EncryptedStr)]
        keys = list(table.primary_key.columns)
        if not columns or not keys:
            continue
        for column in columns:
            raw = type_coerce(column, String)  # the stored ciphertext, undecrypted
            rows = conn.execute(select(*keys, raw).where(column.is_not(None))).all()
            for row in rows:
                *ids, value = row
                try:
                    plain = legacy.decrypt(value.encode())  # fails under the new key
                except InvalidToken:
                    continue  # Already under the current key.
                conn.execute(
                    update(table)
                    .where(*(k == v for k, v in zip(keys, ids, strict=True)))
                    .values(
                        {
                            column.name: type_coerce(
                                current.encrypt(plain).decode(), String
                            )
                        }
                    )
                )
                count += 1
    if count:
        log.info("Re-encrypted %d stored secrets under this install's key.", count)
    return count
