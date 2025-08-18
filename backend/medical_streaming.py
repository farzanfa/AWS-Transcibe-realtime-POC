"""
Real-time medical transcription using AWS Transcribe Medical streaming API.
This module provides WebSocket-based real-time audio streaming and transcription
using AWS Transcribe Medical streaming capabilities.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import warnings

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent, TranscriptResultStream
from botocore.exceptions import ClientError
from fastapi import WebSocket, WebSocketDisconnect

# Suppress the specific InvalidStateError that occurs during shutdown
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*InvalidStateError.*")

# Configure logging
logger = logging.getLogger(__name__)

# Monkey patch to handle AWS SDK's InvalidStateError during shutdown
import concurrent.futures
_original_set_result = concurrent.futures.Future.set_result

def _patched_set_result(self, result):
    """Patched set_result that ignores InvalidStateError during shutdown."""
    try:
        _original_set_result(self, result)
    except concurrent.futures.InvalidStateError:
        # This happens when the future is cancelled during shutdown
        # We can safely ignore it
        logger.debug("Ignoring InvalidStateError on cancelled future during shutdown")

# Apply the patch
concurrent.futures.Future.set_result = _patched_set_result

# Environment configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')
MEDICAL_VOCABULARY_NAME = os.getenv('MEDICAL_VOCABULARY_NAME', '')  # Optional medical vocabulary
MEDICAL_CONTENT_IDENTIFICATION_TYPE = os.getenv('MEDICAL_CONTENT_IDENTIFICATION_TYPE', 'PHI')
SHOW_SPEAKER_LABELS = os.getenv('SHOW_SPEAKER_LABELS', 'false').lower() == 'true'


class MedicalTranscriptHandler(TranscriptResultStreamHandler):
    """Handler for AWS Transcribe Medical streaming results."""
    
    def __init__(self, output_stream: TranscriptResultStream, websocket: WebSocket, session_id: str):
        super().__init__(output_stream)
        self.websocket = websocket
        self.session_id = session_id
        self.transcripts = []
        
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        """Handle transcript events from AWS Transcribe Medical."""
        results = transcript_event.transcript.results
        
        for result in results:
            if not result.alternatives:
                continue
            
            alternative = result.alternatives[0]
            transcript_text = alternative.transcript
            
            if not transcript_text:
                continue
            
            is_partial = result.is_partial
            
            # Extract medical entities if available
            entities = []
            if hasattr(alternative, 'entities'):
                for entity in alternative.entities:
                    entities.append({
                        'text': entity.content,
                        'category': entity.category,
                        'confidence': entity.confidence
                    })
            
            # Prepare the transcript message
            message = {
                'type': 'transcript',
                'session_id': self.session_id,
                'transcript': {
                    'text': transcript_text,
                    'is_partial': is_partial,
                    'confidence': getattr(alternative, 'confidence', None),
                    'entities': entities  # Medical entities identified
                },
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Add speaker label if available
            if hasattr(result, 'speaker_label'):
                message['transcript']['speaker'] = result.speaker_label
            
            # Send to client
            try:
                await self.websocket.send_text(json.dumps(message))
                logger.debug(f"Sent transcript: {transcript_text[:50]}...")
            except Exception as e:
                logger.error(f"Error sending transcript: {e}")
            
            # Store final transcripts
            if not is_partial:
                self.transcripts.append({
                    'text': transcript_text,
                    'timestamp': datetime.utcnow().isoformat(),
                    'entities': entities
                })


class MedicalTranscriptionSession:
    """Manages a real-time medical transcription session."""
    
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
        Start AWS Transcribe Medical streaming session.
        
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
                vocabulary_name = config.get('vocabulary_name', MEDICAL_VOCABULARY_NAME)
                content_identification_type = config.get('content_identification_type', MEDICAL_CONTENT_IDENTIFICATION_TYPE)
                show_speaker_label = config.get('show_speaker_label', SHOW_SPEAKER_LABELS)
            else:
                language_code = 'en-US'
                specialty = MEDICAL_SPECIALTY
                medical_type = MEDICAL_TYPE
                vocabulary_name = MEDICAL_VOCABULARY_NAME
                content_identification_type = MEDICAL_CONTENT_IDENTIFICATION_TYPE
                show_speaker_label = SHOW_SPEAKER_LABELS
            
            logger.info(f"Starting medical transcription stream - Session: {self.session_id}")
            logger.info(f"Configuration - Specialty: {specialty}, Type: {medical_type}, Language: {language_code}")
            
            # Create the TranscribeStreamingClient
            self.transcribe_client = TranscribeStreamingClient(region=AWS_REGION)
            
            # Prepare transcription parameters for medical streaming
            transcribe_kwargs = {
                'language_code': language_code,
                'media_sample_rate_hz': self.sample_rate,
                'media_encoding': 'pcm',
                # Medical-specific parameters
                'specialty': specialty,
                'type': medical_type,
                'content_identification_type': content_identification_type,
                'show_speaker_label': show_speaker_label
            }
            
            # Add medical vocabulary if configured
            if vocabulary_name:
                transcribe_kwargs['vocabulary_name'] = vocabulary_name
                logger.info(f"Using medical vocabulary: {vocabulary_name}")
            
            # Try to start medical stream transcription
            try:
                # Attempt to use start_medical_stream_transcription if available
                self.stream = await self.transcribe_client.start_medical_stream_transcription(**transcribe_kwargs)
                logger.info("Started medical stream transcription")
            except AttributeError:
                # Fallback to regular transcription with medical parameters
                logger.warning("start_medical_stream_transcription not available, using regular transcription with medical context")
                # Remove medical-specific parameters for regular transcription
                regular_kwargs = {
                    'language_code': language_code,
                    'media_sample_rate_hz': self.sample_rate,
                    'media_encoding': 'pcm'
                }
                if vocabulary_name:
                    regular_kwargs['vocabulary_name'] = vocabulary_name
                
                self.stream = await self.transcribe_client.start_stream_transcription(**regular_kwargs)
            
            self.running = True
            
            # Create handler for transcript events
            self.handler = MedicalTranscriptHandler(
                self.stream.output_stream,
                self.websocket,
                self.session_id
            )
            
            # Start the audio writer task
            self._write_task = asyncio.create_task(self._write_audio_chunks())
            
            # Start the handler task with error wrapper
            async def handle_events_wrapper():
                try:
                    await self.handler.handle_events()
                except asyncio.CancelledError:
                    logger.debug("Handler task cancelled")
                except Exception as e:
                    error_str = str(e).lower()
                    if any(x in error_str for x in ["closed", "cancelled", "invalid state"]):
                        logger.debug(f"Expected error during handler shutdown: {e}")
                    else:
                        logger.error(f"Unexpected error in handler: {e}")
            
            self._handler_task = asyncio.create_task(handle_events_wrapper())
            
            # Send success message to client
            await self.websocket.send_text(json.dumps({
                "type": "session_started",
                "session_id": self.session_id,
                "config": {
                    "language_code": language_code,
                    "sample_rate": self.sample_rate,
                    "medical_specialty": specialty,
                    "medical_type": medical_type,
                    "vocabulary": vocabulary_name or "none",
                    "content_identification": content_identification_type,
                    "speaker_labels": show_speaker_label
                },
                "timestamp": datetime.utcnow().isoformat()
            }))
            
            logger.info(f"Medical transcription stream started successfully: {self.session_id}")
            
        except ClientError as e:
            error_msg = f"AWS Client Error: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Failed to start medical transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
    
    async def _write_audio_chunks(self):
        """Write audio chunks to the transcription stream."""
        stream_ended = False
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
                    
                    # Check if stream is still valid before sending
                    if not self.running or not self.stream:
                        break
                    
                    # Send audio to transcribe with error handling
                    try:
                        await self.stream.input_stream.send_audio_event(audio_chunk=audio_chunk)
                    except Exception as e:
                        if "closed" in str(e).lower() or "cancelled" in str(e).lower():
                            logger.debug("Stream appears to be closed, stopping audio writer")
                            break
                        raise
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    logger.debug("Audio writer task cancelled")
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    if any(x in error_str for x in ["closed", "cancelled", "invalid state"]):
                        logger.debug(f"Expected error during shutdown: {e}")
                        break
                    logger.error(f"Error writing audio chunk: {e}")
                    
        except asyncio.CancelledError:
            logger.debug("Audio writer task cancelled during main loop")
        except Exception as e:
            logger.error(f"Error in audio writer: {e}")
        finally:
            # End the stream if not already ended and still running
            if not stream_ended and self.stream and self.running:
                try:
                    await self.stream.input_stream.end_stream()
                    stream_ended = True
                    logger.debug("Stream ended successfully")
                except Exception as e:
                    error_str = str(e).lower()
                    if not any(x in error_str for x in ["closed", "cancelled", "invalid state"]):
                        logger.error(f"Error ending stream: {e}")
    
    async def send_audio_chunk(self, audio_data: str):
        """
        Send audio chunk to AWS Transcribe Medical.
        
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
        logger.info(f"Stopping medical transcription session: {self.session_id}")
        self.running = False
        
        try:
            # Send sentinel to stop audio writer
            if self.audio_queue:
                try:
                    await self.audio_queue.put(None)
                except Exception:
                    pass  # Queue might be closed
            
            # First, try to gracefully close the stream
            if self.stream and hasattr(self.stream, 'input_stream'):
                try:
                    # Don't wait for the stream to end, just mark it as ending
                    asyncio.create_task(self.stream.input_stream.end_stream())
                except Exception as e:
                    logger.debug(f"Error ending stream (this is often expected): {e}")
            
            # Cancel tasks in parallel
            tasks_to_cancel = []
            if self._write_task and not self._write_task.done():
                tasks_to_cancel.append(self._write_task)
            if self._handler_task and not self._handler_task.done():
                tasks_to_cancel.append(self._handler_task)
            
            # Cancel all tasks
            for task in tasks_to_cancel:
                task.cancel()
            
            # Wait for tasks to be cancelled with a short timeout
            if tasks_to_cancel:
                done, pending = await asyncio.wait(
                    tasks_to_cancel,
                    timeout=2.0,
                    return_when=asyncio.ALL_COMPLETED
                )
                
                # Force cancel any pending tasks
                for task in pending:
                    logger.warning(f"Force cancelling task: {task}")
                    task.cancel()
            
            # Get transcript data from handler
            transcripts = self.handler.transcripts if self.handler else []
            
            # Send session ended message with summary
            try:
                await self.websocket.send_text(json.dumps({
                    'type': 'session_ended',
                    'session_id': self.session_id,
                    'summary': {
                        'total_transcripts': len(transcripts),
                        'duration': f"{len(transcripts) * 2} seconds (estimated)",  # Rough estimate
                        'medical_entities_found': sum(len(t.get('entities', [])) for t in transcripts)
                    },
                    'timestamp': datetime.utcnow().isoformat()
                }))
            except Exception as e:
                logger.debug(f"Could not send session ended message: {e}")
            
        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")


async def handle_medical_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for medical transcription.
    
    This uses AWS Transcribe Medical streaming for real-time medical transcription
    with support for medical specialties, vocabularies, and entity detection.
    """
    await websocket.accept()
    session = None
    
    try:
        logger.info("Medical WebSocket connection established")
        session = MedicalTranscriptionSession(websocket)
        
        # Send initial message about the service
        await websocket.send_text(json.dumps({
            "type": "info",
            "message": "Connected to AWS Transcribe Medical streaming service",
            "supported_specialties": ["PRIMARYCARE", "CARDIOLOGY", "NEUROLOGY", "ONCOLOGY", "RADIOLOGY", "UROLOGY"],
            "features": {
                "entity_detection": True,
                "speaker_identification": SHOW_SPEAKER_LABELS,
                "custom_vocabulary": bool(MEDICAL_VOCABULARY_NAME),
                "content_identification": MEDICAL_CONTENT_IDENTIFICATION_TYPE
            }
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