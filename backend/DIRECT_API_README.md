# AWS Transcribe Medical Direct API Implementation

This implementation provides direct access to the `StartMedicalStreamTranscription` API using both WebSocket and HTTP/2 protocols.

## Overview

The Direct API allows real-time medical transcription with:
- Low-latency streaming
- Medical entity detection
- PHI (Protected Health Information) identification
- Support for medical specialties and vocabularies

## Key Features

### 1. WebSocket Protocol (`/ws/medical/direct`)
- Direct WebSocket connection to AWS Transcribe Medical
- Real-time bidirectional streaming
- Event stream format for audio and transcripts
- Automatic reconnection handling

### 2. HTTP/2 Protocol (`/ws/medical/http2`)
- HTTP/2 streaming connection
- Multiplexing support
- Header compression
- Server push capabilities

### 3. Unified Endpoint (`/ws/medical/unified`)
- Auto-selects the best protocol
- Fallback support
- Protocol negotiation

## Architecture

### Authentication
- Uses AWS SigV4 signing for secure connections
- Supports IAM roles and temporary credentials
- Pre-signed URLs for WebSocket connections

### Event Stream Format
- Audio events: PCM 16-bit, 16kHz
- Transcript events: JSON with medical entities
- Error handling and retry logic

## Usage

### Running the Backend

```bash
cd backend
pip install -r requirements.txt
python main_direct_api.py
```

### Environment Variables

```bash
export AWS_REGION=us-east-1
export S3_BUCKET=your-bucket-name
export MEDICAL_SPECIALTY=PRIMARYCARE  # or CARDIOLOGY, NEUROLOGY, etc.
export MEDICAL_TYPE=CONVERSATION      # or DICTATION
export CONTENT_IDENTIFICATION_TYPE=PHI # or ALL
export LANGUAGE_CODE=en-US
```

### Using the Client

1. Open `client/direct_api_client.html` in a web browser
2. Select protocol (WebSocket, HTTP/2, or Auto)
3. Click "Connect"
4. Click "Start Recording" and allow microphone access
5. Speak - medical entities will be detected in real-time
6. Click "Stop Recording" to end session

## Medical Entity Categories

The Direct API detects these medical entity types:

- **MEDICATION**: Drug names, dosages, frequencies
- **MEDICAL_CONDITION**: Diseases, symptoms, diagnoses
- **ANATOMY**: Body parts, organs, anatomical systems
- **TEST_TREATMENT_PROCEDURE**: Lab tests, procedures, therapies
- **PHI**: Names, dates, identifiers (when enabled)

## WebSocket Protocol Details

### Connection Flow
1. Client connects to `/ws/medical/direct`
2. Server creates pre-signed URL with SigV4
3. Server establishes WebSocket to AWS
4. Audio streams from client → server → AWS
5. Transcripts stream from AWS → server → client

### Message Format

**Client → Server:**
```json
{
  "type": "audio",
  "data": "base64_encoded_pcm_audio"
}
```

**Server → Client:**
```json
{
  "type": "transcript",
  "session_id": "uuid",
  "transcript": {
    "text": "Patient presents with...",
    "is_partial": false,
    "entities": [
      {
        "text": "hypertension",
        "category": "MEDICAL_CONDITION",
        "confidence": 0.98
      }
    ]
  }
}
```

## HTTP/2 Protocol Details

### Connection Flow
1. Client connects to `/ws/medical/http2`
2. Server initiates HTTP/2 POST request
3. Streaming request/response over single connection
4. Multiplexed audio and transcript streams

### Benefits
- Better performance over high-latency networks
- Efficient header compression
- Stream prioritization
- Server push for real-time updates

## Error Handling

The implementation includes:
- Automatic retry with exponential backoff
- Graceful degradation between protocols
- Detailed error messages
- Session recovery mechanisms

## Security Considerations

- All connections use AWS IAM authentication
- Audio data is encrypted in transit
- PHI detection can be configured
- S3 storage with encryption at rest
- No audio data is stored locally

## Performance Tips

1. Use WebSocket for lowest latency
2. Use HTTP/2 for better reliability
3. Keep audio chunks small (4096 samples)
4. Monitor network bandwidth
5. Implement client-side buffering

## Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Check AWS credentials
   - Verify IAM permissions for Transcribe
   - Ensure region is correct

2. **Connection Failures**
   - Check network connectivity
   - Verify WebSocket/HTTP/2 support
   - Check firewall rules

3. **No Transcription**
   - Verify audio format (PCM 16-bit, 16kHz)
   - Check microphone permissions
   - Monitor console for errors

## Required IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "transcribe:StartMedicalStreamTranscription"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::your-bucket-name/*"
    }
  ]
}
```

## Additional Resources

- [AWS Transcribe Medical Documentation](https://docs.aws.amazon.com/transcribe/latest/dg/what-is-transcribe-medical.html)
- [WebSocket API Reference](https://docs.aws.amazon.com/transcribe/latest/dg/websocket-med.html)
- [HTTP/2 API Reference](https://docs.aws.amazon.com/transcribe/latest/dg/http2-med.html)