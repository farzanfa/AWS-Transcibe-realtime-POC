# Medical Transcription Architecture

## Overview

This implementation provides real-time medical transcription using AWS Transcribe Medical API with a clean client-server architecture:

```
Client (Browser) -> WebSocket -> Backend Server -> AWS API -> AWS Medical Transcription
                 <-            <-                <-         <-
```

## Components

### 1. Client (`client/medical_client.html`)
- Simple web interface
- Captures microphone audio
- Sends audio over WebSocket to backend
- Displays real-time transcriptions
- Shows detected medical entities

### 2. Backend Server (`backend/medical_transcription_server.py`)
- FastAPI server with WebSocket endpoint
- Manages connection to AWS Transcribe Medical
- Handles authentication (SigV4)
- Forwards audio from client to AWS
- Forwards transcriptions from AWS to client
- Saves sessions to S3

### 3. AWS Transcribe Medical API
- Processes audio in real-time
- Returns transcriptions with medical entities
- Identifies medications, conditions, anatomy, procedures
- Optional PHI detection

## Quick Start

### 1. Set up AWS credentials

```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=us-east-1
export S3_BUCKET=your-bucket-name
```

### 2. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 3. Start the backend server

```bash
python medical_transcription_server.py
```

### 4. Open the client

Open `client/medical_client.html` in a web browser.

### 5. Use the application

1. Click "Connect" to connect to the backend
2. Click "Start Recording" and allow microphone access
3. Speak - you'll see real-time transcription
4. Medical entities are automatically detected
5. Click "Stop Recording" to end session
6. Session is automatically saved to S3

## Data Flow

### Audio Flow (Client to AWS)
1. Browser captures microphone audio (16kHz, PCM)
2. Audio converted to 16-bit PCM format
3. Base64 encoded and sent via WebSocket
4. Backend decodes and forwards to AWS
5. AWS processes audio stream

### Transcription Flow (AWS to Client)
1. AWS sends transcript events
2. Backend receives and parses events
3. Extracts text and medical entities
4. Forwards to client via WebSocket
5. Client displays in real-time

## Message Protocol

### Client to Server
```json
{
  "type": "audio",
  "data": "base64_encoded_pcm_audio"
}
```

### Server to Client
```json
{
  "type": "transcript",
  "text": "Patient presents with hypertension",
  "is_partial": false,
  "entities": [
    {
      "text": "hypertension",
      "category": "MEDICAL_CONDITION",
      "confidence": 0.98
    }
  ]
}
```

## Features

- **Real-time transcription**: Low latency streaming
- **Medical entity detection**: Automatic identification of medical terms
- **Session management**: Each session has unique ID
- **S3 storage**: Automatic saving of completed sessions
- **Clean architecture**: Simple client-server separation
- **Error handling**: Graceful error recovery

## Security

- AWS IAM authentication for API access
- WebSocket for client-server communication
- Optional SSL/TLS for production
- PHI detection configurable
- No audio stored on server

## Requirements

- Python 3.7+
- Modern web browser with WebRTC
- AWS account with Transcribe Medical access
- S3 bucket for session storage

## Configuration

Backend environment variables:
- `AWS_REGION`: AWS region (default: us-east-1)
- `S3_BUCKET`: S3 bucket for sessions
- `LOG_LEVEL`: Logging level (default: INFO)

## Monitoring

The backend provides detailed logging:
- Connection events
- Session lifecycle
- AWS API interactions
- Error conditions

## Production Considerations

1. Add SSL/TLS certificates
2. Implement authentication
3. Add rate limiting
4. Configure CORS properly
5. Set up CloudWatch logging
6. Enable S3 encryption
7. Configure VPC endpoints