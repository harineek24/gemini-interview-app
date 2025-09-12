#!/usr/bin/env python3
"""
Debug script to test audio input
"""
import pyaudio
import time
from array import array

# Audio constants
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 16000
MIN_AUDIO_LEVEL = 100

def test_microphone():
    pa = pyaudio.PyAudio()
    
    print("🎤 Testing microphone input...")
    print(f"Available audio devices:")
    
    # List all audio devices
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            print(f"  {i}: {info['name']} (inputs: {info['maxInputChannels']})")
    
    try:
        # Get default microphone
        mic_info = pa.get_default_input_device_info()
        print(f"\nUsing default microphone: {mic_info['name']}")
        
        # Open audio stream
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE
        )
        
        print("🔴 Recording... Speak now! (10 seconds)")
        print("📊 Audio levels:")
        
        for i in range(100):  # 10 seconds of recording
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            
            # Convert to integers and check amplitude
            int_data = array('h', data)
            amplitude = sum(abs(x) for x in int_data) / len(int_data)
            
            # Show audio level indicator
            level_bars = int(amplitude / 100)
            bars = "█" * min(level_bars, 20)
            
            if amplitude > MIN_AUDIO_LEVEL:
                print(f"\r🎵 SPEECH DETECTED: {amplitude:4.0f} {bars}                    ", end="")
            else:
                print(f"\r🔇 Silence: {amplitude:4.0f} {bars}                    ", end="")
            
            time.sleep(0.1)
        
        print("\n✅ Audio test completed!")
        stream.stop_stream()
        stream.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        pa.terminate()

if __name__ == "__main__":
    test_microphone()