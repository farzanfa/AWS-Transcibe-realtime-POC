# Real-Time Medical Transcription Streaming

This guide demonstrates how to use AWS Transcribe Medical's `StartMedicalStreamTranscription` API for real-time audio streaming and transcription from microphones or other audio sources.

## Overview

The real-time medical streaming implementation provides:
- WebSocket-based bidirectional communication
- Real-time audio streaming from microphones or files
- Live transcription with medical entity recognition
- Speaker identification for conversations
- Protected Health Information (PHI) detection
- Support for multiple medical specialties

## Architecture

```
┌─────────────┐     WebSocket      ┌─────────────┐      AWS API       ┌──────────────────┐
│   Client    │ ◄─────────────────► │   Backend   │ ◄────────────────► │ AWS Transcribe   │
│ (Browser/   │    Audio Stream     │  (FastAPI)  │   Medical Stream   │    Medical       │
│  Python)    │    + Transcripts    │             │                    │                  │
└─────────────┘                     └─────────────┘                    └──────────────────┘
```

## Backend Implementation

### Medical Streaming Module (`medical_streaming.py`)

The backend provides a dedicated WebSocket endpoint for medical transcription:

```python
@app.websocket("/ws/medical")
async def medical_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time medical transcription."""
    await handle_medical_websocket(websocket)
```

Key features:
- Uses `StartMedicalStreamTranscription` API
- Handles multiple medical specialties
- Processes audio in real-time
- Extracts medical entities
- Supports speaker identification

### Configuration Options

```python
config = {
    'specialty': 'PRIMARYCARE',  # Medical specialty
    'type': 'CONVERSATION',      # or 'DICTATION'
    'language_code': 'en-US',
    'sample_rate': 16000,
    'show_speaker_labels': True,
    'content_identification_type': 'PHI'  # or 'NONE'
}
```

### Medical Specialties

- `PRIMARYCARE` - General medical conversations
- `CARDIOLOGY` - Heart-related terminology
- `NEUROLOGY` - Neurological terms
- `ONCOLOGY` - Cancer-related vocabulary
- `RADIOLOGY` - Imaging and radiology terms
- `UROLOGY` - Urological terminology

## Client Implementation

### Web Client (HTML/JavaScript)

The web client (`medical_streaming_client.html`) provides:
- Real-time audio capture from microphone
- WebSocket connection management
- Live transcription display
- Medical entity visualization
- Audio level monitoring

### Python Client

The Python client (`medical_streaming_client.py`) offers:
- Command-line interface
- Microphone and file streaming
- Programmatic API access
- Transcript export capabilities

## Usage Examples

### 1. Web Browser Client

1. Open `medical_streaming_client.html` in a web browser
2. Select medical specialty and transcription type
3. Click "Connect" to establish WebSocket connection
4. Click "Start Recording" to begin streaming
5. Speak into your microphone
6. View real-time transcripts and medical entities
7. Click "Stop Recording" when finished

### 2. Python Client - Microphone

```bash
# Basic usage with microphone
python medical_streaming_client.py

# With specific configuration
python medical_streaming_client.py \
    --specialty CARDIOLOGY \
    --type CONVERSATION \
    --duration 30
```

### 3. Python Client - File Streaming

```bash
# Stream from WAV file
python medical_streaming_client.py \
    --file patient_consultation.wav \
    --specialty PRIMARYCARE \
    --type CONVERSATION
```

### 4. Programmatic Usage

```python
import asyncio
from medical_streaming_client import MedicalStreamingClient

async def transcribe_medical_audio():
    # Create client
    client = MedicalStreamingClient("ws://localhost:8000/ws/medical")
    
    # Connect to server
    await client.connect()
    
    # Start transcription
    config = {
        'specialty': 'CARDIOLOGY',
        'type': 'DICTATION',
        'show_speaker_labels': False
    }
    await client.start_transcription(config)
    
    # Stream audio (example with file)
    await client.stream_from_file("doctor_notes.wav")
    
    # Stop and get results
    await client.stop_transcription()
    await client.get_full_transcript()
    
    # Disconnect
    await client.disconnect()

# Run
asyncio.run(transcribe_medical_audio())
```

## WebSocket Protocol

### Client → Server Messages

#### Start Transcription
```json
{
    "type": "start",
    "config": {
        "specialty": "PRIMARYCARE",
        "type": "CONVERSATION",
        "language_code": "en-US",
        "sample_rate": 16000,
        "show_speaker_labels": true,
        "content_identification_type": "PHI"
    }
}
```

#### Send Audio
```json
{
    "type": "audio",
    "data": "base64_encoded_pcm_audio"
}
```

#### Stop Transcription
```json
{
    "type": "stop"
}
```

