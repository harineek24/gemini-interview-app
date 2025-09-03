#!/usr/bin/env python3
"""
Complete Working WebSocket Server for Gemini Live Interview
"""

import asyncio
import json
import base64
import os
import uuid
import traceback
import time
from datetime import datetime
from typing import Optional
import websockets
from websockets.server import serve
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import TCPServer

# Import dependencies for the handler
from dotenv import load_dotenv
from google import genai
import google.generativeai as genai_summary
from google.genai import types

# Load environment variables
load_dotenv()

# Simple data storage
interviews_db = {}
transcripts_db = {}

class SimpleInterview:
    def __init__(self):
        self.id = str(uuid.uuid4())
        self.status = 'active'
        self.started_at = datetime.now()
        self.ended_at = None
        self.duration_seconds = 0
        self.full_transcript = ""
        self.summary = ""
    
    @property
    def duration_formatted(self):
        minutes = self.duration_seconds // 60
        seconds = self.duration_seconds % 60
        return f"{minutes}:{seconds:02d}"

class SimpleTranscript:
    def __init__(self, interview_id: str, speaker: str, text: str, sequence: int):
        self.id = str(uuid.uuid4())
        self.interview_id = interview_id
        self.speaker = speaker
        self.text = text
        self.sequence_number = sequence
        self.timestamp = datetime.now()

