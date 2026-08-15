import os
import time
from dotenv import load_dotenv
from google import genai
from repository import log_gemini_call

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def call_gemini(prompt: str, model: str = "gemini-3.5-flash", max_retries: int = 4):
    """Calls Gemini, automatically retrying with backoff if we hit a rate limit."""
    for attempt in range(max_retries):
        try:
            result = client.interactions.create(model=model, input=prompt)
            log_gemini_call(model, status="success")
            return result
        except Exception as e:
            error_text = str(e).lower()
            is_rate_limit = "429" in error_text or "quota" in error_text or "rate limit" in error_text
            log_gemini_call(model, status="rate_limited" if is_rate_limit else "error")
            if is_rate_limit and attempt < max_retries - 1:
                wait_seconds = 30 * (attempt + 1)
                print(f"Rate limited by Gemini - waiting {wait_seconds}s before retry {attempt + 2}/{max_retries}...")
                time.sleep(wait_seconds)
                continue
            raise