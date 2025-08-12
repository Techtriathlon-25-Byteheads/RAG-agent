from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import AIMessage, HumanMessage
from loader import load_and_create_vectorstore
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

# Validate API Key
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

welcome_message = "Hello! This is Nawariyan AI assistant. How can I help you today?"

sessions = {}


def load_prompt_template(path="prompt.md"):
    """Loads the prompt template from a file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Error: Prompt file not found at {path}")
        return "History: {history}\nContext: {context}\nUser question: {input}\nAnswer:"


def extract_action_details(text: str):
    """
    Extracts a JSON block describing an action from the AI response text.
    
    Returns:
        tuple: (clean_answer, action_dict or None)
    """
    try:
        json_match = re.search(r"(\{.*?\})", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            action_json = json.loads(json_str)
            
            clean_answer = text.replace(json_str, "").strip()
            return clean_answer, action_json
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(f"An unexpected error occurred in extract_action_details: {e}")

    return text.strip(), None

def format_history(history_messages):
    """Formats the conversation history for the prompt."""
    if not history_messages:
        return "No conversation history yet."
    
    formatted = []
    for msg in history_messages:
        if isinstance(msg, HumanMessage):
            formatted.append(f"Human: {msg.content}")
        elif isinstance(msg, AIMessage):
            formatted.append(f"AI: {msg.content}")
    return "\n".join(formatted)


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    # Initialize session if it's new
    if session_id not in sessions:
        print(f"New session created: {session_id}")
        sessions[session_id] = ConversationBufferMemory(memory_key="history", return_messages=True)

    memory = sessions[session_id]

    # Send welcome message only if history is empty
    if not memory.chat_memory.messages:
        memory.chat_memory.add_ai_message(welcome_message)
        await websocket.send_text(welcome_message)

    try:
        while True:
            data = await websocket.receive_text()
            memory.chat_memory.add_user_message(data)

            # 1. Retrieve context from the vector store (RAG step)
            results = chroma_db.similarity_search(data, k=3)
            context = "\n\n---\n\n".join([doc.page_content for doc in results])

            # 2. Prepare the prompt
            prompt_template = load_prompt_template()
            
            # Load and format history correctly
            history_messages = memory.load_memory_variables({})['history']
            formatted_history = format_history(history_messages[:-1]) # Exclude the latest user message

            # CORRECTED: Pass all required variables ('history', 'context', 'input') to the prompt
            prompt = prompt_template.format(
                history=formatted_history, 
                context=context, 
                input=data
            )

            # 3. Get response from LLM
            # Using .invoke() which is the recommended method over the legacy .predict()
            full_response = (await llm.ainvoke(prompt)).content
            
            clean_answer, action_details = extract_action_details(full_response)

            # 4. Save only the clean, user-facing answer to memory
            memory.chat_memory.add_ai_message(clean_answer)

            # 5. Send the structured response to the client
            response_payload = {
                "answer": clean_answer,
                "action_details": action_details,
                "history": [m.content for m in memory.chat_memory.messages] # For client-side display
            }
            await websocket.send_json(response_payload)

    except WebSocketDisconnect:
        print(f"Client disconnected: {session_id}")
    except Exception as e:
        print(f"An error occurred in websocket for session {session_id}: {e}")
        await websocket.send_json({
            "answer": "Sorry, an internal error occurred. Please try again.",
            "action_details": None,
            "history": [m.content for m in memory.chat_memory.messages]
        })