# Go Backend for Medical Transcription

This is a Go implementation of the medical transcription backend using AWS SDK v2 and AWS Transcribe Medical streaming API.

## Features

- WebSocket-based real-time audio streaming to AWS Transcribe Medical
- HTTP/2 protocol support for batch audio processing
- S3 integration for audio storage
- Compatible with the existing frontend client
- Built with Go 1.21 and AWS SDK v2

## Architecture

The backend is built using:
- **Gin** - HTTP web framework
- **Gorilla WebSocket** - WebSocket implementation
- **AWS SDK v2** - AWS services integration
  - `transcribestreaming` - For real-time medical transcription
  - `s3` - For audio file storage

## Environment Variables

```bash
# AWS Credentials
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key

# AWS Configuration
AWS_REGION=us-east-1
S3_BUCKET=your-bucket-name

# Transcription Configuration
LANGUAGE_CODE=en-US
MEDICAL_SPECIALTY=PRIMARYCARE
MEDICAL_TYPE=CONVERSATION
MEDICAL_VOCABULARY_NAME=
CONTENT_IDENTIFICATION_TYPE=PHI

# Server Configuration
PORT=8000
LOG_LEVEL=INFO
```

## Running Locally

1. Install Go 1.21 or later
2. Copy `.env.example` to `.env` and configure
3. Install dependencies:
   ```bash
   go mod download
   ```
4. Run the server:
   ```bash
   go run .
   ```

## Running with Docker

Use the dedicated Docker Compose file:

```bash
docker-compose -f docker-compose.go.yml up
```

## API Endpoints

- `GET /health` - Health check endpoint
- `GET /config` - Get current configuration
- `GET /ws` - WebSocket endpoint for streaming transcription

## WebSocket Protocol

The WebSocket protocol follows the same format as the Python implementation:

1. Client connects to `/ws`
2. Client sends protocol selection:
   ```json
   {
     "type": "select_protocol",
     "protocol": "websocket" | "http2"
   }
   ```
3. Server confirms protocol selection
4. Client sends audio data:
   ```json
   {
     "type": "audio",
     "data": "base64_encoded_audio"
   }
   ```
5. Server sends transcript events:
   ```json
   {
     "type": "transcript",
     "transcript": { ... }
   }
   ```
6. Client sends stop signal:
   ```json
   {
     "type": "stop"
   }
   ```

## Key Differences from Python Implementation

1. **Concurrency Model**: Uses Go's goroutines instead of Python's asyncio
2. **Type Safety**: Strongly typed with compile-time checks
3. **Performance**: Generally faster execution and lower memory footprint
4. **Dependencies**: Fewer external dependencies, more built-in functionality

## Development

### Building

```bash
go build -o medical-transcription-backend
```

### Testing

```bash
go test ./...
```

### Linting

```bash
golangci-lint run
```

## Deployment

The application can be deployed using:
- Docker (see Dockerfile)
- Direct binary deployment
- Cloud services (AWS ECS, Google Cloud Run, etc.)

## Known Limitations

1. The HTTP/2 implementation currently only uploads audio to S3 (full transcription integration pending)
2. Medical vocabulary support is basic (can be enhanced)
3. Error handling can be improved for edge cases

## Future Enhancements

1. Implement full HTTP/2 transcription using non-streaming API
2. Add metrics and monitoring
3. Implement connection pooling for AWS clients
4. Add unit and integration tests
5. Support for multiple concurrent sessions per connection