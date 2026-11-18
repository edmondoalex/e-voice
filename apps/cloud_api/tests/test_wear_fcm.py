from cryptography.fernet import Fernet

from apps.cloud_api.app.config import Settings
from apps.cloud_api.app.wear_fcm import WearFcmSender


def test_fcm_token_is_decrypted_only_at_delivery_boundary(tmp_path):
    key = Fernet.generate_key().decode()
    settings = Settings(pairing_delivery_key=key, wear_fcm_credentials_path=str(tmp_path / "none"))
    plaintext = "firebase-device-token-that-is-long-enough"
    encrypted = Fernet(key.encode()).encrypt(plaintext.encode()).decode()

    assert plaintext not in encrypted
    assert WearFcmSender(settings).decrypt(encrypted) == plaintext
