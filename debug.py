import os
import inspect
from backboard import BackboardClient

API_KEY = os.getenv("BACKBOARD_API_KEY")
client = BackboardClient(api_key=API_KEY)

print("\n" + "="*50)
print("🕵️‍♂️ DETECTIVE MODE: Inspecting add_message")
print("="*50)

try:
    # We inspect add_message this time
    sig = inspect.signature(client.add_message)
    print(f"Function Signature:\n{sig}")
    
    print("\nValid Parameters:")
    for name, param in sig.parameters.items():
        print(f" - {name} (Default: {param.default})")
        
except Exception as e:
    print(f"Could not inspect: {e}")

print("="*50 + "\n")