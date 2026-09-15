from cryptography.fernet import Fernet

from app.core.config import settings

_fernet = Fernet(settings.token_encryption_key.encode())


def encrypt_token(plain_text: str) -> str:
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt_token(encrypted_text: str) -> str:
    return _fernet.decrypt(encrypted_text.encode()).decode()