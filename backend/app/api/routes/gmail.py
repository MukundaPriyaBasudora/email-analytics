import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
import requests
from datetime import datetime
from email.utils import parsedate_to_datetime

from app.core.gmail_client import get_gmail_service

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

@router.post("/sync")
def sync_emails(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT google_email, encrypted_refresh_token FROM gmail_connections WHERE user_id = %s AND is_connected = TRUE",
                (current_user["id"],),
            )
            connection = cursor.fetchone()

        if not connection:
            raise HTTPException(status_code=400, detail="Gmail is not connected.")

        google_email = connection["google_email"]
        service = get_gmail_service(connection["encrypted_refresh_token"])

        # Get recent message IDs (last 50)
        results = service.users().messages().list(userId="me", maxResults=50).execute()
        message_ids = [m["id"] for m in results.get("messages", [])]

        saved_count = 0
        with conn.cursor() as cursor:
            for msg_id in message_ids:
                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute()

                headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
                sender = headers.get("From", "")
                recipient = headers.get("To", "")
                subject = headers.get("Subject", "")
                date_str = headers.get("Date", "")

                try:
                    sent_at = parsedate_to_datetime(date_str)
                except Exception:
                    continue  # skip messages with unparseable dates

                direction = "outgoing" if google_email.lower() in sender.lower() else "incoming"

                cursor.execute(
                    """
                    INSERT INTO emails
                        (user_id, gmail_message_id, thread_id, direction, sender_email, recipient_email, subject, snippet, sent_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        snippet = VALUES(snippet)
                    """,
                    (
                        current_user["id"],
                        msg_id,
                        msg["threadId"],
                        direction,
                        sender,
                        recipient,
                        subject,
                        msg.get("snippet", ""),
                        sent_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                saved_count += 1

        conn.commit()
        return {"message": f"Synced {saved_count} emails."}
    finally:
        conn.close()


@router.post("/compute-reply-pairs")
def compute_reply_pairs(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, thread_id, direction, sent_at FROM emails WHERE user_id = %s ORDER BY thread_id, sent_at ASC",
                (current_user["id"],),
            )
            all_emails = cursor.fetchall()

        # Group emails by thread_id
        threads: dict[str, list[dict]] = {}
        for e in all_emails:
            threads.setdefault(e["thread_id"], []).append(e)

        pairs_created = 0
        with conn.cursor() as cursor:
            for thread_id, messages in threads.items():
                for i in range(len(messages) - 1):
                    current_msg = messages[i]
                    next_msg = messages[i + 1]

                    if current_msg["direction"] == "incoming" and next_msg["direction"] == "outgoing":
                        delay = next_msg["sent_at"] - current_msg["sent_at"]
                        delay_minutes = int(delay.total_seconds() // 60)

                        cursor.execute(
                            """
                            INSERT INTO reply_pairs
                                (user_id, thread_id, incoming_email_id, outgoing_email_id, reply_delay_minutes)
                            VALUES (%s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                reply_delay_minutes = VALUES(reply_delay_minutes)
                            """,
                            (current_user["id"], thread_id, current_msg["id"], next_msg["id"], delay_minutes),
                        )
                        pairs_created += 1

        conn.commit()
        return {"message": f"Computed {pairs_created} reply pairs."}
    finally:
        conn.close()