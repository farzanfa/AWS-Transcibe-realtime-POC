# Medical Transcription Client

This unified client supports both web-based and Python-based real-time medical transcription using AWS Transcribe Medical.

## Features

- 🌐 **Web Client**: Modern HTML5 interface with real-time visualization
- 🐍 **Python Client**: Command-line interface for programmatic access
- 🎤 **Real-time Transcription**: Stream audio and receive transcripts instantly
- 🏥 **Medical Entity Detection**: Automatic detection of medical terms, conditions, medications, etc.
- 🔌 **Multiple Endpoints**: Support for regular transcription, medical SDK, WebSocket, and HTTP/2
- 📊 **Visual Feedback**: Volume meter, connection status, and live transcripts

## Usage

### Web Client Mode (Default)

1. Start the web server:
   ```bash
   python client.py
   ```
   Or specify the port:
   ```bash
   python client.py --mode web --port 8080
   ```

2. Open your browser to: http://localhost:8080/client.html

3. Select an endpoint from the dropdown:
   - **Regular Transcription**: Standard AWS Transcribe
   - **Medical Transcription (SDK)**: AWS Transcribe Medical via SDK
   - **Medical Direct (WebSocket)**: Direct WebSocket API for medical transcription
   - **Medical Direct (HTTP/2)**: HTTP/2 protocol for medical transcription
   - **Medical Unified**: Auto-selects the best available protocol

4. Click "Connect" to establish WebSocket connection

5. Click "Start Recording" and allow microphone access

6. Speak - you'll see real-time transcription and medical entities

7. Click "Stop Recording" to end and save transcript to S3

### Python Client Mode

1. Install required dependencies:
   ```bash
   pip install pyaudio websockets numpy
   ```

2. Run the Python client:
   ```bash
   python client.py --mode python
   ```
   Or specify a custom WebSocket URL:
   ```bash
   python client.py --mode python --url ws://localhost:8000/ws/medical
   ```

3. Use commands:
   - `start` - Begin recording and streaming
   - `stop` - Stop recording
   - `quit` - Exit the client

## File Structure

- `client.py` - Unified client supporting both web server and Python streaming modes
- `client.html` - Modern web interface for browser-based transcription
- `requirements.txt` - Python dependencies (optional for Python client mode)
- `Dockerfile` - Container configuration for deployment

## Requirements

### For Web Mode (Default)
- Python 3.7+ (standard library only)
- Modern web browser with WebRTC support

### For Python Client Mode
- Python 3.7+
- pyaudio (for microphone access)
- websockets (for WebSocket communication)
- numpy (for audio processing)

## Configuration

The client automatically connects to `localhost:8000` by default. To connect to a different server:

- **Web Mode**: Edit the WebSocket URLs in the endpoint dropdown in the HTML
- **Python Mode**: Use the `--url` parameter

## Medical Entity Categories

The client can detect and highlight the following medical entities:

- 💊 **Medications**: Drug names, dosages
- 🏥 **Medical Conditions**: Diseases, symptoms, diagnoses
- 🫀 **Anatomy**: Body parts, organs, systems
- 🔬 **Tests & Procedures**: Lab tests, medical procedures, treatments
- 🔒 **PHI**: Protected Health Information (when configured)

## Docker Deployment

Build and run with Docker:
```bash
docker build -t medical-transcription-client .
docker run -p 8080:8080 medical-transcription-client
```

## Notes

- The web client uses modern JavaScript features - ensure your browser is up to date
- Microphone permissions are required for audio capture
- For production use, ensure proper SSL/TLS configuration
- The client automatically handles reconnection on connection loss