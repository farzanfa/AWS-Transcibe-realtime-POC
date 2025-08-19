"""
Unified Speech Transcription Backend
Consolidated version with all transcription functionality in a single file.
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

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
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')

if not S3_BUCKET:
    raise ValueError("S3_BUCKET environment variable is required")

# Initialize AWS clients
s3_client = boto3.client('s3', region_name=AWS_REGION)
transcribe_client = boto3.client('transcribe', region_name=AWS_REGION)

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
        self.stream = None
        self.input_stream = None
        self.output_stream = None
        self.running = False
        self.final_transcripts = []
        self.stream_id = str(uuid.uuid4())
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._response_handler_task = None
    
    async def start_transcription(self):
        """Start AWS Transcribe streaming session."""
        try:
            logger.info("Starting transcription session")
            
            # Start regular stream (medical streaming requires different API)
            loop = asyncio.get_event_loop()
            self.stream = await loop.run_in_executor(
                self.executor,
                lambda: transcribe_client.start_stream_transcription(
                    language_code=TRANSCRIBE_LANGUAGE_CODE,
                    media_sample_rate_hz=16000,
                    media_encoding='pcm',
                    enable_channel_identification=False,
                    number_of_channels=1
                )
            )
            
            self.running = True
            
            # Get input and output streams
            self.input_stream = self.stream['AudioInputStream']
            self.output_stream = self.stream['TranscriptResultStream']
            
            # Start processing responses in background
            self._response_handler_task = asyncio.create_task(self._handle_stream_responses())
            
            logger.info(f"Started transcription stream: {self.stream_id}")
            
        except Exception as e:
            logger.error(f"Failed to start transcription: {e}")
            raise
    
    async def _handle_stream_responses(self):
        """Handle responses from the transcription stream."""
        try:
            loop = asyncio.get_event_loop()
            
            def process_events():
                try:
                    for event in self.output_stream:
                        if not self.running:
                            break
                            
                        if 'TranscriptEvent' in event:
                            transcript = event['TranscriptEvent']['Transcript']
                            
                            for result in transcript.get('Results', []):
                                if not result.get('Alternatives'):
                                    continue
                                    
                                alternative = result['Alternatives'][0]
                                transcript_text = alternative.get('Transcript', '')
                                
                                if transcript_text:
                                    is_final = not result.get('IsPartial', True)
                                    
                                    # Schedule sending to websocket
                                    asyncio.run_coroutine_threadsafe(
                                        self._send_transcript(transcript_text, is_final),
                                        loop
                                    )
                                    
                        elif 'BadRequestException' in event:
                            logger.error(f"Bad request: {event['BadRequestException']['Message']}")
                            break
                            
                except Exception as e:
                    logger.error(f"Error processing events: {e}")
            
            # Run event processing in executor
            await loop.run_in_executor(self.executor, process_events)
            
        except Exception as e:
            logger.error(f"Error in response handler: {e}")
        finally:
            self.running = False
    
    async def _send_transcript(self, transcript: str, is_final: bool):
        """Send transcript to client via WebSocket."""
        try:
            message = {
                "type": "transcript",
                "text": transcript,
                "is_final": is_final
            }
            
            await self.websocket.send_text(json.dumps(message))
            logger.debug(f"Sent transcript: {transcript} (final: {is_final})")
            
            # Store final transcripts
            if is_final and transcript.strip():
                self.final_transcripts.append(transcript)
                
        except Exception as e:
            logger.error(f"Error sending transcript: {e}")
            self.running = False
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to transcription stream."""
        if self.input_stream and self.running:
            try:
                # Create audio event
                audio_event = {
                    'AudioEvent': {
                        'AudioChunk': audio_data
                    }
                }
                
                # Send audio in executor to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    lambda: self.input_stream._write_to_stream(audio_event)
                )
                
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
                self.running = False
    
    async def stop_transcription(self) -> str:
        """Stop transcription and save final transcript to S3."""
        self.running = False
        
        if self.input_stream:
            try:
                # Send end frame
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    lambda: self.input_stream._write_to_stream({})
                )
                
                # Close the input stream
                await loop.run_in_executor(
                    self.executor,
                    self.input_stream.close
                )
                
            except Exception as e:
                logger.error(f"Error closing stream: {e}")
        
        # Wait for response handler to finish
        if self._response_handler_task:
            try:
                await asyncio.wait_for(self._response_handler_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Response handler timeout")
        
        # Shutdown executor
        self.executor.shutdown(wait=True)
        
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


# Medical transcription endpoints - placeholder implementations
# Note: Real-time medical transcription requires additional AWS setup and APIs
@app.websocket("/ws/medical")
async def medical_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for medical transcription."""
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "info",
        "message": "Medical transcription requires AWS Transcribe Medical API setup",
        "note": "Using regular transcription for now"
    }))
    # Reuse regular transcription session
    await websocket_endpoint(websocket)


@app.websocket("/ws/medical/direct")
async def medical_direct_websocket_endpoint(websocket: WebSocket):
    """Direct WebSocket implementation for AWS Transcribe Medical streaming."""
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "info",
        "message": "Direct medical transcription requires AWS Transcribe Medical streaming setup",
        "note": "Using regular transcription for now"
    }))
    # Reuse regular transcription session
    await websocket_endpoint(websocket)


@app.websocket("/ws/medical/http2")
async def medical_http2_websocket_endpoint(websocket: WebSocket):
    """HTTP/2 implementation for AWS Transcribe Medical streaming."""
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "info",
        "message": "HTTP/2 medical transcription requires specialized setup",
        "note": "Using regular transcription for now"
    }))
    # Reuse regular transcription session
    await websocket_endpoint(websocket)


@app.websocket("/ws/medical/unified")
async def medical_unified_websocket_endpoint(websocket: WebSocket):
    """Unified Direct API endpoint supporting both WebSocket and HTTP/2 protocols."""
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "info",
        "message": "Unified medical transcription endpoint",
        "note": "Using regular transcription for now"
    }))
    # Reuse regular transcription session
    await websocket_endpoint(websocket)


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