#!/usr/bin/env python3
"""
Complete Working WebSocket Server for Gemini Live Interview with Speech Recognition
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
from pure_gemini_handler import PureGeminiHandler

# Load environment variables
load_dotenv()

# Simple data storage
interviews_db = {}
transcripts_db = {}

# HTML content with Web Speech API integration
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
        
        <!-- Debug info with speech recognition status -->
        <div class="bg-gray-100 p-3 rounded-lg mt-5 text-xs">
            <div>WebSocket: <span id="debugStatus">Not connected</span></div>
            <div>Speech Recognition: <span id="speechStatus">Not started</span></div>
            <div>Server audio processing: <span class="text-green-600">Enabled (PyAudio)</span></div>
            <div>Responses received: <span id="responseCount">0</span></div>
            <div>API Status: <span id="apiStatus">Not connected</span></div>
        </div>
        
        <audio id="audioPlayer" preload="auto"></audio>
    </div>

    <script>
        class WorkingInterviewApp {
            constructor() {
                this.ws = null;
                this.recognition = null;  // Added for Web Speech API
                this.audioContext = null;
                this.audioStream = null;
                this.isRecording = false;
                this.responseCount = 0;
                
                // Audio playback queue
                this.audioQueue = [];
                this.isPlaying = false;
                
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
                this.speechStatus = document.getElementById('speechStatus');  // Added
                this.responseCountEl = document.getElementById('responseCount');
                this.apiStatus = document.getElementById('apiStatus');
            }

            initializeEventListeners() {
                this.startBtn.addEventListener('click', () => this.startInterview());
                this.stopBtn.addEventListener('click', () => this.stopInterview());
            }

            async startInterview() {
                try {
                    this.debugStatus.textContent = "Connecting...";
                    this.apiStatus.textContent = "Connecting to Gemini...";
                    
                    this.ws = new WebSocket('ws://localhost:8001');
                    
                    this.ws.onopen = () => {
                        console.log('WebSocket connected');
                        this.debugStatus.textContent = "Connected";
                        this.updateStatus('Connected to server...', 'active');
                        
                        // Start speech recognition immediately after WebSocket connects
                        this.startSpeechRecognition();
                    };

                    this.ws.onmessage = (event) => {
                        this.responseCount++;
                        this.responseCountEl.textContent = this.responseCount;
                        this.handleWebSocketMessage(JSON.parse(event.data));
                    };

                    this.ws.onclose = () => {
                        console.log('WebSocket closed');
                        this.debugStatus.textContent = "Disconnected";
                        this.apiStatus.textContent = "Disconnected";
                        this.stopSpeechRecognition();
                        this.resetInterface();
                    };

                    this.ws.onerror = (error) => {
                        console.error('WebSocket error:', error);
                        this.debugStatus.textContent = "Error";
                        this.apiStatus.textContent = "Connection failed";
                        this.updateStatus('Connection failed', 'idle');
                    };

                    // Request microphone permission
                    await this.requestMicrophonePermission();
                    
                } catch (error) {
                    console.error('Error starting interview:', error);
                    this.updateStatus('Failed to start interview', 'idle');
                }
            }

            startSpeechRecognition() {
                if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                    this.recognition = new SpeechRecognition();
                    
                    this.recognition.continuous = true;
                    this.recognition.interimResults = false;
                    this.recognition.lang = 'en-US';
                    this.recognition.maxAlternatives = 1;
                    
                    this.recognition.onstart = () => {
                        console.log('Speech recognition started');
                        this.speechStatus.textContent = "Listening...";
                        this.speechStatus.className = "text-green-600";
                    };
                    
                    this.recognition.onresult = (event) => {
                        const last = event.results.length - 1;
                        const transcript = event.results[last][0].transcript.trim();
                        
                        if (transcript) {
                            console.log('User said:', transcript);
                            this.addTranscript('user', transcript);
                            
                            // Send transcribed text to server so AI knows what was said
                            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                                this.ws.send(JSON.stringify({
                                    type: 'user_speech',
                                    text: transcript
                                }));
                            }
                        }
                    };
                    
                    this.recognition.onerror = (event) => {
                        console.log('Speech recognition error:', event.error);
                        this.speechStatus.textContent = `Error: ${event.error}`;
                        this.speechStatus.className = "text-red-600";
                        
                        // Restart recognition after error
                        setTimeout(() => {
                            if (this.recognition && this.ws && this.ws.readyState === WebSocket.OPEN) {
                                this.recognition.start();
                            }
                        }, 1000);
                    };
                    
                    this.recognition.onend = () => {
                        console.log('Speech recognition ended');
                        // Auto-restart recognition if session is still active
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            setTimeout(() => {
                                if (this.recognition) {
                                    this.recognition.start();
                                }
                            }, 500);
                        }
                    };
                    
                    this.recognition.start();
                    
                } else {
                    console.log('Speech recognition not supported');
                    this.speechStatus.textContent = "Not supported";
                    this.speechStatus.className = "text-yellow-600";
                }
            }

            stopSpeechRecognition() {
                if (this.recognition) {
                    this.recognition.stop();
                    this.recognition = null;
                    this.speechStatus.textContent = "Stopped";
                    this.speechStatus.className = "text-gray-600";
                }
            }

            async requestMicrophonePermission() {
                try {
                    // Request permission for speech recognition
                    const stream = await navigator.mediaDevices.getUserMedia({ 
                        audio: {
                            sampleRate: 16000,
                            channelCount: 1,
                            echoCancellation: true,
                            noiseSuppression: true
                        } 
                    });
                    
                    // Close the stream immediately since server handles audio
                    stream.getTracks().forEach(track => track.stop());
                    
                    console.log('Microphone permission granted');
                    
                } catch (error) {
                    console.error('Error requesting microphone permission:', error);
                    this.updateStatus('Microphone permission denied', 'idle');
                    throw error;
                }
            }

            stopInterview() {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'stop_interview' }));
                }
                this.stopSpeechRecognition();
                this.cleanup();
            }

            cleanup() {
                this.isRecording = false;
                
                // Stop speech recognition
                this.stopSpeechRecognition();
                
                // Stop any playing audio
                if (this.audioPlayer) {
                    this.audioPlayer.pause();
                    this.audioPlayer.src = '';
                }
                
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
                        this.apiStatus.textContent = "Connected to Gemini Live";
                        this.transcripts.innerHTML = '';
                        break;

                    case 'live_transcript':
                        this.addTranscript(data.speaker, data.text);
                        break;

                    case 'audio_stream_start':
                        console.log('Audio stream starting');
                        break;
                        
                    case 'audio_chunk_response':
                        this.processAudioChunk(data.audio);
                        break;
                        
                    case 'audio_stream_end':
                        console.log('Audio stream ended');
                        this.playNextInQueue();
                        break;

                    case 'interview_ended':
                        this.updateStatus(`Interview completed`, 'completed');
                        this.apiStatus.textContent = "Session ended";
                        this.showSummary(data.summary || "Interview completed successfully");
                        this.cleanup();
                        this.resetInterface();
                        break;
                        
                    case 'error':
                        this.updateStatus(`Error: ${data.message}`, 'idle');
                        this.apiStatus.textContent = "API Error";
                        console.error('Server error:', data.message);
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

            processAudioChunk(base64Audio) {
                try {
                    const audioData = atob(base64Audio);
                    const uint8Array = new Uint8Array(audioData.length);
                    
                    for (let i = 0; i < audioData.length; i++) {
                        uint8Array[i] = audioData.charCodeAt(i);
                    }
                    
                    const wavBuffer = this.createWAVFromPCM(uint8Array, 24000);
                    const blob = new Blob([wavBuffer], { type: 'audio/wav' });
                    this.audioQueue.push(blob);
                    
                    // Start playing if not already playing
                    this.playNextInQueue();

                } catch (error) {
                    console.error('Error processing audio chunk:', error);
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
                view.setUint16(20, 1, true);   // PCM format
                view.setUint16(22, 1, true);   // mono
                view.setUint32(24, sampleRate, true);
                view.setUint32(28, sampleRate * 2, true);  // byte rate
                view.setUint16(32, 2, true);   // block align
                view.setUint16(34, 16, true);  // bits per sample
                writeString(36, 'data');
                view.setUint32(40, length, true);
                
                const pcmInt16 = new Int16Array(length / 2);
                for (let i = 0; i < length; i += 2) {
                    pcmInt16[i/2] = (pcmInt16[i+1] << 8) | pcmData[i];
                }
                
                new Uint8Array(buffer, 44).set(new Uint8Array(pcmInt16.buffer));
                return buffer;
            }

            async playNextInQueue() {
                if (this.isPlaying || this.audioQueue.length === 0) return;
                
                this.isPlaying = true;
                const audioBlob = this.audioQueue.shift();
                
                try {
                    this.audioPlayer.src = URL.createObjectURL(audioBlob);
                    await this.audioPlayer.play();
                    
                    this.audioPlayer.onended = () => {
                        URL.revokeObjectURL(this.audioPlayer.src);
                        this.isPlaying = false;
                        this.playNextInQueue();
                    };
                } catch (error) {
                    console.error("Error playing audio:", error);
                    this.isPlaying = false;
                    this.playNextInQueue();
                }
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
            SimpleHTTPRequestHandler.do_GET(self)

async def handle_websocket(websocket, path):
    """Handle WebSocket connections"""
    print(f"New WebSocket connection from {websocket.remote_address}")
    
    try:
        handler = PureGeminiHandler(websocket, interviews_db, transcripts_db)
        await handler.start_interview_session()
        
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
    print("🌐 HTTP Server: http://localhost:8000")
    print("🔌 WebSocket Server: ws://localhost:8001")
    print("🎙️ Audio processing: Server-side (PyAudio)")
    print("🗣️ Speech recognition: Client-side (Web Speech API)")
    print()
    print("Make sure you have:")
    print("  - GEMINI_API_KEY environment variable set")
    print("  - All dependencies installed (pip install -r requirements.txt)")
    print("  - Microphone access enabled")
    print()
    
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
    