class WorkingGeminiLiveHandler:
    def __init__(self, websocket, interviews_db: dict, transcripts_db: dict):
        self.websocket = websocket
        self.interview: Optional[SimpleInterview] = None
        self.interviews_db = interviews_db
        self.transcripts_db = transcripts_db
        
        # Get API key from environment
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
            
        # Use v1alpha API version for Live API
        self.client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
        self.model = "gemini-2.5-flash-preview-native-audio-dialog"
        self.transcript_counter = 0
        self.session = None
        self.conversation_inactive_threshold = 60  # Increased to 60 seconds
        self.last_activity = None
        
        # Audio streaming improvements
        self.audio_buffer = asyncio.Queue()
        self.last_audio_time = None
        self.silence_threshold = 1.5  # seconds
        self.is_session_active = True
        self.pending_audio_chunks = []
        self.audio_chunk_timeout = 0.2  # 200ms buffer
        
    async def start_interview(self):
        """Initialize the interview and start the Gemini Live session"""
        try:
            # Create new interview record
            self.interview = SimpleInterview()
            self.interviews_db[self.interview.id] = self.interview
            
            await self.websocket.send(json.dumps({
                'type': 'interview_started',
                'interview_id': str(self.interview.id)
            }))
            
            # Config for continuous conversation
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction="""You are a professional AI interviewer. Start by greeting the interviewee and asking them to introduce themselves. Then ask follow-up questions about their background, experience, and goals. Keep the conversation natural and engaging. Speak clearly and at a moderate pace.""",
                input_audio_transcription={}
            )
            
            # Start Gemini Live session
            async with self.client.aio.live.connect(model=self.model, config=config) as session:
                self.session = session
                self.last_activity = datetime.now()
                self.is_session_active = True
                
                print("✅ Gemini Live session started successfully")
                
                # Send initial greeting
                await session.send_realtime_input(
                    text="Hello! Welcome to this interview. Please start by introducing yourself and telling me a bit about your background."
                )
                
                # Start all tasks
                tasks = [
                    asyncio.create_task(self.handle_gemini_responses()),
                    asyncio.create_task(self.handle_client_messages()),
                    asyncio.create_task(self.audio_sender()),
                    asyncio.create_task(self.audio_streamer()),
                    asyncio.create_task(self.monitor_inactivity())
                ]
                
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                except Exception as e:
                    print(f"Error in session tasks: {e}")
                finally:
                    self.is_session_active = False
                    await self.end_interview()
                    
        except Exception as e:
            print(f"Error connecting to Gemini: {e}")
            traceback.print_exc()
            raise e

    async def handle_client_messages(self):
        """Handle messages from the client (user audio/commands)"""
        try:
            async for message in self.websocket:
                if not self.is_session_active:
                    break
                    
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    print("Invalid JSON received from client")
                    continue
                
                if data['type'] == 'audio_chunk':
                    try:
                        audio_data = base64.b64decode(data['audio'])
                        if len(audio_data) > 32:  # Only process meaningful audio
                            self.pending_audio_chunks.append(audio_data)
                            self.last_audio_time = time.time()
                            self.last_activity = datetime.now()
                            
                    except Exception as e:
                        print(f"Error processing audio chunk: {e}")
                    
                elif data['type'] == 'stop_interview':
                    print("🛑 User requested interview stop")
                    self.is_session_active = False
                    break
                    
        except websockets.exceptions.ConnectionClosed:
            print("Client disconnected")
        except Exception as e:
            print(f"Error handling client messages: {e}")
        finally:
            self.is_session_active = False
    
    async def audio_sender(self):
        """Send audio to Gemini with batching"""
        while self.is_session_active:
            try:
                # Check if we have pending audio and enough time has passed
                if (self.pending_audio_chunks and self.last_audio_time and 
                    time.time() - self.last_audio_time > self.audio_chunk_timeout):
                    
                    # Combine chunks
                    combined_audio = b''.join(self.pending_audio_chunks)
                    self.pending_audio_chunks.clear()
                    
                    if len(combined_audio) > 0:
                        await self.send_audio_to_gemini(combined_audio)
                
                # Send stream end after silence
                elif (self.last_audio_time and 
                      time.time() - self.last_audio_time > self.silence_threshold):
                    await self.send_audio_stream_end()
                    self.last_audio_time = None
                
                await asyncio.sleep(0.05)
                
            except Exception as e:
                print(f"Error in audio sender: {e}")
                await asyncio.sleep(0.1)
    
    async def handle_gemini_responses(self):
        """Handle responses from Gemini Live API"""
        try:
            async for response in self.session.receive():
                if not self.is_session_active:
                    break
                    
                if hasattr(response, 'server_content') and response.server_content:
                    
                    # Handle input transcription (user speech)
                    if (hasattr(response.server_content, 'input_transcription') and 
                        response.server_content.input_transcription and
                        hasattr(response.server_content.input_transcription, 'text')):
                        
                        user_text = response.server_content.input_transcription.text
                        if user_text and user_text.strip():
                            print(f"👤 User said: {user_text}")
                            await self.save_live_transcript('user', user_text)
                            await self.websocket.send(json.dumps({
                                'type': 'live_transcript',
                                'speaker': 'user',
                                'text': user_text
                            }))
                    
                    # Handle interruptions
                    if (hasattr(response.server_content, 'interrupted') and 
                        response.server_content.interrupted):
                        print("🤚 AI response interrupted")
                        # Clear audio buffer
                        while not self.audio_buffer.empty():
                            try:
                                self.audio_buffer.get_nowait()
                            except:
                                break
                    
                    # Handle model responses
                    if (hasattr(response.server_content, 'model_turn') and 
                        response.server_content.model_turn and
                        hasattr(response.server_content.model_turn, 'parts')):
                        
                        for part in response.server_content.model_turn.parts:
                            # Handle text responses
                            if hasattr(part, 'text') and part.text and part.text.strip():
                                ai_text = part.text.strip()
                                print(f"🤖 AI said: {ai_text}")
                                await self.save_live_transcript('ai', ai_text)
                                await self.websocket.send(json.dumps({
                                    'type': 'live_transcript',
                                    'speaker': 'ai',
                                    'text': ai_text
                                }))
                            
                            # Handle audio responses
                            if (hasattr(part, 'inline_data') and 
                                part.inline_data and 
                                hasattr(part.inline_data, 'data') and
                                part.inline_data.data):
                                
                                await self.audio_buffer.put({
                                    'data': part.inline_data.data,
                                    'mime_type': getattr(part.inline_data, 'mime_type', 'audio/pcm')
                                })
                    
                    # Handle turn complete
                    if (hasattr(response.server_content, 'turn_complete') and 
                        response.server_content.turn_complete):
                        print("✅ AI turn complete")
                        await self.audio_buffer.put({'end_turn': True})
                    
                    self.last_activity = datetime.now()
                    
        except Exception as e:
            print(f"Error handling Gemini responses: {e}")
            traceback.print_exc()
    
    async def audio_streamer(self):
        """Stream audio to client with proper buffering"""
        current_stream_id = None
        
        while self.is_session_active:
            try:
                audio_item = await asyncio.wait_for(self.audio_buffer.get(), timeout=0.1)
                
                if audio_item.get('end_turn'):
                    if current_stream_id:
                        await self.websocket.send(json.dumps({
                            'type': 'audio_stream_end',
                            'stream_id': current_stream_id
                        }))
                        current_stream_id = None
                    continue
                
                # Start new audio stream
                if not current_stream_id:
                    current_stream_id = str(uuid.uuid4())
                    await self.websocket.send(json.dumps({
                        'type': 'audio_stream_start',
                        'stream_id': current_stream_id,
                        'sample_rate': 24000,
                        'format': 'pcm_16'
                    }))
                
                # Send audio chunk
                audio_b64 = base64.b64encode(audio_item['data']).decode()
                await self.websocket.send(json.dumps({
                    'type': 'audio_chunk_response',
                    'stream_id': current_stream_id,
                    'audio': audio_b64,
                    'mime_type': audio_item.get('mime_type', 'audio/pcm')
                }))
                
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                print(f"Error in audio streamer: {e}")
                await asyncio.sleep(0.1)
    
    async def send_audio_to_gemini(self, audio_data: bytes):
        """Send audio data to Gemini Live API"""
        try:
            if len(audio_data) == 0:
                return
                
            # Send audio
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000"
                )
            )
            
        except Exception as e:
            print(f"Error sending audio to Gemini: {e}")

    async def send_audio_stream_end(self):
        """Send audio stream end to flush cached audio"""
        try:
            await self.session.send_realtime_input(audio_stream_end=True)
            print("📤 Sent audio stream end")
        except Exception as e:
            print(f"Error sending audio stream end: {e}")

    async def save_live_transcript(self, speaker: str, text: str):
        """Save live transcript"""
        try:
            if self.interview and text.strip():
                transcript = SimpleTranscript(
                    self.interview.id, 
                    speaker, 
                    text.strip(), 
                    self.transcript_counter
                )
                self.transcripts_db[transcript.id] = transcript
                self.transcript_counter += 1
                
        except Exception as e:
            print(f"Error saving transcript: {e}")
    
    async def monitor_inactivity(self):
        """Monitor for conversation inactivity"""
        while self.is_session_active:
            try:
                await asyncio.sleep(5)
                
                if self.last_activity:
                    inactive_seconds = (datetime.now() - self.last_activity).total_seconds()
                    
                    if inactive_seconds > self.conversation_inactive_threshold:
                        print(f"⏰ Conversation inactive for {inactive_seconds:.1f}s")
                        self.is_session_active = False
                        break
                        
            except Exception as e:
                print(f"Error in inactivity monitor: {e}")
                await asyncio.sleep(1)

    async def end_interview(self):
        """End the interview and generate summary"""
        if self.interview and self.interview.status == 'active':
            try:
                print(f"🏁 Ending interview {self.interview.id}")
                
                self.interview.status = 'completed'
                self.interview.ended_at = datetime.now()
                
                # Calculate duration
                duration = self.interview.ended_at - self.interview.started_at
                self.interview.duration_seconds = int(duration.total_seconds())
                
                # Generate transcript
                interview_transcripts = [t for t in self.transcripts_db.values() 
                                       if t.interview_id == self.interview.id]
                interview_transcripts.sort(key=lambda x: x.sequence_number)
                
                full_transcript = '\n'.join([f"{t.speaker.upper()}: {t.text}" 
                                           for t in interview_transcripts])
                self.interview.full_transcript = full_transcript
                
                # Generate summary
                print("📝 Generating interview summary...")
                summary = await self.generate_summary(full_transcript)
                self.interview.summary = summary
                
                # Send audio stream end
                try:
                    await self.send_audio_stream_end()
                except:
                    pass
                
                # Notify client
                await self.websocket.send(json.dumps({
                    'type': 'interview_ended',
                    'interview_id': str(self.interview.id),
                    'duration': self.interview.duration_formatted,
                    'summary': summary,
                    'total_transcripts': len(interview_transcripts)
                }))
                
                print(f"✅ Interview completed - Duration: {self.interview.duration_formatted}, Transcripts: {len(interview_transcripts)}")
                
            except Exception as e:
                print(f"❌ Error ending interview: {e}")
                if self.interview:
                    self.interview.status = 'failed'
    
    async def generate_summary(self, transcript: str) -> str:
        """Generate interview summary"""
        try:
            if not transcript.strip():
                return "No conversation content to summarize."
            
            api_key = os.environ.get("GEMINI_API_KEY")
            genai_summary.configure(api_key=api_key)
            model = genai_summary.GenerativeModel('gemini-1.5-flash')
            
            prompt = f"""
            Please provide a concise summary of this interview conversation.
            Focus on key topics, insights, and overall tone.
            Keep it under 200 words.
            
            Transcript:
            {transcript}
            """
            
            response = await asyncio.to_thread(model.generate_content, prompt)
            return response.text.strip() if response.text else "Summary could not be generated."
            
        except Exception as e:
            print(f"❌ Error generating summary: {e}")
            return f"Summary generation failed: {str(e)}"

