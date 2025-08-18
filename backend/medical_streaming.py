"""
Real-time medical transcription using AWS Transcribe streaming API.
This module provides WebSocket-based real-time audio streaming and transcription.

Note: AWS Transcribe Medical real-time streaming is not available through the standard SDK.
This implementation uses regular AWS Transcribe with medical vocabulary support.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent, TranscriptResultStream
from botocore.exceptions import ClientError
from fastapi import WebSocket, WebSocketDisconnect

# Configure logging
logger = logging.getLogger(__name__)

# Environment configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')
MEDICAL_VOCABULARY_NAME = os.getenv('MEDICAL_VOCABULARY_NAME', '')  # Optional medical vocabulary


class MedicalTranscriptHandler(TranscriptResultStreamHandler):
    """Handler for AWS Transcribe streaming results."""
    
    def __init__(self, output_stream: TranscriptResultStream, websocket: WebSocket, session_id: str):
        super().__init__(output_stream)
        self.websocket = websocket
        self.session_id = session_id
        self.transcripts = []
        
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        """Handle transcript events from AWS Transcribe."""
        results = transcript_event.transcript.results
        
        for result in results:
            if not result.alternatives:
                continue
            
            alternative = result.alternatives[0]
            transcript_text = alternative.transcript
            
            if not transcript_text:
                continue
            
            is_partial = result.is_partial
            
            # Prepare the transcript message
            message = {
                'type': 'transcript',
                'session_id': self.session_id,
                'transcript': {
                    'text': transcript_text,
                    'is_partial': is_partial,
                    'confidence': getattr(alternative, 'confidence', None)
                },
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Send to client
            try:
                await self.websocket.send_text(json.dumps(message))
                logger.debug(f"Sent transcript: {transcript_text[:50]}...")
            except Exception as e:
                logger.error(f"Error sending transcript: {e}")
            
            # Store final transcripts
            if not is_partial:
                self.transcripts.append(transcript_text)


class MedicalTranscriptionSession:
    """Manages a real-time transcription session with medical context."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.transcribe_client = None
        self.stream = None
        self.handler = None
        self.running = False
        self.sample_rate = 16000  # Default sample rate
        self.audio_queue = asyncio.Queue()
        self._write_task = None
        self._handler_task = None
        
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """
        Start AWS Transcribe streaming session with medical context.
        
        Args:
            config: Optional configuration dictionary
        """
        try:
            # Apply configuration
            if config:
                language_code = config.get('language_code', 'en-US')
                self.sample_rate = config.get('sample_rate', 16000)
                specialty = config.get('specialty', MEDICAL_SPECIALTY)
                medical_type = config.get('type', MEDICAL_TYPE)
            else:
                language_code = 'en-US'
                specialty = MEDICAL_SPECIALTY
                medical_type = MEDICAL_TYPE
            
            logger.info(f"Starting medical-context transcription stream - Session: {self.session_id}")
            logger.info(f"Configuration - Language: {language_code}, Medical context: {specialty}/{medical_type}")
            
            # Create the TranscribeStreamingClient
            self.transcribe_client = TranscribeStreamingClient(region=AWS_REGION)
            
            # Prepare transcription parameters
            transcribe_kwargs = {
                'language_code': language_code,
                'media_sample_rate_hz': self.sample_rate,
                'media_encoding': 'pcm'
            }
            
            # Add medical vocabulary if configured
            if MEDICAL_VOCABULARY_NAME:
                transcribe_kwargs['vocabulary_name'] = MEDICAL_VOCABULARY_NAME
                logger.info(f"Using medical vocabulary: {MEDICAL_VOCABULARY_NAME}")
            
            # Start the transcription stream
            self.stream = await self.transcribe_client.start_stream_transcription(**transcribe_kwargs)
            
            self.running = True
            
            # Create handler for transcript events
            self.handler = MedicalTranscriptHandler(
                self.stream.output_stream,
                self.websocket,
                self.session_id
            )
            
            # Start the audio writer task
            self._write_task = asyncio.create_task(self._write_audio_chunks())
            
            # Start the handler task
            self._handler_task = asyncio.create_task(self.handler.handle_events())
            
            # Send success message to client
            await self.websocket.send_text(json.dumps({
                "type": "session_started",
                "session_id": self.session_id,
                "config": {
                    "language_code": language_code,
                    "sample_rate": self.sample_rate,
                    "medical_context": f"{specialty}/{medical_type}",
                    "vocabulary": MEDICAL_VOCABULARY_NAME or "none"
                },
                "note": "Using regular transcribe with medical vocabulary support"
            }))
            
            logger.info(f"Transcription stream started successfully: {self.session_id}")
            
        except ClientError as e:
            error_msg = f"AWS Client Error: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Failed to start transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
    
    async def _write_audio_chunks(self):
        """Write audio chunks to the transcription stream."""
        try:
            while self.running:
                try:
                    # Get audio chunk from queue with timeout
                    audio_chunk = await asyncio.wait_for(
                        self.audio_queue.get(),
                        timeout=1.0
                    )
                    
                    if audio_chunk is None:  # Sentinel value to stop
                        break
                    
                    # Send audio to transcribe
                    await self.stream.input_stream.send_audio_event(audio_chunk=audio_chunk)
                    
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error writing audio chunk: {e}")
                    
        except Exception as e:
            logger.error(f"Error in audio writer: {e}")
        finally:
            # End the stream
            try:
                await self.stream.input_stream.end_stream()
            except Exception as e:
                logger.error(f"Error ending stream: {e}")
    
    async def send_audio_chunk(self, audio_data: str):
        """
        Send audio chunk to AWS Transcribe.
        
        Args:
            audio_data: Base64 encoded audio data
        """
        if not self.running:
            logger.warning("Stream not running, cannot send audio")
            return
        
        try:
            # Decode base64 audio data
            audio_bytes = base64.b64decode(audio_data)
            
            # Add to queue for processing
            await self.audio_queue.put(audio_bytes)
            
            # Send acknowledgment
            await self.websocket.send_text(json.dumps({
                "type": "audio_received",
                "session_id": self.session_id,
                "bytes": len(audio_bytes)
            }))
            
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
            await self._send_error(f"Error processing audio: {str(e)}")
    
    async def _send_error(self, error_message: str):
        """Send error message to client."""
        try:
            await self.websocket.send_text(json.dumps({
                'type': 'error',
                'error': error_message,
                'session_id': self.session_id,
                'timestamp': datetime.utcnow().isoformat()
            }))
        except Exception as e:
            logger.error(f"Error sending error message: {e}")
    
    async def stop_transcription(self):
        """Stop the transcription session."""
        logger.info(f"Stopping transcription session: {self.session_id}")
        self.running = False
        
        try:
            # Send sentinel to stop audio writer
            if self.audio_queue:
                await self.audio_queue.put(None)
            
            # Wait for tasks to complete
            if self._write_task and not self._write_task.done():
                await asyncio.wait_for(self._write_task, timeout=5.0)
            
            if self._handler_task and not self._handler_task.done():
                self._handler_task.cancel()
                try:
                    await self._handler_task
                except asyncio.CancelledError:
                    pass
            
            # Get transcript count from handler
            transcript_count = len(self.handler.transcripts) if self.handler else 0
            
            # Send session ended message
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                'total_transcripts': transcript_count,
                'timestamp': datetime.utcnow().isoformat()
            }))
            
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for tasks to complete")
        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")


