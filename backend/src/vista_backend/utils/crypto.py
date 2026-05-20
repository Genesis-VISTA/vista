from cryptography.fernet import Fernet
from sqlalchemy.types import TypeDecorator, String
import functools
from ..config import settings

@functools.cache
def get_fernet() -> Fernet:
    return Fernet(settings.encryption_key.get_secret_value().encode())


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
