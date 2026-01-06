# smart_coach.py
import asyncio
import json
import os
from backboard import BackboardClient
from pydantic import ValidationError # <--- 1. Import this to handle the bug
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# ---------------------------------------------------------
# 1. SETUP: Mock Database & Tool Definition
# ---------------------------------------------------------
FOOD_DATABASE = {
    "pad thai": {"calories": 350, "ingredients": ["rice noodles", "peanuts", "egg", "shrimp"]},
    "grilled chicken salad": {"calories": 200, "ingredients": ["chicken breast", "lettuce", "olive oil"]},
    "protein shake": {"calories": 150, "ingredients": ["whey protein", "milk", "cocoa"]}
}

async def lookup_nutrition(food_name: str):
    """Simulates fetching data from a real nutrition API."""
    print(f"\n[TOOL] 🔍 Searching database for: {food_name}...")
    result = FOOD_DATABASE.get(food_name.lower())
    if result:
        return json.dumps(result)
    return json.dumps({"error": "Food not found in database"})

# ---------------------------------------------------------
# 2. MAIN APP LOGIC
# ---------------------------------------------------------
async def main():
    api_key = os.getenv("BACKBOARD_API_KEY")
    if not api_key:
        print("Please set your BACKBOARD_API_KEY environment variable.")
        return

    client = BackboardClient(api_key=api_key)

    # --- STEP A: DEFINE THE TOOL ---
    nutrition_tool = {
        "type": "function",
        "function": {
            "name": "lookup_nutrition",
            "description": "Get ingredients and calories for a specific food.",
            "parameters": {
                "type": "object",
                "properties": {
                    "food_name": {"type": "string", "description": "The name of the food"}
                },
                "required": ["food_name"]
            }
        }
    }

    # --- STEP B: CREATE ASSISTANT ---
    print("🤖 Creating Smart Coach Assistant...")
    assistant = await client.create_assistant(
        name="Smart Nutrition Coach",
        description="A nutritionist that remembers user preferences and looks up food data.",
        tools=[nutrition_tool]
    )

    # ==============================================================================
    # SESSION 1: The "Memory" Phase
    # ==============================================================================
    print("\n--- 🧵 SESSION 1 (Setting Preferences) ---")
    thread1 = await client.create_thread(assistant.assistant_id)
    user_fact = "I am strictly allergic to peanuts. Please remember this."
    print(f"User: {user_fact}")

    try:
        await client.add_message(
            thread_id=thread1.thread_id,
            content=user_fact,
            memory="Auto",
            stream=False
        )
        print("✅ Preference saved.")
    except ValidationError:
        # Ignore the library bug
        print("✅ Preference saved (Ignored library validation error).")

    # ==============================================================================
    # SESSION 2: The "Recall & Action" Phase
    # ==============================================================================
    print("\n--- 🧵 SESSION 2 (The Next Day - New Thread) ---")
    thread2 = await client.create_thread(assistant.assistant_id)
    query = "Can I safely eat Pad Thai?"
    print(f"User: {query}")

    response = None

    # 1. Send Message
    try:
        response = await client.add_message(
            thread_id=thread2.thread_id,
            content=query,
            memory="Auto",
            stream=False
        )
    except ValidationError:
        print("⚠️ Library error on send. Switching to polling mode...")

    # 2. Poll for Response (Handles both success and error cases)
    # We need to loop because the AI might be "thinking" or the tool call might be delayed
    tool_calls_found = []
    
    # Wait up to 10 seconds for a reply
    for i in range(5):
        if response and response.status == "REQUIRES_ACTION":
            # If we got a valid response object immediately
            tool_calls_found = response.tool_calls
            break
            
        # Poll the thread manually if the library crashed OR if we are waiting
        await asyncio.sleep(2)
        try:
            thread_data = await client.get_thread(thread2.thread_id)
            if hasattr(thread_data, 'messages') and thread_data.messages:
                last_msg = thread_data.messages[-1]
                
                # Check if the last message has tool calls
                if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                    tool_calls_found = last_msg.tool_calls
                    print("✅ Found tool call via polling!")
                    break
                
                # Or if it's just a text reply
                if last_msg.role == "assistant" and last_msg.content:
                    print(f"\n🤖 AI Response (No Tool): {last_msg.content}")
                    return
        except Exception as e:
            pass

    # --- STEP C: HANDLE TOOL CALLS ---
    if tool_calls_found:
        tool_outputs = []
        
        for tc in tool_calls_found:
            if tc.function.name == "lookup_nutrition":
                # 1. Parse arguments
                # Note: When polling, args might be a string string, so we ensure it's a dict
                args = tc.function.parsed_arguments
                if isinstance(args, str):
                    args = json.loads(args)
                
                food = args.get("food_name")
                
                # 2. Run our Python function
                data = await lookup_nutrition(food)
                
                # 3. Prepare output
                tool_outputs.append({
                    "tool_call_id": tc.id,
                    "output": data
                })
        
        # 4. Submit results back to AI
        print("📤 Submitting tool outputs...")
        try:
            final_response = await client.submit_tool_outputs(
                thread_id=thread2.thread_id,
                # If we polled, we might not have a run_id, but try/except handles it
                run_id=getattr(response, 'run_id', None), 
                tool_outputs=tool_outputs
            )
            print(f"\n🤖 AI Response: {final_response.content}")
        except:
             # Fallback: Just read the final message from the thread
            await asyncio.sleep(2)
            final_data = await client.get_thread(thread2.thread_id)
            print(f"\n🤖 AI Response: {final_data.messages[-1].content}")

    else:
        print("⚠️ No response or tool call detected.")

if __name__ == "__main__":
    asyncio.run(main())