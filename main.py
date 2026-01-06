import os
import io
import json
import asyncio
import requests
from datetime import datetime, timedelta
from google import genai 
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backboard import BackboardClient
from PIL import Image
from dotenv import load_dotenv
from pydantic import ValidationError

# --------------------
# 1. Initialization
# --------------------
load_dotenv()
app = FastAPI()

BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
EXISTING_ASSISTANT_ID = os.getenv("BACKBOARD_ASSISTANT_ID") 

if not BACKBOARD_API_KEY or not GOOGLE_API_KEY:
    raise RuntimeError("⚠️ Set BACKBOARD_API_KEY and GOOGLE_API_KEY in .env")

# Clients
bb_client = BackboardClient(api_key=BACKBOARD_API_KEY)

# FIX: Remove 'http_options' and use the default constructor
gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

# FIX: Use the stable base name (No -latest, no models/ prefix)
MODEL_ID = "gemini-flash-latest" 

# State
assistant_id = EXISTING_ASSISTANT_ID 
latest_thread_id = None 

app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------
# 2. Tool Definitions
# --------------------

def calculate_bmi(weight_kg: float, height_cm: float):
    """Business logic for the BMI Tool."""
    height_m = height_cm / 100
    bmi = weight_kg / (height_m ** 2)
    category = "Normal"
    if bmi < 18.5: category = "Underweight"
    elif bmi >= 25: category = "Overweight"
    elif bmi >= 30: category = "Obese"
    return {"bmi": round(bmi, 2), "category": category, "timestamp": datetime.now().isoformat()}

# --------------------
# 3. Helper Logic
# --------------------

def get_thread_label(thread_data):
    messages = thread_data.get("messages", [])
    if not messages: return "New Session"
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if "Log Session: " in content:
                return content.replace("Log Session: ", "")
            return content[:20] + "..."
    return f"Chat {thread_data.get('thread_id', '')[:5]}"

def get_thread_details(t_id):
    try:
        url = f"https://app.backboard.io/api/threads/{t_id}"
        resp = requests.get(url, headers={"X-API-Key": BACKBOARD_API_KEY})
        return resp.json().get("messages", []) if resp.status_code == 200 else []
    except: return []

async def create_visible_thread(label: str):
    try:
        thread = await bb_client.create_thread(assistant_id)
        t_id = thread.thread_id
        await bb_client.add_message(thread_id=t_id, content=f"Log Session: {label}", send_to_llm="false")
        return t_id
    except: return None

# --------------------
# 4. Startup Config (With Session Recovery)
# --------------------

@app.on_event("startup")
async def startup():
    global assistant_id, latest_thread_id
    
    # NEW: Automatically recover the last session on startup/reload
    try:
        t_url = "https://app.backboard.io/api/threads?limit=1"
        t_resp = requests.get(t_url, headers={"X-API-Key": BACKBOARD_API_KEY})
        if t_resp.status_code == 200 and t_resp.json():
            latest_thread_id = t_resp.json()[0]["thread_id"]
            print(f"🔄 Session Recovered: {latest_thread_id}")
    except Exception as e:
        print(f"⚠️ Session Recovery Failed: {e}")

    # Assistant & Tool Setup
    if not assistant_id:
        bmi_tool = {
            "type": "function",
            "function": {
                "name": "calculate_bmi",
                "description": "Calculate Body Mass Index to provide health context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number", "description": "Weight in kg"},
                        "height_cm": {"type": "number", "description": "Height in cm"}
                    },
                    "required": ["weight_kg", "height_cm"]
                }
            }
        }
        try:
            assistant = await bb_client.create_assistant(
                name="FoodCrawler Pro",
                description="Agentic nutrition assistant with health tools.",
                tools=[bmi_tool] 
            )
            assistant_id = assistant.assistant_id
            print(f"✅ Assistant Ready: {assistant_id}")
        except Exception as e:
            print(f"⚠️ Startup Error: {e}")

# --------------------
# 5. API Endpoints
# --------------------

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.post("/log-meal")
async def log_meal(image: UploadFile, meal: str = Form(...)):
    thread_id = await create_visible_thread(meal)
    global latest_thread_id
    latest_thread_id = thread_id

    try:
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))
        response = gemini_client.models.generate_content(
            model=MODEL_ID, 
            contents=[f"Identify {meal} and estimate macros concisely.", pil_image]
        )
        analysis = response.text

        if thread_id:
            try:
                await bb_client.add_message(thread_id=thread_id, content=f"Logged {meal}: {analysis}", memory="Auto")
            except ValidationError: pass

        return {"result": analysis, "thread_id": thread_id}
    except Exception as e:
        return {"error": str(e)}

