import os
import io
import asyncio
import requests
from google import genai 
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backboard import BackboardClient
from PIL import Image
from dotenv import load_dotenv
from pydantic import ValidationError

app = FastAPI()

load_dotenv()

# --------------------
# 1. Setup Keys & Clients
# --------------------
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
EXISTING_ASSISTANT_ID = os.getenv("BACKBOARD_ASSISTANT_ID") 

if not BACKBOARD_API_KEY or not GOOGLE_API_KEY:
    raise RuntimeError("⚠️ Set BACKBOARD_API_KEY and GOOGLE_API_KEY in .env")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

# Setup Backboard
bb_client = BackboardClient(api_key=BACKBOARD_API_KEY)
assistant_id = EXISTING_ASSISTANT_ID 
latest_thread_id = None 

# Setup Gemini
gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

@app.on_event("startup")
async def startup():
    global assistant_id
    if not assistant_id:
        try:
            assistant = await bb_client.create_assistant(
                name="FoodCrawler",
                description="Calorie tracking assistant with persistent memory."
            )
            assistant_id = assistant.assistant_id
            print(f"✅ Assistant Created: {assistant_id}")
        except Exception as e:
            print(f"⚠️ Startup Warning: {e}")

async def get_or_create_thread():
    forced_id = os.getenv("FORCE_THREAD_ID")
    if forced_id:
        return forced_id

    global latest_thread_id
    if latest_thread_id:
        return latest_thread_id
    
    if assistant_id:
        try:
            thread = await bb_client.create_thread(assistant_id)
            latest_thread_id = thread.thread_id
            return thread.thread_id
        except:
            pass
    return None

# --------------------
# 2. Log Meal
# --------------------
@app.post("/log-meal")
async def log_meal(image: UploadFile, meal: str = Form(...)):
    thread_id = await get_or_create_thread()
    
    # --- STEP A: GEMINI VISION ---
    try:
        print(f"👀 Sending image of '{meal}' to {MODEL_ID}...")
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))
        
        gemini_prompt = (
            f"The user labeled this food as '{meal}'. "
            "Analyze the image. Identify the food accurately. "
            "Estimate the calories and macros (Protein, Carbs, Fat). "
            "Be concise."
        )
        
        response = gemini_client.models.generate_content(
            model=MODEL_ID,
            contents=[gemini_prompt, pil_image]
        )
        # This creates the variable that was missing!
        analysis_text = response.text 
        print("✅ Gemini Analysis Complete.")

    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return {"error": str(e)}

    # --- STEP B: BACKBOARD STORAGE (FIXED) ---
    if thread_id:
        try:
            print("💾 Saving data to Backboard...")
            storage_message = f"Logged meal '{meal}'. Analysis: {analysis_text}."
            
            await bb_client.add_message(
                thread_id=thread_id,
                content=storage_message,
                memory="Auto"
            )
            print("✅ Saved to Backboard history.")

        except ValidationError:
            # Catch the library bug: The message SAVED, but return parsing failed.
            print("✅ Saved to Backboard (Ignored library parsing error).")
            
        except Exception as e:
            # This catches the 'analysis_text' not defined error if Step A fails
            print(f"⚠️ Backboard Warning: {e}")

    return {"result": analysis_text}

# --------------------
# 3. Ask
# --------------------
@app.post("/ask")
async def ask(question: str = Form(...)):
    thread_id = await get_or_create_thread()
    
    try:
        await bb_client.add_message(
            thread_id=thread_id,
            content=question,
            llm_provider="openai",
            model_name="gpt-4o",
            memory="Auto"
        )
    except ValidationError:
        # Ignore library response parsing error; the message was sent.
        pass
    except Exception as e:
        return {"error": str(e)}

    # Wait for the AI's reply
    for _ in range(5):
        await asyncio.sleep(2)
        data = await bb_client.get_thread(thread_id)
        if hasattr(data, 'messages'):
            for msg in reversed(data.messages):
                if msg.role == "assistant":
                    return {"answer": msg.content}
    return {"error": "Timeout"}

