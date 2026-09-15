from pydantic import BaseModel


class GmailConnectionStatus(BaseModel):
    is_connected: bool
    google_email: str | None = None