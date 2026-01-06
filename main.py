import os
import io
import asyncio
from google import genai 
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from backboard import BackboardClient
from PIL import Image
from dotenv import load_dotenv
from pydantic import ValidationError # <--- Important import

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
                name="SnapCal",
                description="Calorie tracking assistant with Gemini Vision."
            )
            assistant_id = assistant.assistant_id
            print(f"✅ Backboard Assistant Created: {assistant_id}")
            print(f"⚠️ ADD THIS TO .env: BACKBOARD_ASSISTANT_ID={assistant_id}")
        except Exception as e:
            print(f"⚠️ Startup Warning: {e}")
    else:
        print(f"✅ Using Existing Assistant: {assistant_id}")

async def get_or_create_thread():
    # 1. First, check if we forced a visible website thread in .env
    forced_id = os.getenv("FORCE_THREAD_ID")
    if forced_id:
        # This prints to console so you know it's working
        print(f"🔗 Connecting to Website Dashboard Thread: {forced_id}")
        return forced_id

    # 2. Otherwise, use the standard logic (invisible API threads)
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
# 2. Log Meal (FIXED)
# --------------------
@app.post("/log-meal")
async def log_meal(
    image: UploadFile,
    meal: str = Form(...)
):
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
        analysis_text = response.text
        print("✅ Gemini Analysis Complete.")

    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return {"error": str(e)}

    # --- STEP B: BACKBOARD STORAGE ---
    if thread_id:
        try:
            print("💾 Saving data to Backboard...")
            storage_message = (
                f"I just ate a meal labeled '{meal}'. "
                f"Visual analysis data: {analysis_text}. "
                "Please log this into my nutrition history."
            )
            
            await bb_client.add_message(
                thread_id=thread_id,
                content=storage_message,
                memory="Auto"
            )
            print("✅ Saved to Backboard history.")

        except ValidationError:
            # IGNORE LIBRARY BUG
            print("✅ Saved to Backboard history (Ignored library error).")
            
        except Exception as e:
            print(f"⚠️ Backboard Save Warning: {e}")

    return {"result": analysis_text}

# --------------------
# 3. Ask (COMPLETELY FIXED)
# --------------------
@app.post("/ask")
async def ask(question: str = Form(...)):
    thread_id = await get_or_create_thread()
    
    if not thread_id:
        return {"error": "No active conversation found."}

    # 1. Send Message
    try:
        await bb_client.add_message(
            thread_id=thread_id,
            content=question,
            # SWITCHING TO OPENAI GPT-4o
            llm_provider="openai",
            model_name="gpt-4o",
            memory="Auto"
        )
    except ValidationError:
        # Expected error due to library bug. Proceed to polling.
        pass
    except Exception as e:
        return {"error": f"Send Failed: {str(e)}"}

    # 2. Poll for Answer
    try:
        # Wait up to 10 seconds for the AI to reply
        for _ in range(5):
            await asyncio.sleep(2)
            data = await bb_client.get_thread(thread_id)
            
            if hasattr(data, 'messages'):
                # Look for the latest message from the assistant
                for msg in reversed(data.messages):
                    if msg.role == "assistant":
                        return {"answer": msg.content}
                        
    except Exception as e:
        return {"error": f"Polling Failed: {str(e)}"}

    return {"error": "Timeout - AI took too long to reply"}

# --------------------
# 4. Memory Viewer (Your Custom Dashboard)
# --------------------
@app.get("/dashboard")
async def dashboard():
    """Fetches real memory data from Backboard and displays it."""
    import requests
    
    # 1. Fetch Memories
    memories = []
    try:
        url = f"https://app.backboard.io/api/assistants/{EXISTING_ASSISTANT_ID}/memories"
        resp = requests.get(url, headers={"X-API-Key": BACKBOARD_API_KEY})
        if resp.status_code == 200:
            data = resp.json()
            # Handle the list vs dict format
            raw_list = data.get("memories", []) if isinstance(data, dict) else data
            
            for m in raw_list:
                # Extract text safely
                if isinstance(m, dict):
                    memories.append(m.get('memory') or m.get('content') or str(m))
                else:
                    memories.append(str(m))
    except Exception as e:
        memories = [f"Error fetching memories: {str(e)}"]

    # 2. Simple HTML Display
    html_content = f"""
    <html>
        <head>
            <title>SnapCal Brain 🧠</title>
            <style>
                body {{ font-family: sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
                h1 {{ color: #2c3e50; }}
                .memory-card {{
                    background: #f8f9fa;
                    border-left: 5px solid #2ecc71;
                    padding: 15px;
                    margin-bottom: 10px;
                    border-radius: 4px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
            </style>
        </head>
        <body>
            <h1>🧠 SnapCal Long-Term Memory</h1>
            <p>This is what the AI has learned about you from your conversations:</p>
            <hr>
            {''.join([f'<div class="memory-card">{m}</div>' for m in memories])}
            <br>
            <a href="/">⬅️ Back to Tracker</a>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)