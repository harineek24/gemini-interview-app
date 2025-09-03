import os
import asyncio
import pyaudio
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

# --- Configuration ---
load_dotenv()
API_KEY = os.environ.get("GEMINI_API_KEY")

# Native Audio Models (choose one):
# Best quality with emotion awareness and proactive audio
NATIVE_MODEL = "gemini-2.5-flash-preview-native-audio-dialog"

# Alternative native models you can try:
# NATIVE_MODEL = "gemini-2.0-flash-live-preview"  # Basic live model
# NATIVE_MODEL = "gemini-live-2.5-flash-preview"  # Half-cascade model

FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_RATE = 16000   # Input to Gemini must be 16kHz
OUTPUT_RATE = 24000  # Gemini native audio outputs at 24kHz
CHUNK_SIZE = 512

async def native_audio_conversation():
    """
    Native audio conversation with advanced features:
    - Emotion-aware responses (affective dialogue)
    - Proactive audio (AI decides when to respond)
    - High-quality natural speech
    - Interruptible responses
    """
    print("--- Starting Native Audio Conversation ---")
    print(f"Using model: {NATIVE_MODEL}")
    
    # Native audio configuration with advanced features
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="You are a helpful AI assistant with natural conversation abilities. "
                          "Respond with appropriate emotion and tone based on the user's voice. "
                          "Keep responses conversational and engaging.",
        # Optional: Configure voice settings
        # speech_config=types.SpeechConfig(
        #     voice_config=types.VoiceConfig(
        #         prebuilt_voice_config=types.PrebuiltVoiceConfig(
        #             voice_name="Aoede"  # Options: Aoede, Charon, Fenrir, Kore, Puck
        #         )
        #     )
        # )
    )

    pya = pyaudio.PyAudio()
    
    # Input stream for microphone
    mic_info = pya.get_default_input_device_info()
    print(f"Using microphone: {mic_info['name']}")
    
    input_stream = pya.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=INPUT_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    
    # Output stream for AI speech (24kHz for native audio)
    output_stream = pya.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=OUTPUT_RATE,
        output=True,
        frames_per_buffer=CHUNK_SIZE * 3,  # Larger buffer for smoother playback
    )

    print("Setting up audio buffers...")
    audio_buffer = asyncio.Queue()
    is_speaking = False
    conversation_active = True

    try:
        print("Connecting to Gemini Native Audio API...")
        async with client.aio.live.connect(model=NATIVE_MODEL, config=config) as session:
            print("🎤 Connection successful! Start speaking...")
            print("💡 The AI will respond naturally with emotion and context awareness")
            print("⏹️  Say 'goodbye' or 'stop' to end the conversation\n")
            
            async def capture_and_send_audio():
                """Capture microphone input and stream to Gemini"""
                nonlocal conversation_active
                silence_start = None
                
                while conversation_active:
                    try:
                        data = await asyncio.to_thread(
                            input_stream.read, 
                            CHUNK_SIZE, 
                            exception_on_overflow=False
                        )
                        
                        if len(data) < CHUNK_SIZE // 2:  # Skip very small chunks
                            await asyncio.sleep(0.001)
                            continue
                        
                        # Detect silence for better streaming
                        volume = max(data) if data else 0
                        if volume < 1000:  # Silence threshold
                            if silence_start is None:
                                silence_start = time.time()
                            elif time.time() - silence_start > 1.0:  # 1 second of silence
                                # Send audio stream end to flush cached audio
                                try:
                                    await session.send_realtime_input(audio_stream_end=True)
                                    silence_start = None
                                except:
                                    pass
                        else:
                            silence_start = None
                        
                        # Send audio chunk to Gemini
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=data,
                                mime_type="audio/pcm;rate=16000"
                            )
                        )
                        
                        await asyncio.sleep(0.005)  # Small delay to prevent overwhelming
                        
                    except Exception as e:
                        print(f"Audio capture error: {e}")
                        await asyncio.sleep(0.1)

            async def handle_responses():
                """Handle AI responses and play audio"""
                nonlocal is_speaking, conversation_active
                
                async for response in session.receive():
                    try:
                        # Handle interruptions
                        if (hasattr(response, 'server_content') and 
                            response.server_content and 
                            hasattr(response.server_content, 'interrupted') and
                            response.server_content.interrupted):
                            print("🤚 AI was interrupted")
                            # Clear audio buffer when interrupted
                            while not audio_buffer.empty():
                                try:
                                    audio_buffer.get_nowait()
                                except:
                                    break
                            is_speaking = False
                            continue

                        # Handle model turn with audio content
                        if (hasattr(response, 'server_content') and 
                            response.server_content and
                            hasattr(response.server_content, 'model_turn') and
                            response.server_content.model_turn):
                            
                            for part in response.server_content.model_turn.parts:
                                if (hasattr(part, 'inline_data') and 
                                    part.inline_data and 
                                    hasattr(part, 'inline_data')):
                                    
                                    # Queue audio for playback
                                    audio_data = part.inline_data.data
                                    if audio_data:
                                        await audio_buffer.put(audio_data)
                                        if not is_speaking:
                                            print("🤖 AI is responding...")
                                        is_speaking = True

                        # Handle turn complete
                        if (hasattr(response, 'server_content') and
                            response.server_content and
                            hasattr(response.server_content, 'turn_complete') and
                            response.server_content.turn_complete):
                            print("✅ Response complete\n")
                            is_speaking = False

                    except Exception as e:
                        print(f"Response handling error: {e}")

            async def play_audio():
                """Play queued audio responses"""
                while conversation_active:
                    try:
                        # Get audio from buffer with timeout
                        audio_data = await asyncio.wait_for(audio_buffer.get(), timeout=0.1)
                        
                        if audio_data:
                            # Play the audio chunk
                            await asyncio.to_thread(output_stream.write, audio_data)
                            
                    except asyncio.TimeoutError:
                        continue  # No audio to play, continue loop
                    except Exception as e:
                        print(f"Audio playback error: {e}")
                        await asyncio.sleep(0.1)

            # Start all tasks concurrently
            tasks = [
                asyncio.create_task(capture_and_send_audio()),
                asyncio.create_task(handle_responses()),
                asyncio.create_task(play_audio())
            ]
            
            # Run conversation for specified duration or until stopped
            print("🚀 Conversation started! Try saying 'Hello, how are you?'\n")
            
            # Run for 5 minutes or until conversation ends
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=300)
            except asyncio.TimeoutError:
                print("⏰ Conversation timed out after 5 minutes")
            except KeyboardInterrupt:
                print("\n🛑 Conversation interrupted by user")
            
            conversation_active = False
            
            # Cancel all tasks
            for task in tasks:
                task.cancel()
            
            # Send final audio stream end
            try:
                await session.send_realtime_input(audio_stream_end=True)
            except:
                pass
                
    except Exception as e:
        print(f"\n❌ Connection error: {e}")
        print(f"Error type: {type(e).__name__}")
        
        # Common fixes for different errors:
        if "1007" in str(e):
            print("\n🔧 Possible fixes for 1007 error:")
            print("1. Check if your region supports the Live API")
            print("2. Verify your API key has Live API access")
            print("3. Try a different model name")
            print("4. Check your internet connection")
        
    finally:
        print("\n🔄 Cleaning up...")
        conversation_active = False
        
        if input_stream.is_active():
            input_stream.stop_stream()
        if output_stream.is_active():
            output_stream.stop_stream()
            
        input_stream.close()
        output_stream.close()
        pya.terminate()
        
        print("✅ Cleanup complete")