async def handle_medical_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for medical transcription.
    
    This uses regular AWS Transcribe with medical vocabulary support,
    as real-time AWS Transcribe Medical is not available through the SDK.
    """
    await websocket.accept()
    session = None
    
    try:
        logger.info("Medical WebSocket connection established")
        session = MedicalTranscriptionSession(websocket)
        
        # Send initial message about the service
        await websocket.send_text(json.dumps({
            "type": "info",
            "message": "Connected to medical transcription service",
            "note": "Using AWS Transcribe with medical vocabulary support"
        }))
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            message_type = message.get('type')
            
            if message_type == 'start':
                # Start transcription with optional configuration
                config = message.get('config', {})
                await session.start_transcription(config)
                
            elif message_type == 'audio':
                # Process audio chunk
                audio_data = message.get('data')
                if audio_data:
                    await session.send_audio_chunk(audio_data)
                
            elif message_type == 'stop':
                # Stop transcription
                await session.stop_transcription()
                break
            else:
                logger.warning(f"Unknown message type: {message_type}")
                
    except WebSocketDisconnect:
        logger.info("Medical WebSocket disconnected by client")
    except Exception as e:
        logger.error(f"Medical WebSocket error: {e}")
        if session:
            await session._send_error(str(e))
    finally:
        if session and session.running:
            await session.stop_transcription()
        logger.info("Medical WebSocket connection closed")