#### Get Full Transcript
```json
{
    "type": "get_transcript",
    "format": "with_speakers"  // or "plain"
}
```

### Server → Client Messages

#### Session Started
```json
{
    "type": "session_started",
    "session_id": "uuid",
    "config": {
        "specialty": "PRIMARYCARE",
        "type": "CONVERSATION",
        "language_code": "en-US",
        "sample_rate": 16000
    }
}
```

#### Transcript
```json
{
    "type": "transcript",
    "session_id": "uuid",
    "transcript": {
        "text": "Patient has hypertension",
        "is_partial": false,
        "start_time": 1.23,
        "end_time": 2.45,
        "speaker": "1",
        "confidence": 0.98
    },
    "medical_entities": [
        {
            "text": "hypertension",
            "category": "MEDICAL_CONDITION",
            "confidence": 0.95,
            "start_time": 1.8,
            "end_time": 2.3
        }
    ],
    "timestamp": "2024-01-10T12:34:56Z"
}
```

#### Error
```json
{
    "type": "error",
    "error": "Error message",
    "session_id": "uuid",
    "timestamp": "2024-01-10T12:34:56Z"
}
```

## Audio Requirements

- **Format**: PCM (16-bit signed integer)
- **Sample Rate**: 16000 Hz (recommended)
- **Channels**: Mono (1 channel)
- **Encoding**: Little-endian byte order

## Medical Entity Categories

The service recognizes these medical entity types:

1. **MEDICATION** - Drug names, dosages, frequencies
2. **MEDICAL_CONDITION** - Diseases, symptoms, diagnoses
3. **PROTECTED_HEALTH_INFORMATION** - Patient identifiers, dates, locations
4. **TEST_TREATMENT_PROCEDURE** - Medical tests, procedures, treatments
5. **ANATOMY** - Body parts, organs, systems

## Best Practices

### 1. Audio Quality
- Use a good quality microphone
- Minimize background noise
- Speak clearly and at a moderate pace
- Use echo cancellation and noise suppression

### 2. Network Stability
- Ensure stable internet connection
- Implement reconnection logic
- Buffer audio data during temporary disconnections

### 3. Privacy and Security
- Always use HTTPS/WSS in production
- Handle PHI according to HIPAA guidelines
- Implement proper authentication
- Encrypt sensitive data

### 4. Error Handling
- Implement retry logic for transient failures
- Handle rate limiting gracefully
- Provide user feedback for errors
- Log errors for debugging

### 5. Performance Optimization
- Use appropriate chunk sizes (100-200ms)
- Implement client-side audio buffering
- Monitor WebSocket connection health
- Clean up resources properly

## Troubleshooting

### Common Issues

1. **No Audio Input**
   - Check microphone permissions
   - Verify audio device selection
   - Ensure proper audio format

2. **Connection Errors**
   - Verify backend is running
   - Check WebSocket URL
   - Review firewall settings

3. **Poor Transcription Quality**
   - Improve audio quality
   - Select appropriate medical specialty
   - Check for background noise

4. **Rate Limiting**
   - Implement exponential backoff
   - Reduce concurrent connections
   - Contact AWS for limit increases

## Security Considerations

1. **Authentication**: Implement proper authentication before allowing WebSocket connections
2. **Authorization**: Verify user permissions for medical transcription
3. **Encryption**: Use WSS (WebSocket Secure) in production
4. **PHI Handling**: Follow HIPAA guidelines for protected health information
5. **Audit Logging**: Log all transcription sessions for compliance

## Cost Optimization

1. **Efficient Streaming**: Only stream when actively recording
2. **Appropriate Specialty**: Choose the most relevant medical specialty
3. **Session Management**: Properly close sessions when complete
4. **Audio Compression**: Consider audio compression for bandwidth savings

## Advanced Features

### Custom Vocabulary
```python
# Add custom medical terms specific to your use case
config = {
    'vocabulary_name': 'custom_medical_terms',
    # ... other config
}
```

### Multi-Channel Audio
```python
# For telephone conversations with separate channels
config = {
    'enable_channel_identification': True,
    'number_of_channels': 2
}
```

### Real-Time Analytics
```python
# Process entities as they arrive
def process_medical_entities(entities):
    medications = [e for e in entities if e['category'] == 'MEDICATION']
    conditions = [e for e in entities if e['category'] == 'MEDICAL_CONDITION']
    # Perform real-time analysis
```

## Conclusion

The real-time medical streaming implementation provides a robust solution for transcribing medical conversations and dictations. By leveraging AWS Transcribe Medical's streaming capabilities, healthcare applications can provide accurate, real-time transcription with medical entity recognition and PHI detection.