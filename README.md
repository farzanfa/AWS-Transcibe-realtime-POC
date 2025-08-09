# Real-time Speech Transcription POC

 A production-lean proof of concept for real-time speech transcription using AWS Transcribe Streaming, built with a FastAPI backend and an HTML/JavaScript frontend, both containerized with Docker.

## Architecture

```
Browser Microphone → HTML/JS Client (WebRTC) → FastAPI Backend (WebSocket) → AWS Transcribe Streaming → Real-time Transcripts → S3 Storage
```

## Features

- 🎤 **Real-time Audio Capture**: WebRTC-based microphone input through the browser
- 🔄 **Live Transcription**: Real-time speech-to-text using AWS Transcribe Streaming API
- 💾 **Automatic Saving**: Final transcripts saved to S3 with timestamp filenames
- 🐳 **Dockerized**: Complete containerization for easy deployment
- 🏗️ **Infrastructure as Code**: Terraform configuration for AWS resources
- 📊 **Production-Ready**: Proper logging, error handling, and health checks

## Technical Specifications

### Audio Format
- **Sample Rate**: 16kHz
- **Channels**: Mono
- **Encoding**: Linear PCM (16-bit)

### WebSocket Messages

**Client → Backend:**
```json
{
  "type": "audio",
  "payload": "<base64-encoded PCM chunk>"
}
```
```json
{
  "type": "control",
  "action": "stop"
}
```

**Backend → Client:**
```json
{
  "type": "transcript",
  "text": "transcribed text",
  "is_final": true
}
```
```json
{
  "type": "saved",
  "s3_key": "20241215_143022.txt"
}
```

## Prerequisites

- Docker and Docker Compose
- AWS Account with appropriate permissions
- Terraform (for infrastructure setup)

## Quick Start

### 1. Infrastructure Setup

First, deploy the AWS infrastructure using Terraform:

```bash
cd terraform

# Copy and customize variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your preferred settings

# Initialize and apply Terraform
terraform init
terraform plan
terraform apply

# Get the outputs (including access keys)
terraform output
terraform output -raw aws_access_key_id
terraform output -raw aws_secret_access_key
terraform output s3_bucket_name
```

### 2. Environment Configuration

Create environment file with AWS credentials:

```bash
# Copy the environment template
cp env.example .env

# Edit .env with your AWS credentials from Terraform outputs
# AWS_ACCESS_KEY_ID=<from terraform output>
# AWS_SECRET_ACCESS_KEY=<from terraform output>
# S3_BUCKET=<from terraform output>
# AWS_REGION=us-east-1
```

### 3. Run the Application

Start both services using Docker Compose v2 (`docker compose`):

```bash
# Build and start all services
docker compose up --build

# Or run in background
docker compose up -d --build
```

### 4. Access the Application

- **Frontend**: http://localhost:8080/simple_client.html
- **Backend API**: ws://localhost:8000/ws
- **Health Check**: http://localhost:8000/health

## Usage

1. Open the simple HTML client at http://localhost:8080/simple_client.html
2. Click "🎤 Start Recording" to begin audio capture
3. Speak into your microphone - you'll see real-time transcripts appear
4. Click "⏹️ Stop Recording" to end the session
5. Final transcript will be automatically saved to S3 with a timestamp filename

## Project Structure

```
.
├── README.md
├── docker-compose.yml
├── env.example
├── .gitignore
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                 # FastAPI backend with WebSocket support
│
├── client/
│   ├── Dockerfile
│   ├── serve_client.py         # Simple HTTP server for the HTML client (port 8080)
│   └── simple_client.html      # HTML/JS client using WebRTC + WebSocket
│
└── terraform/
    ├── main.tf                 # Main Terraform configuration
    ├── variables.tf            # Variable definitions
    ├── outputs.tf              # Output definitions
    └── terraform.tfvars.example # Example variables file
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | - | AWS access key (from Terraform) |
| `AWS_SECRET_ACCESS_KEY` | - | AWS secret key (from Terraform) |
| `AWS_REGION` | `us-east-1` | AWS region |
| `S3_BUCKET` | - | S3 bucket name (from Terraform) |
| `TRANSCRIBE_LANGUAGE_CODE` | `en-US` | Language code for transcription |
| `LOG_LEVEL` | `INFO` | Logging level |

### Terraform Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `us-east-1` | AWS region for resources |
| `bucket_prefix` | `speech-transcription` | Prefix for S3 bucket name |
| `iam_user_prefix` | `transcription-service` | Prefix for IAM user name |
| `environment` | `dev` | Environment tag |

## Development

### Local Development

For development without Docker:

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Client (in another terminal)
cd client
python3 serve_client.py
```

### Logs

View application logs:

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f client
```

## Troubleshooting

### Common Issues

1. **WebSocket Connection Failed**
   - Ensure backend is running and healthy
   - Check firewall settings
   - Verify environment variables

2. **Audio Not Capturing**
   - Allow microphone permissions in browser
   - Check browser compatibility with WebRTC
   - Ensure HTTPS for production (WebRTC requirement)

3. **AWS Transcribe Errors**
   - Verify AWS credentials and permissions
   - Check AWS region configuration
   - Ensure Transcribe service is available in your region

4. **S3 Upload Failures**
   - Verify S3 bucket exists and is accessible
   - Check IAM permissions for `s3:PutObject`
   - Confirm bucket name in environment variables

### Health Checks

Check service health:

```bash
# Backend health
curl http://localhost:8000/health

# Check service status
docker compose ps
```

## Production Considerations

### Security
- Use HTTPS in production (required for WebRTC)
- Implement proper authentication and authorization
- Use AWS IAM roles instead of access keys when possible
- Enable S3 bucket encryption and versioning (included in Terraform)

### Scalability
- Consider using AWS ECS/EKS for container orchestration
- Implement connection pooling for WebSocket connections
- Use AWS Application Load Balancer for high availability
- Monitor AWS service limits (Transcribe concurrent streams)

### Monitoring
- Implement application metrics and monitoring
- Set up AWS CloudWatch alarms
- Add structured logging for better observability
- Monitor S3 storage costs and implement lifecycle policies

## Cost Optimization

- AWS Transcribe Streaming: ~$0.024 per minute
- S3 storage: Standard pricing applies
- Consider using S3 Intelligent Tiering for automatic cost optimization

## Cleanup

To destroy all AWS resources:

```bash
cd terraform
terraform destroy
```

To stop and remove Docker containers:

```bash
docker compose down
docker compose down --volumes  # Also remove volumes
```

## License

This project is provided as-is for demonstration purposes.

## Contributing

This is a POC project. For production use, consider:
- Adding comprehensive error handling
- Implementing proper authentication
- Adding unit and integration tests
- Setting up CI/CD pipelines
- Adding monitoring and alerting
