import os
import time
from dotenv import load_dotenv
from google import genai
from repository import log_gemini_call

load_dotenv()


def _load_api_keys() -> list[str]:
    """GEMINI_API_KEY is required (the primary key, same env var as always - nothing changes for
    a setup that only has one). GEMINI_API_KEY_2, GEMINI_API_KEY_3, GEMINI_API_KEY_4, ... are
    optional additional keys for round-robin rotation (see call_gemini's docstring for why) -
    numbered rather than a single comma-separated var so each one is its own GitHub Actions
    secret, matching every other API credential in this project (JSEARCH_API_KEY, ADZUNA_APP_ID,
    etc.) instead of introducing a new "one secret, multiple comma-separated values" convention
    just for this.

    Stops at the first gap (e.g. _2 and _3 set but not _4) rather than scanning past it - a typo'd
    variable name like GEMNI_API_KEY_4 should be obviously missing, not silently skipped over.
    """
    keys = []
    primary = os.environ.get("GEMINI_API_KEY")
    if not primary:
        raise RuntimeError(
            "GEMINI_API_KEY is not set - add it to your .env (or GitHub Actions secrets). "
            "GEMINI_API_KEY_2/_3/_4/... are optional, for rotating across multiple keys."
        )
    keys.append(primary)

    i = 2
    while True:
        extra = os.environ.get(f"GEMINI_API_KEY_{i}")
        if not extra:
            break
        keys.append(extra)
        i += 1

    return keys


GEMINI_API_KEYS = _load_api_keys()
_clients = [genai.Client(api_key=key) for key in GEMINI_API_KEYS]

# Advanced by _next_client_index() on EVERY call (not just on a rate limit) - module-level so
# rotation continues smoothly across separate call_gemini() invocations instead of each call
# restarting from key 1. Not thread-safe (a plain int increment can race across threads), but
# nothing in this codebase calls call_gemini() from more than one thread at a time - the
# Streamlit app, the CLI scripts, and run_scheduled_search.py's per-company loop are all single-
# threaded through this path.
_next_index = 0


def _next_client_index() -> int:
    global _next_index
    idx = _next_index
    _next_index = (_next_index + 1) % len(_clients)
    return idx


def call_gemini(prompt: str, model: str = "gemini-3.5-flash", max_sweeps: int = 4):
    """Calls Gemini, round-robining across every configured API key (GEMINI_API_KEY plus any
    GEMINI_API_KEY_2/_3/_4/... - see _load_api_keys) rather than hammering a single key's own
    per-minute quota.

    Two layers, matching the two ways multiple keys actually help:
      1. PROACTIVE spreading: every call (successful or not) advances to the next key in
         rotation, so N keys naturally see roughly 1/N of the total call volume each, keeping any
         one key's per-minute rate further from its own quota ceiling in the first place - this
         is most of the benefit, and it applies even on a run that never hits a 429 at all.
      2. REACTIVE fallback: if a call DOES come back rate-limited, the very next attempt tries
         the NEXT key immediately - no backoff sleep - since a different key's quota is very
         likely still available even when this one just got capped. Backoff (the same
         30 * sweep-number seconds as before multiple keys existed) only kicks in once an entire
         sweep through EVERY configured key comes back rate-limited, because at that point every
         key really is saturated and waiting is the only option left.

    With only GEMINI_API_KEY set (no extra keys), this is byte-for-byte the old behavior: 1 key,
    up to 4 attempts, waiting 30/60/90s between them.

    A non-rate-limit error (bad request, malformed response, etc.) is NOT retried against another
    key and raises immediately, same as before - switching keys only helps with a quota problem,
    not a genuine API/request error, and silently retrying a broken request N times across N keys
    would just be slower to fail.
    """
    num_keys = len(_clients)

    for sweep in range(max_sweeps):
        for _ in range(num_keys):
            idx = _next_client_index()
            client = _clients[idx]
            try:
                result = client.interactions.create(model=model, input=prompt)
                log_gemini_call(model, status="success")
                return result
            except Exception as e:
                error_text = str(e).lower()
                is_rate_limit = "429" in error_text or "quota" in error_text or "rate limit" in error_text
                log_gemini_call(model, status="rate_limited" if is_rate_limit else "error")
                if not is_rate_limit:
                    raise
                if num_keys > 1:
                    print(f"Rate limited on Gemini key {idx + 1}/{num_keys} - trying the next key...")

        if sweep < max_sweeps - 1:
            wait_seconds = 30 * (sweep + 1)
            # Keep this exact "waiting {N}s before retry" phrasing - streamlit_app.py's
            # _RATE_LIMIT_WAIT_PATTERN regex parses it back out of the captured search log to
            # show a real rate-limit summary in the UI.
            print(f"All {num_keys} Gemini key(s) rate limited - waiting {wait_seconds}s "
                  f"before retry {sweep + 2}/{max_sweeps}...")
            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Gemini rate limited on all {num_keys} configured key(s) after {max_sweeps} full sweep(s)."
    )