@app.post("/ask")
async def ask(question: str = Form(...)):
    global latest_thread_id
    if not latest_thread_id:
        return {"error": "No active thread."}
    
    # Using direct requests to bypass the Pydantic validation error in the SDK
    try:
        url = f"https://app.backboard.io/api/threads/{latest_thread_id}/messages"
        headers = {"X-API-Key": BACKBOARD_API_KEY}
        payload = {
            "content": question,
            "memory": "Auto",
            "llm_provider": "openai",
            "model_name": "gpt-4o"
        }
        
        # Send the message
        requests.post(url, headers=headers, data=payload)
        
        # Poll for the response
        for _ in range(12):
            await asyncio.sleep(1.5)
            messages = get_thread_details(latest_thread_id)
            for msg in reversed(messages):
                # Handle tool calls manually if needed, or get the text
                if msg.get("role") == "assistant" and msg.get("content"):
                    return {"answer": msg["content"]}
                    
    except Exception as e:
        return {"error": str(e)}
        
    return {"error": "AI is taking a while. Check the dashboard for the reply!"}

@app.post("/add-fact")
async def add_fact(fact: str = Form(...)):
    url = f"https://app.backboard.io/api/assistants/{assistant_id}/memories"
    resp = requests.post(url, json={"content": fact}, headers={"X-API-Key": BACKBOARD_API_KEY})
    return {"status": "success"} if resp.status_code == 201 else {"error": resp.text}

@app.delete("/delete-memory/{memory_id}")
async def delete_memory(memory_id: str):
    url = f"https://app.backboard.io/api/assistants/{assistant_id}/memories/{memory_id}"
    resp = requests.delete(url, headers={"X-API-Key": BACKBOARD_API_KEY})
    return {"status": "purged"}

@app.get("/dashboard")
async def dashboard(thread_id: str = None):
    t_resp = requests.get("https://app.backboard.io/api/threads?limit=15", headers={"X-API-Key": BACKBOARD_API_KEY})
    threads = t_resp.json() if t_resp.status_code == 200 else []

    m_resp = requests.get(f"https://app.backboard.io/api/assistants/{assistant_id}/memories", headers={"X-API-Key": BACKBOARD_API_KEY})
    memories = m_resp.json().get("memories", []) if m_resp.status_code == 200 else []

    sidebar_html = "".join([
        f'<a href="/dashboard?thread_id={t["thread_id"]}" class="nav-item {"active" if thread_id == t["thread_id"] else ""}">'
        f'{get_thread_label(t)}</a>' for t in threads
    ])

    active_msgs = get_thread_details(thread_id) if thread_id else []
    chat_html = "".join([f'<div class="msg {m["role"]}">{m["content"]}</div>' for m in active_msgs if m.get("content")])

    return HTMLResponse(content=f"""
    <html>
        <head>
            <title>FoodCrawler Console</title>
            <style>
                body {{ background: #080808; color: #eee; font-family: sans-serif; display: flex; margin: 0; height: 100vh; }}
                .sidebar {{ width: 280px; background: #000; border-right: 1px solid #222; padding: 20px; overflow-y: auto; }}
                .main {{ flex: 1; background: #0f0f0f; overflow-y: auto; padding: 40px; }}
                .nav-item {{ display: block; padding: 12px; color: #888; text-decoration: none; border-radius: 8px; margin-bottom: 5px; font-size: 14px; }}
                .nav-item.active {{ background: #ff3e3e22; color: #ff3e3e; border: 1px solid #ff3e3e; }}
                .msg {{ padding: 15px; margin-bottom: 15px; border-radius: 10px; max-width: 85%; line-height: 1.5; }}
                .user {{ background: #222; margin-left: auto; }}
                .assistant {{ background: #1a1a1a; border: 1px solid #333; }}
                .memory-card {{ background: #111; padding: 10px; margin-bottom: 8px; border-radius: 5px; display: flex; justify-content: space-between; font-size: 13px; }}
                input {{ background: #000; border: 1px solid #333; color: #0f0; padding: 10px; width: 70%; border-radius: 4px; }}
                button {{ background: #ff3e3e; color: #fff; border: none; padding: 10px 20px; cursor: pointer; border-radius: 4px; font-weight: bold; }}
            </style>
            <script>
                async function purgeMemory(id) {{ await fetch('/delete-memory/'+id, {{method:'DELETE'}}); location.reload(); }}
                async function injectFact() {{
                    const val = document.getElementById('factInput').value;
                    const fd = new FormData(); fd.append('fact', val);
                    await fetch('/add-fact', {{method:'POST', body:fd}}); location.reload();
                }}
            </script>
        </head>
        <body>
            <div class="sidebar">
                <h2 style="color:#ff3e3e; font-size: 14px; letter-spacing: 1px;">SESSION HISTORY</h2>
                {sidebar_html}
            </div>
            <div class="main">
                <div id="chat-content" style="display:flex; flex-direction:column;">{chat_html or "Select a session."}</div>
                <div style="margin-top:50px; border-top: 1px solid #333; padding-top:20px;">
                    <h3 style="color:#ff3e3e;">🧠 HIVE MIND (RAG)</h3>
                    <div style="margin-bottom: 20px;">
                        <input type="text" id="factInput" placeholder="Manually inject a core health fact...">
                        <button onclick="injectFact()">Inject</button>
                    </div>
                    <div style="margin-top:20px;">{"".join([f'<div class="memory-card"><span>{m.get("content")}</span><button onclick="purgeMemory(\'{m.get("id")}\')" style="color:red; background:none; border:none; cursor:pointer; font-weight:bold;">×</button></div>' for m in memories])}</div>
                </div>
            </div>
        </body>
    </html>
    """)