# AWS Medical Transcription Flow Verification

## Complete Flow Diagram

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌────────────────────┐
│  Client Browser │────▶│  Backend Server  │────▶│    AWS API      │────▶│  AWS Medical       │
│   (client.html) │◀────│    (main.py)     │◀────│  (WebSocket)    │◀────│  Transcription     │
└─────────────────┘     └──────────────────┘     └─────────────────┘     └────────────────────┘
        │                        │                                                  
        │                        ▼                                                  
        │                  ┌──────────┐                                            
        │                  │  AWS S3  │                                            
        │                  │  Bucket  │                                            
        │                  └──────────┘                                            
```

## Verified Components

### 1. Client (Browser) → WebSocket → Backend Server ✓

**Client Side (client.html)**:
- WebSocket connection established at line 480: `ws = new WebSocket(endpoint)`
- Three endpoints available:
  - `ws://localhost:8000/ws/medical/direct` (WebSocket protocol)
  - `ws://localhost:8000/ws/medical/http2` (HTTP/2 protocol)
  - `ws://localhost:8000/ws/medical/unified` (Auto-select protocol)
- Audio capture and sending at line 711: `ws.send(JSON.stringify({type: 'audio', data: base64}))`
- Transcript receiving at line 496: `ws.onmessage = (event) => { handleMessage(data) }`

**Backend Side (main.py)**:
- WebSocket endpoints defined:
  - Line 650: `@app.websocket("/ws/medical/direct")`
  - Line 707: `@app.websocket("/ws/medical/http2")`
  - Line 765: `@app.websocket("/ws/medical/unified")`
- Accepts WebSocket connections and creates session objects

### 2. Backend Server → S3 Saving ✓

**S3 Configuration**:
- S3 client initialized at line 48: `s3_client = boto3.client('s3', region_name=AWS_REGION)`
- S3 bucket from environment at line 37: `S3_BUCKET = os.getenv('S3_BUCKET')`

**Saving Function**:
- `save_to_s3` method at line 147 in `MedicalTranscriptionSessionBase` class
- Saves transcript with metadata at line 153:
  ```python
  s3_client.put_object(
      Bucket=S3_BUCKET,
      Key=s3_key,
      Body=json.dumps(content, indent=2).encode('utf-8'),
      ContentType='application/json'
  )
  ```
- Called when session ends (lines 392 and 633)

### 3. Backend Server → AWS API ✓

**AWS Connection**:
- Creates presigned URL for WebSocket at line 181: `create_presigned_url()`
- WebSocket connection to AWS at line 243:
  ```python
  self.aws_websocket = await websockets.connect(
      url,
      subprotocols=['aws-transcribe-medical']
  )
  ```
- Uses AWS SigV4 authentication at line 214: `SigV4Auth(creds, 'transcribe', AWS_REGION)`

**Endpoint Configuration**:
- AWS Medical Transcribe endpoint: `transcribestreaming.{region}.amazonaws.com`
- Path: `/medical-stream-transcription-websocket`
- Parameters include: language-code, media-encoding, sample-rate, specialty, type

### 4. Audio Flow: Client → Backend → AWS ✓

**Client to Backend**:
1. Audio captured via MediaRecorder API
2. Converted to base64 at line 709-713 in client.html
3. Sent as JSON message: `{type: 'audio', data: base64}`

**Backend Processing**:
1. Receives audio message at line 678: `if message['type'] == 'audio'`
2. Decodes base64 at line 680: `audio_data = base64.b64decode(message['data'])`
3. Forwards to AWS at line 681: `await session.send_audio(audio_data)`

**To AWS**:
- `send_audio` method at line 345 sends raw audio to AWS WebSocket
- AWS handles event stream formatting internally

### 5. Transcript Flow: AWS → Backend → Client ✓

**AWS to Backend**:
1. Receives messages in `_receive_loop` at line 272
2. Parses event stream messages at line 280: `EventStreamParser.parse_message(message)`
3. Processes TranscriptEvent at line 88

**Backend Processing**:
1. Extracts transcript text and entities
2. Formats response at lines 325-332:
   ```python
   {
       'type': 'transcript',
       'session_id': self.session_id,
       'transcript': {
           'text': text,
           'is_partial': is_partial,
           'entities': entities
       }
   }
   ```

**Backend to Client**:
- Sends via WebSocket: `await self.websocket.send_text(json.dumps(...))`

**Client Handling**:
1. Receives message in `handleMessage` at line 530
2. Updates transcript display at line 576: `updateTranscriptDisplay(text, isPartial)`
3. Shows medical entities in UI

## Medical Entity Detection

The flow includes automatic detection of:
- Medications (with dosage, route, frequency)
- Medical conditions
- Treatments and procedures
- Anatomy references
- PHI (Protected Health Information) when configured

## Session Management

1. **Session Start**: Creates unique session ID, establishes AWS connection
2. **During Session**: Streams audio, receives transcripts, collects entities
3. **Session End**: 
   - Aggregates all transcripts and entities
   - Creates summary with entity counts
   - Saves complete transcript to S3
   - Returns S3 location to client

## Error Handling

- WebSocket reconnection logic
- AWS connection error handling
- Graceful session cleanup
- Client-side error notifications

## Configuration

All configuration via environment variables:
- `AWS_REGION`: AWS region for services
- `S3_BUCKET`: Bucket for transcript storage
- `MEDICAL_SPECIALTY`: Medical specialty (e.g., PRIMARYCARE)
- `MEDICAL_TYPE`: Conversation type (e.g., CONVERSATION)
- `CONTENT_IDENTIFICATION_TYPE`: PHI detection level
- `LANGUAGE_CODE`: Language for transcription

## Verified ✓

The complete flow from client browser through WebSocket to backend server, then to AWS Medical Transcription API, with S3 saving functionality, is fully implemented and verified.