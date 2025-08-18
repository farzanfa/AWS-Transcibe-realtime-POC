import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any

import boto3
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
S3_BUCKET = os.getenv('S3_BUCKET')
TRANSCRIBE_LANGUAGE_CODE = os.getenv('TRANSCRIBE_LANGUAGE_CODE', 'en-US')
USE_MEDICAL_TRANSCRIBE = os.getenv('USE_MEDICAL_TRANSCRIBE', 'true').lower() == 'true'
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')  # PRIMARYCARE, CARDIOLOGY, NEUROLOGY, ONCOLOGY, RADIOLOGY, UROLOGY

if not S3_BUCKET:
    raise ValueError("S3_BUCKET environment variable is required")

# Initialize AWS clients
s3_client = boto3.client('s3', region_name=AWS_REGION)
transcribe_client = TranscribeStreamingClient(region=AWS_REGION)

app = FastAPI(title="Speech Transcription Backend")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscriptionHandler(TranscriptResultStreamHandler):
    """Handler for processing transcription results from AWS Transcribe."""
    
    def __init__(self, websocket: WebSocket, transcript_result_stream):
        super().__init__(transcript_result_stream)
        self.websocket = websocket
        self.final_transcripts = []
    
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        """Process transcript events and send to client."""
        results = transcript_event.transcript.results
        
        for result in results:
            if result.alternatives:
                transcript = result.alternatives[0].transcript
                is_final = not result.is_partial
                
                # Send real-time transcript to client
                message = {
                    "type": "transcript",
                    "text": transcript,
                    "is_final": is_final
                }
                
                try:
                    await self.websocket.send_text(json.dumps(message))
                    logger.debug(f"Sent transcript: {transcript} (final: {is_final})")
                    
                    # Store final transcripts for later saving
                    if is_final and transcript.strip():
                        self.final_transcripts.append(transcript)
                        
                except Exception as e:
                    logger.error(f"Error sending transcript: {e}")


class TranscriptionSession:
    """Manages a single transcription session."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.handler = None
        self.stream = None
        self.running = False
    
    async def start_transcription(self):
        """Start AWS Transcribe streaming session."""
        try:
            if USE_MEDICAL_TRANSCRIBE:
                # Use Amazon Transcribe Medical
                self.stream = await transcribe_client.start_medical_stream_transcription(
                    language_code=TRANSCRIBE_LANGUAGE_CODE,
                    media_sample_rate_hz=16000,
                    media_encoding="pcm",
                    specialty=MEDICAL_SPECIALTY,
                )
                logger.info(f"Started medical transcription stream with specialty: {MEDICAL_SPECIALTY}")
            else:
                # Use regular Amazon Transcribe
                self.stream = await transcribe_client.start_stream_transcription(
                    language_code=TRANSCRIBE_LANGUAGE_CODE,
                    media_sample_rate_hz=16000,
                    media_encoding="pcm",
                )
            
            # Create handler with the stream
            self.handler = TranscriptionHandler(self.websocket, self.stream.output_stream)
            
            # Start handler in background
            self.running = True
            asyncio.create_task(self.handler.handle_events())
            
            logger.info("Started transcription stream")
            
        except Exception as e:
            logger.error(f"Failed to start transcription: {e}")
            raise
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to transcription stream."""
        if self.stream and self.running:
            try:
                await self.stream.input_stream.send_audio_event(audio_chunk=audio_data)
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
    
    async def stop_transcription(self) -> str:
        """Stop transcription and save final transcript to S3."""
        self.running = False
        
        if self.stream:
            try:
                await self.stream.input_stream.end_stream()
                # Give some time for final events to process
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error ending stream: {e}")
        
        # Concatenate final transcripts and save to S3
        final_text = " ".join(self.handler.final_transcripts)
        
        if final_text.strip():
            s3_key = await self.save_to_s3(final_text)
            logger.info(f"Saved transcript to S3: {s3_key}")
            return s3_key
        else:
            logger.info("No final transcript to save")
            return ""
    
    async def save_to_s3(self, transcript: str) -> str:
        """Save transcript to S3 bucket."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_key = f"{timestamp}.txt"
        
        try:
            # Run S3 upload in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=s3_key,
                    Body=transcript.encode('utf-8'),
                    ContentType='text/plain'
                )
            )
            return s3_key
            
        except Exception as e:
            logger.error(f"Failed to upload to S3: {e}")
            raise


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time transcription."""
    await websocket.accept()
    logger.info("WebSocket connection established")
    
    session = TranscriptionSession(websocket)
    
    try:
        # Start transcription session
        await session.start_transcription()
        
        # Listen for messages from client
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message["type"] == "audio":
                    # Decode base64 audio and send to transcription
                    audio_data = base64.b64decode(message["payload"])
                    await session.send_audio(audio_data)
                    
                elif message["type"] == "control" and message["action"] == "stop":
                    # Stop transcription and save to S3
                    s3_key = await session.stop_transcription()
                    
                    # Send confirmation to client
                    response = {
                        "type": "saved",
                        "s3_key": s3_key
                    }
                    await websocket.send_text(json.dumps(response))
                    break
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {e}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                break
    
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if session.running:
            await session.stop_transcription()
        logger.info("WebSocket connection closed")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "transcription-backend"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
