"""One-off diagnostic: list the Gemini models actually available to this API key."""
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

for model in client.models.list():
    name = getattr(model, "name", model)
    print(name)
