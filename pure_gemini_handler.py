#!/usr/bin/env python3
"""
Gemini Live Handler - COMPLETE VERSION with Web Speech API Integration
"""

import asyncio
import json
import base64
import os
import traceback
import struct
import pyaudio
import numpy as np
from google import genai
from google.genai import types

# Audio constants
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000

class PureGeminiHandler:
    def __init__(self, client_websocket, interviews_db: dict, transcripts_db: dict):
        self.client_ws = client_websocket
        self.interviews_db = interviews_db
        self.transcripts_db = transcripts_db
        
        self.is_session_active = True
        self.audio_input_stream = None
        self.audio_output_stream = None
        
        # Async queues for audio processing
        self.audio_out_queue = asyncio.Queue()  # Audio to send to Gemini
        self.audio_in_queue = asyncio.Queue()   # Audio from Gemini to play
        
        # Initialize Gemini client
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
            
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-live-2.5-flash-preview"
        self.live_session = None
        
        # Initialize PyAudio
        self.pa = pyaudio.PyAudio()

    async def start_interview_session(self):
        """Main entry point to start and manage the interview."""
        try:
            print("🔄 Starting Gemini Live session...")
            
            # Start client message handler and Gemini session
            tasks = [
                asyncio.create_task(self.handle_client_messages()),
                asyncio.create_task(self.manage_gemini_session())
            ]
            
            try:
                await asyncio.gather(*tasks)
            except Exception as e:
                print(f"Error in main tasks: {e}")
                traceback.print_exc()
                
        except Exception as e:
            print(f"❌ Error in interview session: {e}")
            traceback.print_exc()
        finally:
            await self.end_interview()

    async def handle_client_messages(self):
        """Listen for messages from the browser."""
        try:
            # Send interview started message
            await self.client_ws.send(json.dumps({
                'type': 'interview_started', 
                'interview_id': 'gemini-live-interview'
            }))
            
            async for message in self.client_ws:
                if not self.is_session_active:
                    break
                try:
                    data = json.loads(message)
                    
                    if data.get('type') == 'stop_interview':
                        print("🛑 User requested interview stop.")
                        self.is_session_active = False
                        break
                    
                    # NEW: Handle user speech from Web Speech API
                    elif data.get('type') == 'user_speech':
                        user_text = data.get('text', '').strip()
                        if user_text:
                            print(f"🗣️ User speech received: '{user_text}'")
                            # Send text to Gemini so it can understand and respond
                            if self.live_session:
                                await self.live_session.send(input=user_text, end_of_turn=True)
                                print(f"📤 Sent user text to Gemini: '{user_text}'")
                            
                except json.JSONDecodeError:
                    print(f"Warning: Invalid JSON from client: {message}")
                    
        except Exception as e:
            print(f"Error handling client messages: {e}")
            self.is_session_active = False
    
    async def manage_gemini_session(self):
        """Connect to Gemini and manage the live session."""
        try:
            print("🔗 Connecting to Gemini Live API...")
            
            # Enhanced config for better conversation flow
            config = {
                "response_modalities": ["AUDIO"],               # Audio responses only
                "output_audio_transcription": {},              # Get AI speech transcripts
                "generation_config": {
                    "temperature": 0.8,                        # Make responses more conversational
                    "max_output_tokens": 1000,
                },
                "system_instruction": {
                    "parts": [{
                        "text": """You are an AI interviewer conducting a professional interview. Your role is to:
                        1. Ask follow-up questions based on what the candidate says
                        2. Keep the conversation flowing naturally
                        3. Ask about their experience, skills, and background
                        4. Be conversational but professional
                        5. Always respond to what they tell you with relevant follow-up questions
                        
                        After each response from the candidate, ask a relevant follow-up question to keep the interview going."""
                    }]
                }
            }
            
            print(f"🔄 Connecting with model: {self.model} (Audio + Web Speech API)")
            
            async with self.client.aio.live.connect(model=self.model, config=config) as session:
                self.live_session = session
                print("✅ Connected to Gemini Live API")
                
                # Send initial prompt
                await session.send(input="Hello! Please introduce yourself and tell me about your background.", end_of_turn=True)
                
                # Start audio processing tasks (no need for microphone input now)
                print("🚀 Starting audio processing tasks...")
                
                audio_tasks = await asyncio.gather(
                    asyncio.create_task(self.receive_audio()),
                    asyncio.create_task(self.play_audio()),
                    return_exceptions=True
                )
                
                print("🏁 Audio tasks completed")
                
        except Exception as e:
            print(f"❌ Error in Gemini session: {e}")
            await self.client_ws.send(json.dumps({
                'type': 'error',
                'message': f"Gemini API Error: {str(e)}"
            }))
            traceback.print_exc()
        finally:
            self.live_session = None

    async def receive_audio(self):
        """Receive responses from Gemini with transcription support."""
        try:
            print("👂 Audio receiver ready, waiting for Gemini...")
            
            if not self.live_session:
                print("❌ No live session available for receiving")
                return
                
            async for response in self.live_session.receive():
                if not self.is_session_active:
                    break

                # Handle server content
                if hasattr(response, 'server_content') and response.server_content:
                    server_content = response.server_content
                    
                    # Handle OUTPUT transcription (AI speech)
                    if (hasattr(server_content, 'output_transcription') and 
                        server_content.output_transcription and
                        hasattr(server_content.output_transcription, 'text')):
                        
                        ai_text = server_content.output_transcription.text
                        if ai_text.strip():
                            print(f"🤖 AI said: '{ai_text}'")
                            await self.client_ws.send(json.dumps({
                                'type': 'live_transcript',
                                'speaker': 'ai',
                                'text': ai_text
                            }))
                    
                    # Handle AI audio responses
                    if (hasattr(server_content, 'model_turn') and 
                        server_content.model_turn and
                        hasattr(server_content.model_turn, 'parts')):
                        
                        for part in server_content.model_turn.parts:
                            # Handle audio data
                            if (hasattr(part, 'inline_data') and 
                                part.inline_data and
                                hasattr(part.inline_data, 'data')):
                                
                                audio_data = part.inline_data.data
                                print(f"🔊 Received audio ({len(audio_data)} bytes)")
                                # Queue for playback
                                await self.audio_in_queue.put(audio_data)
                    
                    # Handle turn completion
                    if hasattr(server_content, 'turn_complete') and server_content.turn_complete:
                        print("✅ AI finished speaking")
                
        except Exception as e:
            print(f"❌ Error receiving from Gemini: {e}")
            traceback.print_exc()

    async def play_audio(self):
        """Play audio responses from Gemini."""
        try:
            print("🔊 Setting up audio playback...")
            
            # Open output stream
            self.audio_output_stream = await asyncio.to_thread(
                self.pa.open,
                format=FORMAT,
                channels=CHANNELS,
                rate=RECEIVE_SAMPLE_RATE,
                output=True,
            )
            
            print("🔊 Audio playback ready")
            
            while self.is_session_active:
                try:
                    # Get audio from queue
                    audio_data = await self.audio_in_queue.get()
                    
                    # Play locally
                    await asyncio.to_thread(self.audio_output_stream.write, audio_data)
                    
                    # Also send to browser
                    audio_b64 = base64.b64encode(audio_data).decode('ascii')
                    await self.client_ws.send(json.dumps({
                        'type': 'audio_chunk_response',
                        'audio': audio_b64
                    }))
                    
                    print("▶️ Played audio chunk")
                    
                except Exception as e:
                    print(f"Error playing audio: {e}")
                    break
            
        except Exception as e:
            print(f"❌ Error setting up audio playback: {e}")
            traceback.print_exc()
        finally:
            if self.audio_output_stream:
                try:
                    await asyncio.to_thread(self.audio_output_stream.stop_stream)
                    await asyncio.to_thread(self.audio_output_stream.close)
                except:
                    pass
            print("🔊 Audio playback stopped")

    async def end_interview(self):
        """Clean up all resources."""
        print("🏁 Ending interview session...")
        self.is_session_active = False
        
        # Clean up PyAudio
        try:
            await asyncio.to_thread(self.pa.terminate)
        except:
            pass
        
        print("✅ Interview session ended cleanly")