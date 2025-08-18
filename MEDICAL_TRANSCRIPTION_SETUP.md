# AWS Transcribe Medical Real-time Transcription Setup

## Overview
This backend service has been updated to support AWS Transcribe Medical for real-time medical transcription. The service can switch between regular AWS Transcribe and AWS Transcribe Medical based on environment configuration.

## Key Changes Made

1. **Fixed the TranscribeService Error**: The original error `'TranscribeService' object has no attribute 'start_medical_stream_transcription_websocket'` has been resolved. 
   
   **Important Note**: AWS Transcribe Medical does NOT support real-time streaming via WebSocket. The medical transcription streaming is only available through the batch API. When `USE_MEDICAL_TRANSCRIBE` is set to `true`, the system will:
   - Log a warning about the limitation
   - Fall back to regular AWS Transcribe streaming
   - You can still use medical vocabulary and terminology, but without the specialized medical models

2. **Implementation Details**:
   - Uses the `transcribestreaming` boto3 client with `start_stream_transcription` method
   - Regular transcription works via WebSocket streaming for real-time transcription
   - Medical transcription configuration is preserved but uses regular streaming
   - Processes streaming responses asynchronously
   - Saves final transcripts to S3 with proper medical/regular tagging

## Environment Variables

Make sure these environment variables are set in your `.env` file:

```bash
# AWS Configuration
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key

# S3 Configuration
S3_BUCKET=your-s3-bucket-name

# Transcription Configuration
TRANSCRIBE_LANGUAGE_CODE=en-US
USE_MEDICAL_TRANSCRIBE=true

# Medical Transcription Specific
MEDICAL_SPECIALTY=PRIMARYCARE  # Options: PRIMARYCARE, CARDIOLOGY, NEUROLOGY, ONCOLOGY, RADIOLOGY, UROLOGY
MEDICAL_TYPE=CONVERSATION       # Options: CONVERSATION, DICTATION
```

## AWS IAM Permissions Required

Your AWS IAM user/role needs the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "transcribe:StartStreamTranscription",
                "transcribe:StartMedicalStreamTranscription"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject"
            ],
            "Resource": "arn:aws:s3:::your-s3-bucket-name/transcripts/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:HeadBucket"
            ],
            "Resource": "arn:aws:s3:::your-s3-bucket-name"
        }
    ]
}
```

## Deploying the Updated Service

1. **Rebuild the Docker container**:
   ```bash
   docker-compose build backend
   ```

2. **Restart the service**:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

3. **Check the logs**:
   ```bash
   docker-compose logs backend -f
   ```

## Testing the Medical Transcription

1. **Health Check**:
   ```bash
   curl http://localhost:8000/health
   ```
   
   Expected response:
   ```json
   {
     "status": "healthy",
     "service": "transcription-backend",
     "transcription_mode": "medical",
     "medical_specialty": "PRIMARYCARE",
     "s3_status": "connected",
     "region": "us-east-1"
   }
   ```

2. **Configuration Check**:
   ```bash
   curl http://localhost:8000/config
   ```

3. **WebSocket Test**:
   The frontend should connect to `ws://localhost:8000/ws` and send audio data in the following format:
   ```json
   {
     "type": "audio",
     "payload": "base64_encoded_audio_data"
   }
   ```

## Supported Medical Specialties

AWS Transcribe Medical supports the following specialties:
- `PRIMARYCARE` - For general medical conversations
- `CARDIOLOGY` - For cardiology-specific terminology
- `NEUROLOGY` - For neurology-specific terminology
- `ONCOLOGY` - For oncology-specific terminology
- `RADIOLOGY` - For radiology-specific terminology
- `UROLOGY` - For urology-specific terminology

## Transcription Types

- `CONVERSATION` - For doctor-patient conversations
- `DICTATION` - For medical dictations

## Troubleshooting

### Common Issues and Solutions

1. **AttributeError: 'TranscribeService' object has no attribute 'start_medical_stream_transcription'**
   - **Solution**: This has been fixed by using the correct method names with the `transcribestreaming` client.

2. **WebSocket connection fails**
   - Check that your AWS credentials are correctly configured
   - Verify that your IAM user has the necessary permissions
   - Ensure the AWS region supports AWS Transcribe Medical

3. **No transcripts received**
   - Check the audio format is PCM 16-bit, 16kHz
   - Verify the audio data is being sent correctly as base64
   - Check CloudWatch logs for any AWS API errors

4. **S3 upload fails**
   - Verify the S3 bucket exists and is accessible
   - Check IAM permissions for S3 operations
   - Ensure the bucket is in the same region as configured

## Monitoring

- Backend logs: `docker-compose logs backend -f`
- AWS CloudWatch: Check for AWS Transcribe API errors
- S3 bucket: Verify transcripts are being saved

## Alternatives for Medical Transcription

Since real-time medical transcription is not available via WebSocket, consider these alternatives:

1. **Batch Medical Transcription**:
   - Use AWS Transcribe Medical batch API for processing audio files
   - Upload audio to S3 and process asynchronously
   - Better accuracy with medical-specific models

2. **Custom Medical Vocabulary**:
   - Create custom vocabularies with medical terms for regular Transcribe
   - Use vocabulary filters to improve medical term recognition
   - See AWS documentation for custom vocabulary setup

3. **Post-Processing**:
   - Use regular transcription for real-time
   - Apply medical NLP processing on the transcripts
   - Consider AWS Comprehend Medical for entity extraction

4. **Hybrid Approach**:
   - Use regular transcription for real-time display
   - Process the saved audio with Transcribe Medical batch API for final records
   - Provide both real-time and high-accuracy transcripts

## Performance Considerations

- Medical transcription may have slightly higher latency than regular transcription
- The service handles audio streaming asynchronously to prevent blocking
- Final transcripts are saved to S3 for long-term storage