import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ.get("GENAI_API_KEY"))

response = client.interactions.create(
    model="gemini-3.5-flash",
    input="Write a short poem about the beauty of nature."
)
print(response.output_text)