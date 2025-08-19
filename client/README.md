# AWS Transcribe Medical Direct API Client

This client provides a modern web interface for real-time medical transcription using AWS Transcribe Medical's Direct API (StartMedicalStreamTranscription).

## Features

- 🌐 **Web Interface**: Modern HTML5 interface with real-time visualization
- 🎤 **Real-time Transcription**: Stream audio and receive transcripts instantly
- 🏥 **Medical Entity Detection**: Automatic detection of medical terms, conditions, medications, etc.
- 🔌 **Multiple Protocols**: Support for WebSocket, HTTP/2, and unified endpoint
- 📊 **Visual Feedback**: Volume meter, connection status, and live transcripts
- 🔒 **Secure**: Uses AWS SigV4 authentication

## Usage

### Starting the Client

1. Start the web server:
   ```bash
   python client.py
   ```
   Or specify the port:
   ```bash
   python client.py --mode web --port 8080
   ```

2. Open your browser to: http://localhost:8080/client.html

3. Select a protocol from the dropdown:
   - **WebSocket**: Direct WebSocket connection to AWS Transcribe Medical
   - **HTTP/2**: HTTP/2 streaming protocol
   - **Unified**: Auto-selects the best available protocol

4. Click "Connect" to establish connection

5. Click "Start Recording" and allow microphone access

6. Speak - you'll see real-time transcription and medical entities

7. Click "Stop Recording" to end and save transcript to S3

## Configuration

The client connects to the backend running on `localhost:8000` by default. Make sure the backend is running before starting the client.

### Backend Endpoints

- `/ws/medical/direct` - WebSocket protocol endpoint
- `/ws/medical/http2` - HTTP/2 protocol endpoint  
- `/ws/medical/unified` - Unified endpoint with automatic protocol selection

## Requirements

For the web server:
- Python 3.7+
- No additional dependencies (uses built-in `http.server`)

For the browser:
- Modern browser with WebSocket support
- Microphone access permission

## Audio Configuration

The client captures audio with the following settings:
- Sample Rate: 16,000 Hz
- Channels: Mono (1 channel)
- Format: 16-bit PCM
- Chunk Size: 1024 samples

These settings are optimized for AWS Transcribe Medical requirements.

## Medical Entity Types

The client displays detected medical entities including:
- **Medications**: Drug names, dosages, routes
- **Medical Conditions**: Diagnoses, symptoms
- **Treatments**: Procedures, therapies
- **Anatomy**: Body parts, systems
- **PHI**: Protected Health Information (if configured)

## Troubleshooting

1. **Connection Failed**: Ensure the backend is running on port 8000
2. **No Audio**: Check browser microphone permissions
3. **No Transcription**: Verify AWS credentials in backend
4. **Poor Quality**: Ensure quiet environment and good microphone

## Files

- `client.html` - Main web interface
- `client.py` - Web server for hosting the client
- `requirements.txt` - Python dependencies (optional)
- `Dockerfile` - Container configuration