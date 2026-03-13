from cryptography.fernet import Fernet
from src.config.settings import get_settings


def _get_fernet() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
