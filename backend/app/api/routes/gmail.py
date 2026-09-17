import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
from app.core.scoring import compute_score
from fastapi import APIRouter, Depends, HTTPException
from datetime import date
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
import requests
from datetime import datetime
from email.utils import parsedate_to_datetime
from app.core.urgency import detect_urgency
from app.core.gmail_client import get_gmail_service
from app.core.parsing import extract_email_address
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


@router.post("/detect-urgency")
def run_urgency_detection(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, subject, snippet, sent_at FROM emails WHERE user_id = %s AND direction = 'incoming'",
                (current_user["id"],),
            )
            emails = cursor.fetchall()

        updated_count = 0
        with conn.cursor() as cursor:
            for e in emails:
                is_urgent, matched_keywords, deadline_at = detect_urgency(
                    e["subject"], e["snippet"], e["sent_at"]
                )

                cursor.execute(
                    """
                    UPDATE emails
                    SET is_urgent = %s, matched_keywords = %s, deadline_at = %s
                    WHERE id = %s
                    """,
                    (is_urgent, matched_keywords, deadline_at, e["id"]),
                )
                updated_count += 1

        conn.commit()
        return {"message": f"Urgency detection applied to {updated_count} emails."}
    finally:
        conn.close()


@router.post("/compute-scores")
def compute_scores(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    rp.id AS reply_pair_id,
                    inc.sent_at AS incoming_sent_at,
                    inc.is_urgent,
                    inc.deadline_at,
                    outg.sent_at AS outgoing_sent_at
                FROM reply_pairs rp
                JOIN emails inc ON rp.incoming_email_id = inc.id
                JOIN emails outg ON rp.outgoing_email_id = outg.id
                WHERE rp.user_id = %s
                """,
                (current_user["id"],),
            )
            pairs = cursor.fetchall()

        scored_count = 0
        with conn.cursor() as cursor:
            for p in pairs:
                score, basis = compute_score(
                    sent_at=p["incoming_sent_at"],
                    replied_at=p["outgoing_sent_at"],
                    is_urgent=bool(p["is_urgent"]),
                    deadline_at=p["deadline_at"],
                )

                cursor.execute(
                    """
                    INSERT INTO scores (user_id, reply_pair_id, score_percentage, basis)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        score_percentage = VALUES(score_percentage),
                        basis = VALUES(basis)
                    """,
                    (current_user["id"], p["reply_pair_id"], score, basis),
                )
                scored_count += 1

        conn.commit()
        return {"message": f"Computed scores for {scored_count} reply pairs."}
    finally:
        conn.close()

@router.get("/contacts")
def get_contacts(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT sender_email
                FROM emails
                WHERE user_id = %s AND direction = 'incoming' AND sender_email IS NOT NULL
                """,
                (current_user["id"],),
            )
            rows = cursor.fetchall()

        emails = set()
        for r in rows:
            addr = extract_email_address(r["sender_email"])
            if addr:
                emails.add(addr)

        return {"contacts": sorted(emails)}
    finally:
        conn.close()



@router.get("/contacts/{contact_email}/analytics")
def get_contact_analytics(
    contact_email: str,
    current_user: dict = Depends(get_current_user),
    start_date: date | None = None,
    end_date: date | None = None,
):
    conn = get_db_connection()
    try:
        date_filter_sql = ""
        date_params = []
        if start_date:
            date_filter_sql += " AND sent_at >= %s"
            date_params.append(start_date)
        if end_date:
            date_filter_sql += " AND sent_at <= %s"
            date_params.append(end_date)

        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, direction, subject, snippet, sent_at, is_urgent, deadline_at
                FROM emails
                WHERE user_id = %s
                  AND (sender_email LIKE %s OR recipient_email LIKE %s)
                  {date_filter_sql}
                ORDER BY sent_at ASC
                """,
                (current_user["id"], f"%{contact_email}%", f"%{contact_email}%", *date_params),
            )
            emails = cursor.fetchall()

            # Reply pairs use the same date filter, but applied to inc.sent_at
            rp_date_filter_sql = date_filter_sql.replace("sent_at", "inc.sent_at")

            cursor.execute(
                f"""
                SELECT
                    rp.id AS reply_pair_id,
                    inc.subject,
                    inc.sent_at AS incoming_sent_at,
                    outg.sent_at AS outgoing_sent_at,
                    rp.reply_delay_minutes,
                    s.score_percentage,
                    s.basis
                FROM reply_pairs rp
                JOIN emails inc ON rp.incoming_email_id = inc.id
                JOIN emails outg ON rp.outgoing_email_id = outg.id
                LEFT JOIN scores s ON s.reply_pair_id = rp.id
                WHERE rp.user_id = %s AND inc.sender_email LIKE %s
                  {rp_date_filter_sql}
                ORDER BY inc.sent_at ASC
                """,
                (current_user["id"], f"%{contact_email}%", *date_params),
            )
            reply_pairs = cursor.fetchall()

        avg_score = None
        scored = [p["score_percentage"] for p in reply_pairs if p["score_percentage"] is not None]
        if scored:
            avg_score = round(sum(float(s) for s in scored) / len(scored), 2)

        avg_delay = None
        delays = [p["reply_delay_minutes"] for p in reply_pairs if p["reply_delay_minutes"] is not None]
        if delays:
            avg_delay = round(sum(delays) / len(delays), 1)

        return {
            "contact_email": contact_email,
            "date_range": {"start": start_date, "end": end_date},
            "total_emails": len(emails),
            "total_replied": len(reply_pairs),
            "average_score_percentage": avg_score,
            "average_reply_delay_minutes": avg_delay,
            "emails": emails,
            "reply_pairs": reply_pairs,
        }
    finally:
        conn.close()