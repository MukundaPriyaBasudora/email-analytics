from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import settings
from app.core.crypto import decrypt_token


def get_gmail_service(encrypted_refresh_token: str):
    refresh_token = decrypt_token(encrypted_refresh_token)

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )

    return build("gmail", "v1", credentials=credentials)