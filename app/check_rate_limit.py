from gemini_utils import call_gemini

print("Probing gemini-3.5-flash with a minimal request...")
try:
    response = call_gemini("Reply with just the word: ok", model="gemini-3.5-flash", max_retries=1)
    print(f"Success! Response: {response.output_text.strip()}")
except Exception as e:
    print(f"Still blocked: {e}")