@app.post("/add-fact")
async def add_fact(fact: str = Form(...)):
    """Manually add a fact to the Assistant's long-term memory."""
    import requests
    url = f"https://app.backboard.io/api/assistants/{EXISTING_ASSISTANT_ID}/memories"
    headers = {
        "X-API-Key": BACKBOARD_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"content": fact}
    
    resp = requests.post(url, json=payload, headers=headers)
    if resp.status_code == 201:
        return {"status": "Memory added to the Hive Mind."}
    return {"error": resp.text}

# --------------------
# 4. Memory Management (The Daemon Console)
# --------------------
@app.delete("/delete-memory/{memory_id}")
async def delete_memory(memory_id: str):
    """Purge a specific memory from the Hive Mind."""
    url = f"https://app.backboard.io/api/assistants/{EXISTING_ASSISTANT_ID}/memories/{memory_id}"
    headers = {"X-API-Key": BACKBOARD_API_KEY}
    resp = requests.delete(url, headers=headers)
    if resp.status_code == 200:
        return {"status": "success"}
    return JSONResponse(status_code=400, content={"error": "Failed to purge memory"})

@app.post("/add-memory")
async def add_memory_manually(content: str = Form(...)):
    """Manually inject a fact into the Assistant's long-term memory."""
    url = f"https://app.backboard.io/api/assistants/{EXISTING_ASSISTANT_ID}/memories"
    headers = {
        "X-API-Key": BACKBOARD_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": content,
        "metadata": {"source": "manual_injection"}
    }
    
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 201:
        return {"status": "Memory indexed successfully", "data": response.json()}
    return {"error": response.text}

def get_thread_details(t_id):
    try:
        url = f"https://app.backboard.io/api/threads/{t_id}"
        resp = requests.get(url, headers={"X-API-Key": BACKBOARD_API_KEY})
        if resp.status_code == 200:
            return resp.json().get("messages", [])
    except: return []
    return []

# --- HELPER to get a Label for the Thread ---
# --- HELPER: FIXED STRING PARSING ---
def get_thread_label(thread_data):
    """Safely extracts a title from the thread messages."""
    messages = thread_data.get("messages", [])
    if not messages:
        return "Empty Conversation"
    
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            # Using simple 'in' check and split without complex escapes
            if "labeled '" in content:
                parts = content.split("labeled '")
                if len(parts) > 1:
                    meal_name = parts[1].split("'")[0]
                    return f"Meal: {meal_name}"
            return content[:25] + "..."
            
    return f"Thread {thread_data.get('thread_id', '???')[:5]}"

