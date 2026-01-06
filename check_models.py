import google.generativeai as genai
import os

# 1. Setup your key directly or via environment variable
os.environ["GOOGLE_API_KEY"] = "AIzaSyCgBpRJn1POgwng3rAGt8ZpaPvs567wX50"
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

print("🔍 Checking available models...")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f" - {m.name}")
except Exception as e:
    print(f"❌ Error listing models: {e}")