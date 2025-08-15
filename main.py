from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import AIMessage, HumanMessage
from loader import load_and_create_vectorstore
import os, json, re
from dotenv import load_dotenv
import langdetect
import asyncio

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    raise ValueError("OPENAI_API_KEY not found in environment variables.")

app = FastAPI()

print("Loading vectorstore...")
chroma_db = load_and_create_vectorstore(
    pdf_path="docs/demoDoc.pdf",
    persist_dir="data",
    collection_name="lc_chroma_demo",
    openai_api_key=api_key
)
print("Vectorstore loaded successfully.")

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.8, openai_api_key=api_key)

sessions = {}

# ========================= Helpers =========================

def load_prompt_template(lang="en"):
    path_map = {
        "en": "prompts/prompt_en.md",
        "si": "prompts/prompt_si.md",
    }
    path = path_map.get(lang, path_map["en"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Error: Prompt file not found at {path}")
        return "History: {history}\nContext: {context}\nUser question: {input}\nAnswer:"

def extract_action_details(text: str):
    """
    Extract JSON action details from AI response if present.
    Handles cases where AI wraps JSON in ```json ... ``` blocks. Do not give any introduction or explanation.
    Returns the cleaned answer and the action JSON if found, otherwise returns the original text and None.
    """
    try:
        # Remove ```json and ``` if present
        cleaned_text = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()

        # Search for JSON object
        json_match = re.search(r"(\{.*\})", cleaned_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            action_json = json.loads(json_str)
            clean_answer = cleaned_text.replace(json_str, "").strip()
            return clean_answer, action_json
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(f"Error in extract_action_details: {e}")

    return text.strip(), None

def format_history(history_messages):
    if not history_messages:
        return "No conversation history yet."
    formatted = []
    for msg in history_messages:
        if isinstance(msg, HumanMessage):
            formatted.append(f"Human: {msg.content}")
        elif isinstance(msg, AIMessage):
            formatted.append(f"AI: {msg.content}")
    return "\n".join(formatted)

# ========================= WebSocket =========================

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Initialize session memory
    if session_id not in sessions:
        print(f"New session created: {session_id}")
        memory = ConversationBufferMemory(memory_key="history", return_messages=True)
        sessions[session_id] = {"memory": memory, "lang": "en"}
    else:
        memory = sessions[session_id]["memory"]

    # Send welcome message
    welcome_message = "Hello! This is Nawariyan AI assistant. How can I help you today?"
    memory.chat_memory.add_ai_message(welcome_message)
    await websocket.send_json({
        "answer": welcome_message,
        "action_details": None,
        "history": [m.content for m in memory.chat_memory.messages]
    })

    try:
        while True:
            data = await websocket.receive_text()
            memory.chat_memory.add_user_message(data)

            # Retrieve relevant context from vectorstore
            results = chroma_db.similarity_search(data, k=3)
            context = "\n\n---\n\n".join([doc.page_content for doc in results])

            # Detect language
            lang = langdetect.detect(data)
            if lang not in ["en", "si"]:
                lang = "en"

            # Load prompt template
            prompt_template = load_prompt_template(lang)
            history_messages = memory.load_memory_variables({})["history"]
            formatted_history = format_history(history_messages[:-1])

            # Construct prompt
            prompt = prompt_template.format(
                history=formatted_history,
                context=context,
                input=data
            )

            # Get AI response
            full_response = (await llm.ainvoke(prompt)).content
            clean_answer, action_details = extract_action_details(full_response)

            # Save AI message
            memory.chat_memory.add_ai_message(clean_answer)

            # Send back to frontend
            await websocket.send_json({
                "answer": clean_answer,
                "action_details": action_details,  # Frontend can now show a button if not None
                "history": [m.content for m in memory.chat_memory.messages]
            })

    except WebSocketDisconnect:
        print(f"Client disconnected: {session_id}")
    except Exception as e:
        print(f"Error in websocket for session {session_id}: {e}")
        await websocket.send_json({
            "answer": "Sorry, an internal error occurred. Please try again.",
            "action_details": None,
            "history": [m.content for m in memory.chat_memory.messages]
        })