async def test_native_audio_simple():
    """
    Simpler version for testing native audio with text output
    """
    print("--- Testing Native Audio (Text Output) ---")
    
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],  # Get text to verify it's working
        system_instruction="You are a helpful assistant. Respond naturally and conversationally."
    )

    pya = pyaudio.PyAudio()
    input_stream = pya.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=INPUT_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE,
    )

    try:
        async with client.aio.live.connect(model=NATIVE_MODEL, config=config) as session:
            print("✅ Connected! Speak for 10 seconds...")
            
            async def send_audio():
                start_time = time.time()
                while time.time() - start_time < 10:  # 10 second test
                    data = await asyncio.to_thread(input_stream.read, CHUNK_SIZE, exception_on_overflow=False)
                    if len(data) >= 64:
                        await session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                        )
                    await asyncio.sleep(0.01)
                
                # Send stream end
                await session.send_realtime_input(audio_stream_end=True)

            async def receive_text():
                async for response in session.receive():
                    if hasattr(response, 'server_content') and response.server_content:
                        if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                            for part in response.server_content.model_turn.parts:
                                if hasattr(part, 'text') and part.text:
                                    print(f"🤖 AI: {part.text}")

            await asyncio.gather(send_audio(), receive_text())
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
    finally:
        input_stream.close()
        pya.terminate()


if __name__ == "__main__":
    if not API_KEY:
        raise ValueError("❌ GEMINI_API_KEY not found. Please create a .env file and add your key.")
    
    # Use v1alpha for Live API
    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1alpha"})
    
    print("🎯 Choose version:")
    print("1. Full Native Audio Conversation (recommended)")
    print("2. Simple Test (text output)")
    
    try:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "2":
            asyncio.run(test_native_audio_simple())
        else:
            asyncio.run(native_audio_conversation())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ Failed to start: {e}")