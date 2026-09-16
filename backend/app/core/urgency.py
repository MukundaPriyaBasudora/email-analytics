import re
from datetime import datetime, timedelta

# Phrases that signal urgency, checked case-insensitively
URGENCY_KEYWORDS = [
    "urgent",
    "asap",
    "as soon as possible",
    "at the earliest",
    "immediately",
    "time-sensitive",
    "time sensitive",
    "high priority",
    "please respond",
    "deadline",
]

# Patterns like "within 2 days", "within 48 hours" -> captures the number + unit
DEADLINE_PATTERN = re.compile(r"within\s+(\d+)\s*(day|days|hour|hours)", re.IGNORECASE)


def detect_urgency(subject: str, snippet: str, sent_at: datetime):
    text = f"{subject or ''} {snippet or ''}".lower()

    matched = [kw for kw in URGENCY_KEYWORDS if kw in text]

    deadline_at = None
    match = DEADLINE_PATTERN.search(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if "day" in unit:
            deadline_at = sent_at + timedelta(days=amount)
        else:
            deadline_at = sent_at + timedelta(hours=amount)
        matched.append(match.group(0))

    is_urgent = bool(matched)
    matched_keywords = ", ".join(matched) if matched else None

    return is_urgent, matched_keywords, deadline_at