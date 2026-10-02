"""Timestamp helpers. Everything is stored and compared as timezone-aware UTC."""
import re
from datetime import datetime, timezone


def parse_ts(text: str) -> datetime:
    """Parse an ISO-8601 string (Z or offset, any number of fraction digits) into aware UTC."""
    text = text.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # Python 3.10 accepts at most 6 fraction digits
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_ts(moment: datetime) -> str:
    """Render a datetime as ISO-8601 UTC with a trailing Z, e.g. 2026-10-01T18:17:06.148031Z."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
