import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Use the key from your .env
api_key = os.getenv("GOOGLE_API_KEY")

client = genai.Client(api_key=api_key)

print("📡 --- GOOGLE MODEL INVENTORY ---")
try:
    # This matches the library you are using in main.py
    for m in client.models.list():
        print(f"  > {m.name}")
except Exception as e:
    print(f"  ❌ Could not list models: {e}")