from datetime import datetime, timedelta

URGENT_BASELINE_HOURS = 24
NORMAL_BASELINE_HOURS = 48


def compute_score(sent_at: datetime, replied_at: datetime, is_urgent: bool, deadline_at: datetime | None):
    actual_delay = replied_at - sent_at

    if deadline_at:
        basis = "deadline"
        allowed = deadline_at - sent_at
    elif is_urgent:
        basis = "urgent_no_deadline"
        allowed = timedelta(hours=URGENT_BASELINE_HOURS)
    else:
        basis = "normal"
        allowed = timedelta(hours=NORMAL_BASELINE_HOURS)

    if allowed.total_seconds() <= 0:
        allowed = timedelta(hours=1)  # avoid divide-by-zero edge case

    ratio = actual_delay.total_seconds() / allowed.total_seconds()

    if ratio <= 1:
        # Replied within the allowed window: linearly scale 100% (instant) down to 60% (right at the limit)
        score = 100 - (ratio * 40)
    else:
        # Replied late: drop from 60% down toward 0% the further past deadline
        overage = ratio - 1
        score = max(0, 60 - (overage * 60))

    return round(score, 2), basis