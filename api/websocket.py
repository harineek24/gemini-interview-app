import asyncio
import json
import base64
from datetime import datetime
from typing import Optional
from fastapi import WebSocket
from google import genai
import google.generativeai as genai_summary
from google.genai import types
from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async
from interviews.models import Interview, LiveTranscript

class GeminiLiveHandler:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.interview: Optional[Interview] = None
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = settings.GEMINI_MODEL
        self.transcript_counter = 0
        self.session = None
        self.conversation_inactive_threshold = 10  # seconds of inactivity to detect end
        self.last_activity = None
        self.end_conversation_task = None
        
    async def start_interview(self):
        """Initialize the interview and start the Gemini Live session"""
        # Create new interview record
        self.interview = await sync_to_async(Interview.objects.create)()
        
        await self.websocket.send_text(json.dumps({
            'type': 'interview_started',
            'interview_id': str(self.interview.id)
        }))
        
        # Configure Gemini Live session for voice-to-voice interview
        config = {
            "response_modalities": ["AUDIO"],
            "output_audio_transcription": {},
            "system_instruction": {
                "parts": [{
                    "text": """You are conducting a professional voice interview. 
                    Be conversational, ask follow-up questions, and engage naturally.
                    Keep responses concise and natural for voice interaction.
                    When you sense the conversation is naturally ending (like 'thank you', 'that's all', 'goodbye'), 
                    provide a brief closing statement and indicate the interview is complete."""
                }]
            }
        }
        
        # Start Gemini Live session
        async with self.client.aio.live.connect(model=self.model, config=config) as session:
            self.session = session
            self.last_activity = datetime.now()
            
            # Start tasks for handling responses and monitoring inactivity
            response_task = asyncio.create_task(self.handle_gemini_responses())
            client_task = asyncio.create_task(self.handle_client_messages())
            inactivity_task = asyncio.create_task(self.monitor_inactivity())
            
            # Wait for either task to complete
            try:
                await asyncio.gather(response_task, client_task, inactivity_task)
            except Exception as e:
                print(f"Error in session: {e}")
            finally:
                await self.end_interview()
    
    async def handle_client_messages(self):
        """Handle messages from the client (user audio/commands)"""
        try:
            while True:
                message = await self.websocket.receive_text()
                data = json.loads(message)
                
                if data['type'] == 'audio_chunk':
                    # Handle audio data from client
                    audio_data = base64.b64decode(data['audio'])
                    await self.send_audio_to_gemini(audio_data)
                    self.last_activity = datetime.now()
                    
                elif data['type'] == 'stop_interview':
                    # User manually stopped the interview
                    await self.end_interview()
                    break
                    
                elif data['type'] == 'text_input':
                    # Handle text input for testing/fallback
                    await self.session.send_client_content(
                        turns={"parts": [{"text": data['text']}]}
                    )
                    
                    # Save user transcript
                    await self.save_live_transcript('user', data['text'])
                    self.last_activity = datetime.now()
                    
        except Exception as e:
            print(f"Error handling client messages: {e}")
    
    async def handle_gemini_responses(self):
        """Handle responses from Gemini Live API"""
        try:
            async for chunk in self.session.receive():
                if chunk.server_content:
                    # Handle text responses
                    if chunk.text:
                        await self.save_live_transcript('ai', chunk.text)
                        
                        # Send live transcript to client
                        await self.websocket.send_text(json.dumps({
                            'type': 'live_transcript',
                            'speaker': 'ai',
                            'text': chunk.text
                        }))
                        
                        # Check if conversation is ending
                        if await self.is_conversation_ending(chunk.text):
                            await asyncio.sleep(2)  # Brief pause
                            await self.end_interview()
                            break
                    
                    # Handle audio responses
                    if hasattr(chunk.server_content, 'model_turn') and chunk.server_content.model_turn:
                        for part in chunk.server_content.model_turn.parts:
                            if hasattr(part, 'inline_data') and part.inline_data:
                                # Send audio back to client
                                audio_b64 = base64.b64encode(part.inline_data.data).decode()
                                await self.websocket.send_text(json.dumps({
                                    'type': 'audio_response',
                                    'audio': audio_b64,
                                    'mime_type': part.inline_data.mime_type
                                }))
                    
                    self.last_activity = datetime.now()
                    
        except Exception as e:
            print(f"Error handling Gemini responses: {e}")
    
    async def send_audio_to_gemini(self, audio_data: bytes):
        """Send audio data to Gemini Live API"""
        try:
            # Send audio to Gemini
            await self.session.send_client_content(
                turns={"parts": [{"inline_data": {
                    "mime_type": "audio/pcm",
                    "data": audio_data
                }}]}
            )
        except Exception as e:
            print(f"Error sending audio to Gemini: {e}")
    
    async def save_live_transcript(self, speaker: str, text: str):
        """Save live transcript to database"""
        try:
            await sync_to_async(LiveTranscript.objects.create)(
                interview=self.interview,
                speaker=speaker,
                text=text,
                sequence_number=self.transcript_counter
            )
            self.transcript_counter += 1
        except Exception as e:
            print(f"Error saving transcript: {e}")
    
    async def is_conversation_ending(self, text: str) -> bool:
        """Detect if the conversation is naturally ending"""
        ending_phrases = [
            'thank you for your time',
            'that concludes our interview',
            'thank you very much',
            'have a great day',
            'goodbye',
            'that\'s all for today',
            'interview complete',
            'we\'re done here',
            'that\'s everything'
        ]
        
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in ending_phrases)
    
    async def monitor_inactivity(self):
        """Monitor for conversation inactivity to auto-end"""
        while True:
            await asyncio.sleep(1)
            
            if self.last_activity:
                inactive_seconds = (datetime.now() - self.last_activity).total_seconds()
                
                if inactive_seconds > self.conversation_inactive_threshold:
                    print("Conversation inactive, ending interview")
                    await self.end_interview()
                    break
    
    async def end_interview(self):
        """End the interview and generate summary"""
        if self.interview and self.interview.status == 'active':
            try:
                # Update interview status
                self.interview.status = 'completed'
                self.interview.ended_at = timezone.now()
                
                # Calculate duration
                duration = self.interview.ended_at - self.interview.started_at
                self.interview.duration_seconds = int(duration.total_seconds())
                
                # Generate full transcript
                transcripts = await sync_to_async(list)(LiveTranscript.objects.filter(interview=self.interview).order_by('sequence_number'))
                full_transcript = '\n'.join([f"{t.speaker.upper()}: {t.text}" for t in transcripts])
                self.interview.full_transcript = full_transcript
                
                # Generate summary using Gemini (non-live API)
                summary = await self.generate_summary(full_transcript)
                self.interview.summary = summary
                
                await sync_to_async(self.interview.save)()
                
                # Notify client
                await self.websocket.send_text(json.dumps({
                    'type': 'interview_ended',
                    'interview_id': str(self.interview.id),
                    'duration': self.interview.duration_formatted,
                    'summary': summary
                }))
                
                print(f"Interview {self.interview.id} completed")
                
            except Exception as e:
                print(f"Error ending interview: {e}")
                if self.interview:
                    self.interview.status = 'failed'
                    await sync_to_async(self.interview.save)()
    
    async def generate_summary(self, transcript: str) -> str:
        """Generate interview summary using Gemini"""
        try:
            # Use regular Gemini API for summary generation
            genai_summary.configure(api_key=settings.GEMINI_API_KEY)
            model = genai_summary.GenerativeModel('gemini-1.5-flash')
            
            prompt = f"""
            Please provide a concise summary of this interview conversation.
            Focus on key topics discussed, main insights, and overall tone.
            Keep it under 200 words.
            
            Interview Transcript:
            {transcript}
            """
            
            response = await sync_to_async(model.generate_content)(prompt)
            return response.text
            
        except Exception as e:
            print(f"Error generating summary: {e}")
            return "Summary generation failed."
