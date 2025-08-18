# AWS Transcribe SDK Update

## Summary of Changes

The medical transcription backend has been updated to use the official `amazon-transcribe` SDK instead of the unsupported `transcribestreaming` client from boto3.

### Issues Fixed

1. **Error**: `Unknown service: 'transcribestreaming'`
   - The boto3 library doesn't have a `transcribestreaming` client
   - AWS Transcribe streaming requires the specialized `amazon-transcribe` SDK

### Changes Made

1. **Updated `backend/requirements.txt`**:
   - Added `amazon-transcribe>=0.6.2` dependency

2. **Refactored `backend/medical_streaming.py`**:
   - Replaced boto3 `transcribestreaming` client with `amazon-transcribe` SDK
   - Implemented proper async streaming using `TranscribeStreamingClient`
   - Created custom `MedicalTranscriptHandler` class for handling transcript events
   - Updated audio streaming to use queue-based approach for better async handling

### How to Deploy

1. **Rebuild Docker containers**:
   ```bash
   docker compose down
   docker compose up --build -d
   ```

2. **Verify the deployment**:
   ```bash
   # Check if backend is healthy
   curl http://localhost:8000/health
   
   # Check logs
   docker compose logs backend -f
   ```

3. **Test WebSocket connection**:
   - Open http://localhost:8080/medical_streaming_client.html
   - Click "Start Recording" to test the WebSocket connection

### Technical Details

The new implementation:
- Uses `TranscribeStreamingClient` from `amazon-transcribe` SDK
- Implements proper async/await patterns for streaming
- Maintains compatibility with existing WebSocket protocol
- Supports medical vocabulary configuration via `MEDICAL_VOCABULARY_NAME` env variable

### Environment Variables

The following environment variables are still supported:
- `AWS_REGION`: AWS region for Transcribe service (default: us-east-1)
- `MEDICAL_SPECIALTY`: Medical specialty context (default: PRIMARYCARE)
- `MEDICAL_TYPE`: Type of medical transcription (default: CONVERSATION)
- `MEDICAL_VOCABULARY_NAME`: Optional custom medical vocabulary name

### Notes

- AWS Transcribe Medical real-time streaming is not available through the SDK
- The implementation uses regular AWS Transcribe with medical vocabulary support
- Ensure AWS credentials are properly configured with permissions for `transcribe:StartStreamTranscription`