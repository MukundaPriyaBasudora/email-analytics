import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
import requests

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.crypto import encrypt_token
from app.db.session import get_db_connection

router = APIRouter(prefix="/api/gmail", tags=["gmail"])

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def build_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_redirect_uri],
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
        autogenerate_code_verifier=False,
    )


@router.get("/oauth/start")
def oauth_start(current_user: dict = Depends(get_current_user)):
    flow = build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=str(current_user["id"]),
    )
    return RedirectResponse(auth_url)


@router.get("/oauth/callback")
def oauth_callback(code: str, state: str):
    user_id = int(state)

    flow = build_flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials

    if not credentials.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token received. Revoke access in your Google account and try again.",
        )

    userinfo_resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {credentials.token}"},
    )
    google_email = userinfo_resp.json().get("email")

    encrypted = encrypt_token(credentials.refresh_token)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO gmail_connections (user_id, google_email, encrypted_refresh_token, is_connected, connected_at)
                VALUES (%s, %s, %s, TRUE, NOW())
                ON DUPLICATE KEY UPDATE
                    google_email = VALUES(google_email),
                    encrypted_refresh_token = VALUES(encrypted_refresh_token),
                    is_connected = TRUE,
                    connected_at = NOW()
                """,
                (user_id, google_email, encrypted),
            )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Gmail connected successfully. You can close this tab."}


@router.post("/disconnect")
def disconnect(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE gmail_connections SET is_connected = FALSE, encrypted_refresh_token = NULL WHERE user_id = %s",
                (current_user["id"],),
            )
        conn.commit()
    finally:
        conn.close()
    return {"message": "Gmail disconnected."}