# HTML content (same as before but with debug info)
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Working Gemini Live Interview</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 min-h-screen p-5">
    <div class="max-w-3xl mx-auto bg-white rounded-3xl p-8 shadow-2xl">
        <h1 class="text-center text-gray-800 mb-8 text-5xl font-light">🎙️ Working Gemini Interview</h1>
        
        <div class="status text-center mb-5 p-4 rounded-xl font-semibold bg-blue-50 text-blue-800" id="status">
            Ready to start interview
        </div>

        <div class="flex justify-center gap-5 mb-8">
            <button class="bg-green-500 hover:bg-green-600 text-white px-8 py-4 rounded-full font-semibold" id="startBtn">
                Start Interview
            </button>
            <button class="bg-red-500 hover:bg-red-600 text-white px-8 py-4 rounded-full font-semibold" id="stopBtn" disabled>
                Stop Interview
            </button>
        </div>

        <div class="bg-gray-50 rounded-2xl p-5 mb-5 max-h-96 overflow-y-auto">
            <div class="text-lg font-semibold text-gray-600 mb-4 text-center">Live Transcript</div>
            <div id="transcripts">
                <p class="text-center text-gray-500 italic">Transcripts will appear here...</p>
            </div>
        </div>

        <div class="hidden bg-amber-50 rounded-2xl p-5 mt-5" id="summaryContainer">
            <div class="text-lg font-semibold text-amber-700 mb-3">Interview Summary</div>
            <div id="summary" class="text-gray-700"></div>
        </div>
        
        <!-- Debug info -->
        <div class="bg-gray-100 p-3 rounded-lg mt-5 text-xs">
            <div>Status: <span id="debugStatus">Not connected</span></div>
            <div>Audio chunks sent: <span id="audioChunks">0</span></div>
            <div>Responses received: <span id="responseCount">0</span></div>
        </div>
        
        <audio id="audioPlayer" preload="auto"></audio>
    </div>

    <script>
        class WorkingInterviewApp {
            constructor() {
                this.ws = null;
                this.audioContext = null;
                this.audioStream = null;
                this.isRecording = false;
                this.audioChunkCount = 0;
                this.responseCount = 0;
                
                this.initializeElements();
                this.initializeEventListeners();
            }

            initializeElements() {
                this.startBtn = document.getElementById('startBtn');
                this.stopBtn = document.getElementById('stopBtn');
                this.status = document.getElementById('status');
                this.transcripts = document.getElementById('transcripts');
                this.summaryContainer = document.getElementById('summaryContainer');
                this.summary = document.getElementById('summary');
                this.audioPlayer = document.getElementById('audioPlayer');
                
                // Debug elements
                this.debugStatus = document.getElementById('debugStatus');
                this.audioChunksEl = document.getElementById('audioChunks');
                this.responseCountEl = document.getElementById('responseCount');
            }

            initializeEventListeners() {
                this.startBtn.addEventListener('click', () => this.startInterview());
                this.stopBtn.addEventListener('click', () => this.stopInterview());
            }

            async startInterview() {
                try {
                    this.debugStatus.textContent = "Connecting...";
                    this.ws = new WebSocket('ws://localhost:8001');
                    
                    this.ws.onopen = () => {
                        console.log('WebSocket connected');
                        this.debugStatus.textContent = "Connected";
                        this.updateStatus('Connected to Gemini Live...', 'active');
                    };

                    this.ws.onmessage = (event) => {
                        this.responseCount++;
                        this.responseCountEl.textContent = this.responseCount;
                        this.handleWebSocketMessage(JSON.parse(event.data));
                    };

                    this.ws.onclose = () => {
                        console.log('WebSocket closed');
                        this.debugStatus.textContent = "Disconnected";
                        this.resetInterface();
                    };

                    this.ws.onerror = (error) => {
                        console.error('WebSocket error:', error);
                        this.debugStatus.textContent = "Error";
                        this.updateStatus('Connection failed', 'idle');
                    };

                    await this.setupAudioRecording();
                    
                } catch (error) {
                    console.error('Error starting interview:', error);
                    this.updateStatus('Failed to start interview', 'idle');
                }
            }

            async setupAudioRecording() {
                try {
                    this.audioStream = await navigator.mediaDevices.getUserMedia({ 
                        audio: {
                            sampleRate: 16000,
                            channelCount: 1,
                            echoCancellation: true,
                            noiseSuppression: true
                        } 
                    });

                    this.audioContext = new AudioContext({sampleRate: 16000});
                    const source = this.audioContext.createMediaStreamSource(this.audioStream);
                    const processor = this.audioContext.createScriptProcessor(1024, 1, 1);

                    processor.onaudioprocess = (event) => {
                        if (!this.isRecording) return;
                        
                        const inputBuffer = event.inputBuffer.getChannelData(0);
                        if (inputBuffer.length < 128) return;
                        
                        const pcmData = this.floatTo16BitPCM(inputBuffer);
                        const base64Audio = btoa(String.fromCharCode.apply(null, new Uint8Array(pcmData)));

                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({
                                type: 'audio_chunk',
                                audio: base64Audio
                            }));
                            
                            this.audioChunkCount++;
                            this.audioChunksEl.textContent = this.audioChunkCount;
                        }
                    };

                    source.connect(processor);
                    processor.connect(this.audioContext.destination);
                    this.isRecording = true;

                } catch (error) {
                    console.error('Error setting up audio:', error);
                    throw error;
                }
            }
            
            floatTo16BitPCM(input) {
                const buffer = new ArrayBuffer(input.length * 2);
                const view = new DataView(buffer);
                for (let i = 0; i < input.length; i++) {
                    const sample = Math.max(-1, Math.min(1, input[i]));
                    view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true);
                }
                return buffer;
            }

            stopInterview() {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'stop_interview' }));
                }
                this.cleanup();
            }

            cleanup() {
                this.isRecording = false;
                if (this.audioContext) this.audioContext.close();
                if (this.audioStream) this.audioStream.getTracks().forEach(track => track.stop());
                if (this.ws) this.ws.close();
            }

            resetInterface() {
                this.startBtn.disabled = false;
                this.stopBtn.disabled = true;
                this.updateStatus('Ready to start interview', 'idle');
            }

            handleWebSocketMessage(data) {
                console.log('Received:', data.type);
                
                switch (data.type) {
                    case 'interview_started':
                        this.startBtn.disabled = true;
                        this.stopBtn.disabled = false;
                        this.updateStatus('Interview in progress...', 'active');
                        this.transcripts.innerHTML = '';
                        break;

                    case 'live_transcript':
                        this.addTranscript(data.speaker, data.text);
                        break;

                    case 'audio_stream_start':
                        console.log('Audio stream starting');
                        break;
                        
                    case 'audio_chunk_response':
                        // Buffer and play audio
                        this.playAudio(data.audio);
                        break;
                        
                    case 'audio_stream_end':
                        console.log('Audio stream ended');
                        break;

                    case 'interview_ended':
                        this.updateStatus(`Interview completed (${data.duration})`, 'completed');
                        this.showSummary(data.summary);
                        this.cleanup();
                        this.resetInterface();
                        break;
                }
            }

            addTranscript(speaker, text) {
                const item = document.createElement('div');
                item.className = `mb-4 p-3 rounded-xl ${
                    speaker === 'user' ? 'bg-blue-50 border-l-4 border-blue-500' : 'bg-purple-50 border-l-4 border-purple-500'
                }`;
                
                item.innerHTML = `
                    <div class="font-bold mb-1 text-xs uppercase ${speaker === 'user' ? 'text-blue-700' : 'text-purple-700'}">
                        ${speaker === 'user' ? 'You' : 'AI Interviewer'}
                    </div>
                    <div class="text-gray-700">${text}</div>
                `;
                
                this.transcripts.appendChild(item);
                this.transcripts.scrollTop = this.transcripts.scrollHeight;
            }

            playAudio(base64Audio) {
                try {
                    const audioData = atob(base64Audio);
                    const uint8Array = new Uint8Array(audioData.length);
                    
                    for (let i = 0; i < audioData.length; i++) {
                        uint8Array[i] = audioData.charCodeAt(i);
                    }
                    
                    const wavBuffer = this.createWAVFromPCM(uint8Array, 24000);
                    const blob = new Blob([wavBuffer], { type: 'audio/wav' });
                    const audioUrl = URL.createObjectURL(blob);
                    
                    this.audioPlayer.src = audioUrl;
                    this.audioPlayer.play();
                    
                } catch (error) {
                    console.error('Error playing audio:', error);
                }
            }

            createWAVFromPCM(pcmData, sampleRate) {
                const length = pcmData.length;
                const buffer = new ArrayBuffer(44 + length);
                const view = new DataView(buffer);
                
                // WAV header
                const writeString = (offset, string) => {
                    for (let i = 0; i < string.length; i++) {
                        view.setUint8(offset + i, string.charCodeAt(i));
                    }
                };
                
                writeString(0, 'RIFF');
                view.setUint32(4, 36 + length, true);
                writeString(8, 'WAVE');
                writeString(12, 'fmt ');
                view.setUint32(16, 16, true);
                view.setUint16(20, 1, true);
                view.setUint16(22, 1, true);
                view.setUint32(24, sampleRate, true);
                view.setUint32(28, sampleRate * 2, true);
                view.setUint16(32, 2, true);
                view.setUint16(34, 16, true);
                writeString(36, 'data');
                view.setUint32(40, length, true);
                
                for (let i = 0; i < length; i++) {
                    view.setUint8(44 + i, pcmData[i]);
                }
                
                return buffer;
            }

            showSummary(summaryText) {
                this.summary.textContent = summaryText;
                this.summaryContainer.classList.remove('hidden');
            }

            updateStatus(text, className) {
                this.status.textContent = text;
                this.status.className = `status text-center mb-5 p-4 rounded-xl font-semibold ${
                    className === 'active' ? 'bg-green-50 text-green-800' : 
                    className === 'completed' ? 'bg-amber-50 text-amber-700' :
                    'bg-blue-50 text-blue-800'
                }`;
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            new WorkingInterviewApp();
        });
    </script>
