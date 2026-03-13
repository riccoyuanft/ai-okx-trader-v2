import pyotp
import qrcode
import qrcode.image.svg
import io
import base64


def generate_secret() -> str:
    return pyotp.random_base32()


def verify_token(secret: str, token: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(token, valid_window=1)


def get_provisioning_uri(secret: str, unique_id: str) -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=unique_id, issuer_name="AI OKX Trader")


def generate_qr_base64(secret: str, unique_id: str) -> str:
    uri = get_provisioning_uri(secret, unique_id)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()
