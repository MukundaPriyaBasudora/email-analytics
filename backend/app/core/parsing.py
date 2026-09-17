import re

EMAIL_PATTERN = re.compile(r"[\w\.\-+]+@[\w\.\-]+")

def extract_email_address(raw_header: str) -> str | None:
    if not raw_header:
        return None
    match = EMAIL_PATTERN.search(raw_header)
    return match.group(0) if match else None