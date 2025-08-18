import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any
import struct
import uuid

import boto3
from botocore.exceptions import ClientError
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
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')  # CONVERSATION or DICTATION

if not S3_BUCKET:
    raise ValueError("S3_BUCKET environment variable is required")

# Initialize AWS clients
s3_client = boto3.client('s3', region_name=AWS_REGION)
transcribe_streaming_client = boto3.client('transcribestreaming', region_name=AWS_REGION)

app = FastAPI(title="Speech Transcription Backend")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscriptionSession:
    """Manages a single transcription session."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.transcribe_stream = None
        self.running = False
        self.final_transcripts = []
        self.response_stream = None
        self.stream_id = str(uuid.uuid4())
    
    async def start_transcription(self):
        """Start AWS Transcribe streaming session."""
        try:
            if USE_MEDICAL_TRANSCRIBE:
                # Use Amazon Transcribe Medical
                logger.info(f"Starting medical transcription with specialty: {MEDICAL_SPECIALTY}, type: {MEDICAL_TYPE}")
                self.transcribe_stream = await self._start_medical_stream()
            else:
                # Use regular Amazon Transcribe
                logger.info("Starting regular transcription")
                self.transcribe_stream = await self._start_regular_stream()
            
            self.running = True
            
            # Start processing transcription events in background
            asyncio.create_task(self._process_transcription_events())
            
            logger.info(f"Started transcription stream: {self.stream_id}")
            
        except Exception as e:
            logger.error(f"Failed to start transcription: {e}")
            raise
    
    async def _start_medical_stream(self):
        """Start medical transcription stream using boto3."""
        try:
            # Start medical stream transcription
            response = transcribe_streaming_client.start_medical_stream_transcription(
                LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
                MediaSampleRateHertz=16000,
                MediaEncoding='pcm',
                Specialty=MEDICAL_SPECIALTY,
                Type=MEDICAL_TYPE,
                EnableChannelIdentification=False,
                NumberOfChannels=1
            )
            
            self.response_stream = response['TranscriptResultStream']
            return response
            
        except ClientError as e:
            logger.error(f"AWS Client Error: {e}")
            raise
    
    async def _start_regular_stream(self):
        """Start regular transcription stream using boto3."""
        try:
            # Start regular stream transcription
            response = transcribe_streaming_client.start_stream_transcription(
                LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
                MediaSampleRateHertz=16000,
                MediaEncoding='pcm',
                EnableChannelIdentification=False,
                NumberOfChannels=1
            )
            
            self.response_stream = response['TranscriptResultStream']
            return response
            
        except ClientError as e:
            logger.error(f"AWS Client Error: {e}")
            raise
    
    async def _process_transcription_events(self):
        """Process transcription events from AWS Transcribe."""
        try:
            # Process events from the transcription stream
            async for event in self.response_stream:
                if 'TranscriptEvent' in event:
                    transcript_event = event['TranscriptEvent']
                    
                    if 'Transcript' in transcript_event:
                        results = transcript_event['Transcript'].get('Results', [])
                        
                        for result in results:
                            if result.get('Alternatives'):
                                alternative = result['Alternatives'][0]
                                transcript = alternative.get('Transcript', '')
                                
                                if transcript:
                                    is_final = not result.get('IsPartial', True)
                                    
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
                                        break
                
                elif 'BadRequestException' in event:
                    logger.error(f"Bad request: {event['BadRequestException']}")
                    break
                    
        except Exception as e:
            logger.error(f"Error processing transcription events: {e}")
        finally:
            self.running = False
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to transcription stream."""
        if self.transcribe_stream and self.running:
            try:
                # Create audio event for boto3 transcribe streaming
                audio_event = {
                    'AudioEvent': {
                        'AudioChunk': audio_data
                    }
                }
                
                # Send audio chunk to transcribe
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.transcribe_stream['AudioInputStream'].send(audio_event)
                )
                
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
                self.running = False
    
    async def stop_transcription(self) -> str:
        """Stop transcription and save final transcript to S3."""
        self.running = False
        
        if self.transcribe_stream:
            try:
                # Send end frame
                end_frame = {'AudioEvent': {}}
                self.transcribe_stream['AudioInputStream'].send(end_frame)
                self.transcribe_stream['AudioInputStream'].close()
                
                # Give some time for final events to process
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error ending stream: {e}")
        
        # Concatenate final transcripts and save to S3
        final_text = " ".join(self.final_transcripts)
        
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
        transcript_type = "medical" if USE_MEDICAL_TRANSCRIBE else "regular"
        s3_key = f"transcripts/{transcript_type}/{timestamp}_{self.stream_id}.txt"
        
        try:
            # Run S3 upload in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=s3_key,
                    Body=transcript.encode('utf-8'),
                    ContentType='text/plain',
                    Metadata={
                        'transcription-type': transcript_type,
                        'language': TRANSCRIBE_LANGUAGE_CODE,
                        'specialty': MEDICAL_SPECIALTY if USE_MEDICAL_TRANSCRIBE else 'N/A',
                        'stream-id': self.stream_id
                    }
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
        while session.running:
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
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client")
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                break
    
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        # Send error message to client if possible
        try:
            error_response = {
                "type": "error",
                "message": str(e)
            }
            await websocket.send_text(json.dumps(error_response))
        except:
            pass
    finally:
        if session.running:
            await session.stop_transcription()
        logger.info("WebSocket connection closed")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Check if we can access S3
        s3_client.head_bucket(Bucket=S3_BUCKET)
        s3_status = "connected"
    except:
        s3_status = "error"
    
    return {
        "status": "healthy",
        "service": "transcription-backend",
        "transcription_mode": "medical" if USE_MEDICAL_TRANSCRIBE else "regular",
        "medical_specialty": MEDICAL_SPECIALTY if USE_MEDICAL_TRANSCRIBE else None,
        "s3_status": s3_status,
        "region": AWS_REGION
    }


@app.get("/config")
async def get_config():
    """Get current configuration."""
    return {
        "use_medical_transcribe": USE_MEDICAL_TRANSCRIBE,
        "medical_specialty": MEDICAL_SPECIALTY,
        "medical_type": MEDICAL_TYPE,
        "language_code": TRANSCRIBE_LANGUAGE_CODE,
        "aws_region": AWS_REGION
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)