"""Cross-connector date normalization.
 
Every connector's raw "when was this posted" data looks different - Workday gives relative
strings like "Posted Today" or "Posted 3 Days Ago" that only make sense at the moment they're
fetched (they don't update themselves once stored), while JSearch/Adzuna/Greenhouse/Lever/Ashby
give some flavor of an absolute timestamp. This module turns all of that into one consistent
format: a plain "YYYY-MM-DD" date string (UTC), or None if it genuinely can't be parsed.
 
Callers must treat None as "unknown," not "fresh" - job_aggregator.py's freshness filter
excludes anything with posted_date=None rather than assuming it's recent.
"""
 
import re
from datetime import datetime, timedelta, timezone
 
 
def normalize_posted_date(raw, source: str) -> str | None:
    if raw is None or raw == "":
        return None
 
    if source == "workday":
        return _parse_workday_relative_date(str(raw))
 
    return _parse_absolute_date(str(raw))
 
 
def _parse_workday_relative_date(raw: str) -> str | None:
    today = datetime.now(timezone.utc).date()
    text = raw.strip().lower()
 
    if "today" in text:
        return today.isoformat()
 
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
 
    # Matches "Posted 3 Days Ago", "Posted 30+ Days Ago", "3 days ago", etc.
    match = re.search(r"(\d+)\s*\+?\s*days?\s*ago", text)
    if match:
        days_ago = int(match.group(1))
        return (today - timedelta(days=days_ago)).isoformat()
 
    return None
 
 
def _parse_absolute_date(raw: str) -> str | None:
    cleaned = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed.date().isoformat()
    except ValueError:
        pass
 
    # Some sources may give date-only or differently-delimited strings that fromisoformat
    # won't accept. Try a couple of other common shapes before giving up.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(raw.strip(), fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue
 
    return None
 
 
if __name__ == "__main__":
    # Zero-Gemini-cost sanity check - run directly to eyeball the parsing logic.
    tests = [
        ("Posted Today", "workday"),
        ("Posted Yesterday", "workday"),
        ("Posted 3 Days Ago", "workday"),
        ("Posted 30+ Days Ago", "workday"),
        ("gibberish", "workday"),
        ("2026-08-10T14:23:00Z", "jsearch"),
        ("2026-08-10", "adzuna"),
        (None, "greenhouse"),
        ("", "lever"),
    ]
    for raw, source in tests:
        print(f"{source:12} {raw!r:30} -> {normalize_posted_date(raw, source)}")