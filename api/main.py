from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import os
import sys
from pathlib import Path

# Add the Django project root to the Python path
django_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(django_root))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interview_app.settings')
import django
django.setup()

from interviews.models import Interview, LiveTranscript
from .websocket import GeminiLiveHandler

app = FastAPI(title="Gemini Live Interview API")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def get_homepage():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@app.websocket("/ws/interview")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    handler = GeminiLiveHandler(websocket)
    
    try:
        await handler.start_interview()
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"Error in websocket: {e}")
        await websocket.close()
