# Gemini Live Interview App

A real-time voice interview application powered by Google's Gemini Live API. This app allows you to have natural conversations with an AI interviewer using voice input and output.

## Features

- 🎤 **Real-time voice input** - Speak naturally to the AI
- 🎵 **AI voice responses** - Hear the AI's responses in natural speech
- 💬 **Live transcription** - See real-time text of the conversation
- 🔄 **Continuous conversation** - Keep talking back and forth naturally
- 🌐 **Web-based interface** - No installation needed, works in your browser

## Quick Start

1. **Set up your API key:**
   ```bash
   # Create a .env file in the project root
   echo "GEMINI_API_KEY=your_api_key_here" > .env
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application:**
   ```bash
   python pure_websocket_server.py
   ```

4. **Open your browser:**
   - Go to `http://localhost:8000`
   - Click "Start Interview"
   - Allow microphone access when prompted
   - Start talking!

## How It Works

- **Frontend**: Pure HTML/JavaScript with WebSocket connection
- **Backend**: Pure Python WebSocket server (no Django/FastAPI)
- **AI**: Google Gemini Live API with native audio support
- **Audio**: Real-time PCM audio streaming with browser-compatible playback

## Files

- `pure_websocket_server.py` - Main server with embedded HTML interface
- `pure_gemini_handler.py` - Handles Gemini Live API communication
- `test_live_api.py` - Standalone test script for Gemini Live API
- `requirements.txt` - Python dependencies

## Requirements

- Python 3.8+
- Google Gemini API key
- Modern web browser with WebSocket support
- Microphone access

## Troubleshooting

- **No audio**: Check browser permissions and try refreshing
- **Connection issues**: Ensure port 8000 is available
- **API errors**: Verify your Gemini API key is correct and has quota

## License

MIT License
