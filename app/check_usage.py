from database import init_db
from repository import get_gemini_call_log, get_gemini_call_counts_today

init_db()

print("--- Today's usage (best-effort count) ---")
counts = get_gemini_call_counts_today()
if not counts:
    print("(no calls logged today)")
for model, status_counts in counts.items():
    breakdown = ", ".join(f"{status}: {c}" for status, c in status_counts.items())
    print(f"  {model} — {breakdown}")

print("\n--- Recent call history (most recent first) ---")
log = get_gemini_call_log(limit=50)
if not log:
    print("(no calls logged yet)")
for entry in log:
    print(f"  {entry['called_at']}  {entry['model']:25}  {entry['status']}")