</body>
</html>
"""

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode())
        else:
            self.send_response(404)
            self.end_headers()

async def handle_websocket(websocket, path):
    """Handle WebSocket connections"""
    print(f"New WebSocket connection from {websocket.remote_address}")
    
    try:
        handler = WorkingGeminiLiveHandler(websocket, interviews_db, transcripts_db)
        await handler.start_interview()
        
    except Exception as e:
        print(f"Error in WebSocket handler: {e}")
        traceback.print_exc()
        try:
            await websocket.close()
        except:
            pass

def start_http_server():
    """Start HTTP server on port 8000"""
    with TCPServer(("", 8000), CustomHTTPRequestHandler) as httpd:
        print("HTTP Server running on http://localhost:8000")
        httpd.serve_forever()

async def start_websocket_server():
    """Start WebSocket server on port 8001"""
    print("WebSocket Server starting on ws://localhost:8001")
    async with serve(handle_websocket, "localhost", 8001):
        print("WebSocket Server running on ws://localhost:8001")
        await asyncio.Future()

def main():
    print("Starting WORKING Gemini Live Interview Application...")
    print("HTTP Server: http://localhost:8000")
    print("WebSocket Server: ws://localhost:8001")
    print("\n🔧 Key improvements:")
    print("- Better error handling and validation")
    print("- Initial AI greeting to start conversation") 
    print("- Debug counters to track audio/responses")
    print("- Improved transcription capture")
    print("- More robust audio processing")
    
    # Start HTTP server in thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    
    # Start WebSocket server
    try:
        asyncio.run(start_websocket_server())
    except KeyboardInterrupt:
        print("\nShutting down servers...")

if __name__ == "__main__":
    main()