@app.get("/dashboard")
async def dashboard(thread_id: str = None, search: str = None):
    """Gemini-style Dashboard with corrected syntax and Sidebar Search."""
    threads_list = []
    memories_data = []
    active_messages = []

    # 1. Fetch Threads
    try:
        t_url = "https://app.backboard.io/api/threads?limit=20"
        t_resp = requests.get(t_url, headers={"X-API-Key": BACKBOARD_API_KEY})
        if t_resp.status_code == 200:
            threads_list = t_resp.json()
    except: pass

    # 2. Fetch Memories
    try:
        m_url = f"https://app.backboard.io/api/assistants/{EXISTING_ASSISTANT_ID}/memories"
        m_resp = requests.get(m_url, headers={"X-API-Key": BACKBOARD_API_KEY})
        if m_resp.status_code == 200:
            memories_data = m_resp.json().get("memories", [])
    except: pass

    # 3. Get Active Messages
    if thread_id:
        active_messages = get_thread_details(thread_id)

    # --- Construct Sidebar with Search Filter ---
    sidebar_html = ""
    for t in threads_list:
        label = get_thread_label(t)
        # Simple Search Filtering
        if search and search.lower() not in label.lower():
            continue
            
        is_active = "active" if thread_id == t["thread_id"] else ""
        sidebar_html += f'<a href="/dashboard?thread_id={t["thread_id"]}" class="nav-item {is_active}">{label}</a>'

    memories_html = "".join([
        f'<div class="memory-card" id="mem-{m.get("id")}"><span>{m.get("content") or m.get("memory")}</span>'
        f'<button onclick="purgeMemory(\'{m.get("id")}\')">×</button></div>' for m in memories_data
    ])

    chat_html = "".join([
        f'<div class="msg {m["role"]}"><small>{m["role"].upper()}:</small><br>{m["content"]}</div>' 
        for m in active_messages if m.get("content")
    ]) if active_messages else "<p style='color:#555; text-align:center;'>Select a chat to view the analysis logs.</p>"

    return HTMLResponse(content=f"""
    <html>
        <head>
            <title>FoodCrawler Console 🧠</title>
            <style>
                body {{ background: #0a0a0a; color: #ccc; font-family: 'Inter', sans-serif; display: flex; margin: 0; height: 100vh; }}
                .sidebar {{ width: 280px; background: #000; border-right: 1px solid #222; display: flex; flex-direction: column; padding: 20px; }}
                .search-bar {{ background: #111; border: 1px solid #333; color: #fff; padding: 8px; border-radius: 5px; margin-bottom: 15px; width: 100%; }}
                .nav-item {{ padding: 12px; border-radius: 8px; color: #888; text-decoration: none; margin-bottom: 8px; font-size: 14px; transition: 0.2s; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }}
                .nav-item:hover {{ background: #1a1a1a; color: #fff; }}
                .nav-item.active {{ background: #ff3e3e22; color: #ff3e3e; border: 1px solid #ff3e3e; }}
                .main {{ flex: 1; display: flex; flex-direction: column; overflow-y: auto; background: #0f0f0f; }}
                .container {{ padding: 30px; max-width: 850px; margin: 0 auto; width: 100%; }}
                .msg {{ padding: 20px; border-radius: 12px; margin-bottom: 20px; line-height: 1.6; max-width: 85%; font-size: 15px; }}
                .user {{ background: #252525; align-self: flex-end; margin-left: auto; color: #fff; }}
                .assistant {{ background: #151515; border: 1px solid #252525; }}
                .hive-section {{ margin-top: 60px; padding-top: 30px; border-top: 1px solid #222; }}
                .memory-card {{ background: #111; border: 1px solid #222; padding: 12px; margin-bottom: 10px; display: flex; justify-content: space-between; border-radius: 6px; }}
                .memory-card button {{ background: none; border: none; color: #ff3e3e; cursor: pointer; font-size: 20px; }}
                .inject-input {{ background: #000; border: 1px solid #333; color: #0f0; padding: 12px; width: 75%; border-radius: 6px; }}
            </style>
            <script>
                async function purgeMemory(id) {{ await fetch('/delete-memory/'+id, {{method:'DELETE'}}); location.reload(); }}
                async function injectFact() {{
                    const f = document.getElementById('factInput').value;
                    const fd = new FormData(); fd.append('fact', f);
                    await fetch('/add-fact', {{method:'POST', body:fd}}); location.reload();
                }}
                function filterSearch(val) {{
                    window.location.href = "/dashboard?search=" + val;
                }}
            </script>
        </head>
        <body>
            <div class="sidebar">
                <h3 style="color: #ff3e3e; font-size: 12px; letter-spacing: 2px; margin-bottom: 10px;">HISTORY</h3>
                <input type="text" class="search-bar" placeholder="Search meals..." onchange="filterSearch(this.value)" value="{search or ''}">
                <div style="overflow-y: auto;">{sidebar_html}</div>
            </div>
            <div class="main">
                <div class="container">
                    <div id="chat-view" style="display: flex; flex-direction: column;">{chat_html}</div>
                    <div class="hive-section">
                        <h3 style="color: #ff3e3e;">🧠 HIVE_MIND</h3>
                        <div style="margin-bottom: 25px;">
                            <input type="text" id="factInput" class="inject-input" placeholder="Inject core fact...">
                            <button onclick="injectFact()" style="background:#ff3e3e; color:#fff; border:none; padding:12px; border-radius:6px; cursor:pointer;">Inject</button>
                        </div>
                        {memories_html}
                    </div>
                </div>
            </div>
        </body>
    </html>
    """)