import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

# 1. Setup
API_KEY = os.getenv("BACKBOARD_API_KEY")
ASSISTANT_ID = os.getenv("BACKBOARD_ASSISTANT_ID")
BASE_URL = "https://app.backboard.io/api"

if not API_KEY:
    print("❌ Error: BACKBOARD_API_KEY not found in .env")
    exit()

HEADERS = {"X-API-Key": API_KEY}

def get_all_threads():
    print(f"\n📡 Connecting to {BASE_URL}...")
    try:
        response = requests.get(f"{BASE_URL}/threads?limit=20", headers=HEADERS)
        if response.status_code == 200:
            threads = response.json()
            print(f"✅ Found {len(threads)} threads.\n")
            return threads
    except Exception as e:
        print(f"❌ Connection error: {e}")
    return []

def get_thread_messages(thread_id):
    try:
        response = requests.get(f"{BASE_URL}/threads/{thread_id}", headers=HEADERS)
        if response.status_code == 200:
            return response.json().get("messages", [])
    except:
        pass
    return []

def get_memories():
    if not ASSISTANT_ID:
        return

    print(f"🧠 Checking Memory for Assistant: {ASSISTANT_ID}...")
    response = requests.get(f"{BASE_URL}/assistants/{ASSISTANT_ID}/memories", headers=HEADERS)
    
    if response.status_code == 200:
        data = response.json()
        
        # FIX: The API returns a dict like {"memories": [], "total_count": 0}
        # We need to extract the list properly.
        memories_list = []
        if isinstance(data, dict) and "memories" in data:
            memories_list = data["memories"]
        elif isinstance(data, list):
            memories_list = data
            
        print(f"✅ Found {len(memories_list)} permanent memories:\n")
        
        for mem in memories_list:
            # Handle different memory formats
            if isinstance(mem, dict):
                # Try to find the text content
                text = mem.get('memory') or mem.get('content') or str(mem)
                print(f"   - {text}")
            else:
                print(f"   - {mem}")
    else:
        print(f"⚠️ Memory fetch status: {response.status_code}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("=== SNAPCAL DATABASE VIEWER ===\n")
    
    # 1. Show Memories
    get_memories()
    print("-" * 40)

    # 2. Show Conversations
    threads = get_all_threads()
    
    if threads:
        print("--- 📜 RECENT CONVERSATIONS ---")
        for i, thread in enumerate(threads[:5]):
            t_id = thread.get("thread_id")
            created = thread.get("created_at", "Unknown Date")
            
            print(f"\n[{i+1}] Thread: {t_id} ({created})")
            
            msgs = get_thread_messages(t_id)
            if not msgs:
                print("    (Empty conversation)")
            
            for m in msgs:
                role = m.get("role", "unknown").upper()
                
                # FIX: Handle cases where content is None (like Tool Calls or Images)
                content = m.get("content")
                if content is None:
                    content = "[No Text Content - Likely Image or Tool Call]"
                
                # Safe Preview
                preview = (content[:100] + '..') if len(content) > 100 else content
                print(f"    {role}: {preview}")