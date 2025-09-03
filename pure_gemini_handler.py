"""
Fixed Gemini Live Handler with smooth audio and continuous responses
"""

import asyncio
import json
import base64
import traceback
import os
import uuid
import time
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from google import genai
import google.generativeai as genai_summary
from google.genai import types

# Load environment variables from .env file
load_dotenv()

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

class FixedGeminiLiveHandler:
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
        self.conversation_inactive_threshold = 30
        self.last_activity = None
        self.end_conversation_task = None
        
        # FIXES: Audio streaming improvements
        self.audio_buffer = asyncio.Queue()
        self.last_audio_time = None
        self.silence_threshold = 1.0  # seconds
        self.is_session_active = True
        self.pending_audio_chunks = []
        self.audio_chunk_timeout = 0.1  # 100ms buffer before sending
        
    async def start_interview(self):
        """Initialize the interview and start the Gemini Live session"""
        # Create new interview record
        self.interview = SimpleInterview()
        self.interviews_db[self.interview.id] = self.interview
        
        await self.websocket.send(json.dumps({
            'type': 'interview_started',
            'interview_id': str(self.interview.id)
        }))
        
        # FIXED: Better config for continuous conversation
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction="""You are a professional AI interviewer conducting a live interview. 
            Keep the conversation flowing naturally with follow-up questions. 
            Speak clearly and naturally. Don't end the interview unless explicitly asked.
            If there's silence, occasionally prompt the interviewee to continue or ask clarifying questions.""",
            # FIXED: Enable input transcription for better handling
            input_audio_transcription={}
        )
        
        # Start Gemini Live session
        try:
            async with self.client.aio.live.connect(model=self.model, config=config) as session:
                self.session = session
                self.last_activity = datetime.now()
                self.is_session_active = True
                
                print("✅ Gemini Live session started successfully")
                
                # FIXED: Better task management
                tasks = [
                    asyncio.create_task(self.handle_gemini_responses()),
                    asyncio.create_task(self.handle_client_messages()),
                    asyncio.create_task(self.monitor_inactivity()),
                    asyncio.create_task(self.audio_sender()),  # NEW: Dedicated audio sender
                    asyncio.create_task(self.audio_streamer())  # NEW: Smooth audio streaming
                ]
                
                try:
                    await asyncio.gather(*tasks)
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
                    
                data = json.loads(message)
                
                if data['type'] == 'audio_chunk':
                    # FIXED: Better audio handling
                    try:
                        audio_data = base64.b64decode(data['audio'])
                        if len(audio_data) > 0:
                            # Add to pending chunks for batching
                            self.pending_audio_chunks.append(audio_data)
                            self.last_audio_time = time.time()
                            self.last_activity = datetime.now()
                    except Exception as e:
                        print(f"Error processing audio chunk: {e}")
                    
                elif data['type'] == 'stop_interview':
                    print("🛑 User requested interview stop")
                    self.is_session_active = False
                    await self.end_interview()
                    break
                    
                elif data['type'] == 'text_input':
                    # Handle text input
                    await self.session.send_realtime_input(text=data['text'])
                    await self.save_live_transcript('user', data['text'])
                    self.last_activity = datetime.now()
                    
        except Exception as e:
            print(f"Error handling client messages: {e}")
            self.is_session_active = False
    
    async def audio_sender(self):
        """NEW: Dedicated audio sender with batching to reduce choppiness"""
        while self.is_session_active:
            try:
                # Check if we have pending audio and enough time has passed
                if (self.pending_audio_chunks and self.last_audio_time and 
                    time.time() - self.last_audio_time > self.audio_chunk_timeout):
                    
                    # Combine multiple small chunks to reduce overhead
                    combined_audio = b''.join(self.pending_audio_chunks)
                    self.pending_audio_chunks.clear()
                    
                    if len(combined_audio) > 0:
                        await self.send_audio_to_gemini(combined_audio)
                
                # Check for silence and send stream end
                elif (self.last_audio_time and 
                      time.time() - self.last_audio_time > self.silence_threshold):
                    await self.send_audio_stream_end()
                    self.last_audio_time = None  # Reset to avoid repeated stream ends
                
                await asyncio.sleep(0.05)  # 50ms check interval
                
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
                    
                    # FIXED: Handle input transcription (user speech recognition)
                    if hasattr(response.server_content, 'input_transcription') and response.server_content.input_transcription:
                        user_text = response.server_content.input_transcription.text
                        if user_text:
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
                        # Clear audio buffer on interruption
                        while not self.audio_buffer.empty():
                            try:
                                self.audio_buffer.get_nowait()
                            except:
                                break
                    
                    # Handle model turn with audio/text content
                    if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                        for part in response.server_content.model_turn.parts:
                            
                            # Handle text responses (for transcription)
                            if hasattr(part, 'text') and part.text:
                                await self.save_live_transcript('ai', part.text)
                                await self.websocket.send(json.dumps({
                                    'type': 'live_transcript',
                                    'speaker': 'ai',
                                    'text': part.text
                                }))
                            
                            # FIXED: Handle audio responses with buffering
                            if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                                # Queue audio for smooth streaming
                                await self.audio_buffer.put({
                                    'data': part.inline_data.data,
                                    'mime_type': getattr(part.inline_data, 'mime_type', 'audio/pcm')
                                })
                    
                    # Handle turn complete
                    if hasattr(response.server_content, 'turn_complete') and response.server_content.turn_complete:
                        print("✅ AI response complete")
                        # Signal end of audio stream for this turn
                        await self.audio_buffer.put({'end_turn': True})
                    
                    self.last_activity = datetime.now()
                    
        except Exception as e:
            print(f"Error handling Gemini responses: {e}")
            traceback.print_exc()
    
    async def audio_streamer(self):
        """NEW: Stream audio smoothly to client with proper buffering"""
        current_stream_id = None
        
        while self.is_session_active:
            try:
                # Wait for audio data with timeout
                audio_item = await asyncio.wait_for(self.audio_buffer.get(), timeout=0.1)
                
                if audio_item.get('end_turn'):
                    # Send end of turn signal
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
                        'sample_rate': 24000,  # Gemini native audio outputs at 24kHz
                        'format': 'pcm_16'
                    }))
                
                # Send audio chunk with stream ID
                audio_b64 = base64.b64encode(audio_item['data']).decode()
                await self.websocket.send(json.dumps({
                    'type': 'audio_chunk_response',
                    'stream_id': current_stream_id,
                    'audio': audio_b64,
                    'mime_type': audio_item.get('mime_type', 'audio/pcm')
                }))
                
            except asyncio.TimeoutError:
                continue  # No audio to stream, continue waiting
            except Exception as e:
                print(f"Error in audio streamer: {e}")
                await asyncio.sleep(0.1)
    
    async def send_audio_to_gemini(self, audio_data: bytes):
        """Send audio data to Gemini Live API"""
        try:
            # FIXED: Less restrictive validation
            if len(audio_data) == 0:
                return
                
            # Don't reject valid audio chunks - just log if unusually large
            if len(audio_data) > 65536:  # 64KB warning threshold
                print(f"Warning: Large audio chunk ({len(audio_data)} bytes)")
            
            # Send audio using the working method
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000"
                )
            )
            
        except Exception as e:
            print(f"Error sending audio to Gemini: {e}")
            print(f"Audio data length: {len(audio_data) if audio_data else 'None'}")
            traceback.print_exc()

    async def send_audio_stream_end(self):
        """Send audio stream end to flush cached audio"""
        try:
            await self.session.send_realtime_input(audio_stream_end=True)
            print("📤 Sent audio stream end")
        except Exception as e:
            print(f"Error sending audio stream end: {e}")

    async def save_live_transcript(self, speaker: str, text: str):
        """Save live transcript to simple storage"""
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
                print(f"💾 Saved transcript: {speaker} -> {text[:50]}...")
        except Exception as e:
            print(f"Error saving transcript: {e}")
    
    async def monitor_inactivity(self):
        """Monitor for conversation inactivity to auto-end"""
        while self.is_session_active:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                if self.last_activity:
                    inactive_seconds = (datetime.now() - self.last_activity).total_seconds()
                    
                    if inactive_seconds > self.conversation_inactive_threshold:
                        print(f"⏰ Conversation inactive for {inactive_seconds:.1f}s, ending interview")
                        self.is_session_active = False
                        await self.end_interview()
                        break
                        
            except Exception as e:
                print(f"Error in inactivity monitor: {e}")
                await asyncio.sleep(1)

    async def end_interview(self):
        """End the interview and generate summary"""
        if self.interview and self.interview.status == 'active':
            try:
                print(f"🏁 Ending interview {self.interview.id}")
                
                # Update interview status
                self.interview.status = 'completed'
                self.interview.ended_at = datetime.now()
                
                # Calculate duration
                duration = self.interview.ended_at - self.interview.started_at
                self.interview.duration_seconds = int(duration.total_seconds())
                
                # Generate full transcript
                interview_transcripts = [t for t in self.transcripts_db.values() 
                                       if t.interview_id == self.interview.id]
                interview_transcripts.sort(key=lambda x: x.sequence_number)
                
                full_transcript = '\n'.join([f"{t.speaker.upper()}: {t.text}" 
                                           for t in interview_transcripts])
                self.interview.full_transcript = full_transcript
                
                # Generate summary using Gemini (non-live API)
                print("📝 Generating interview summary...")
                summary = await self.generate_summary(full_transcript)
                self.interview.summary = summary
                
                # Send final audio stream end
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
                
                print(f"✅ Interview {self.interview.id} completed successfully")
                print(f"   Duration: {self.interview.duration_formatted}")
                print(f"   Transcripts: {len(interview_transcripts)}")
                
            except Exception as e:
                print(f"❌ Error ending interview: {e}")
                traceback.print_exc()
                if self.interview:
                    self.interview.status = 'failed'
                
                # Still notify client of end
                try:
                    await self.websocket.send(json.dumps({
                        'type': 'interview_ended',
                        'interview_id': str(self.interview.id) if self.interview else 'unknown',
                        'duration': '0:00',
                        'summary': 'Interview ended with errors.',
                        'error': str(e)
                    }))
                except:
                    pass
    
    async def generate_summary(self, transcript: str) -> str:
        """Generate interview summary using Gemini"""
        try:
            if not transcript.strip():
                return "No conversation content to summarize."
            
            # Use regular Gemini API for summary generation
            api_key = os.environ.get("GEMINI_API_KEY")
            genai_summary.configure(api_key=api_key)
            model = genai_summary.GenerativeModel('gemini-1.5-flash')
            
            prompt = f"""
            Please provide a concise and professional summary of this interview conversation.
            
            Focus on:
            - Key topics and themes discussed
            - Main insights or information shared
            - Overall tone and flow of the conversation
            - Any notable highlights or important points
            
            Keep the summary under 200 words and make it useful for someone who wasn't present.
            
            Interview Transcript:
            {transcript}
            """
            
            response = await asyncio.to_thread(model.generate_content, prompt)
            summary_text = response.text.strip()
            
            if summary_text:
                print(f"✅ Generated summary ({len(summary_text)} chars)")
                return summary_text
            else:
                return "Summary could not be generated from the conversation."
            
        except Exception as e:
            print(f"❌ Error generating summary: {e}")
            return f"Summary generation failed due to an error: {str(e)}"

    async def cleanup(self):
        """Clean up resources"""
        try:
            self.is_session_active = False
            
            # Clear audio buffer
            while not self.audio_buffer.empty():
                try:
                    self.audio_buffer.get_nowait()
                except:
                    break
            
            # Clear pending audio chunks
            self.pending_audio_chunks.clear()
            
            print("🧹 Cleanup completed")
            
        except Exception as e:
            print(f"Error during cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup"""
        try:
            if hasattr(self, 'is_session_active'):
                self.is_session_active = False
        except:
            pass