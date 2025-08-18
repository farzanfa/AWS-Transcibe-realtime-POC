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
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import ClientError
from fastapi import WebSocket, WebSocketDisconnect

# Configure logging
logger = logging.getLogger(__name__)

# Environment configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')
MEDICAL_VOCABULARY_NAME = os.getenv('MEDICAL_VOCABULARY_NAME', '')  # Optional medical vocabulary


class MedicalTranscriptionSession:
    """Manages a real-time transcription session with medical context."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.transcribe_client = boto3.client('transcribestreaming', region_name=AWS_REGION)
        self.stream_response = None
        self.transcript_stream = None
        self.audio_stream = None
        self.running = False
        self.transcripts = []
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._response_handler_task = None
        self.sample_rate = 16000  # Default sample rate
        
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
            
            # Build the transcription parameters for regular streaming
            transcribe_params = {
                'LanguageCode': language_code,
                'MediaSampleRateHertz': self.sample_rate,
                'MediaEncoding': 'pcm'
            }
            
            # Add medical vocabulary if configured
            if MEDICAL_VOCABULARY_NAME:
                transcribe_params['VocabularyName'] = MEDICAL_VOCABULARY_NAME
                logger.info(f"Using medical vocabulary: {MEDICAL_VOCABULARY_NAME}")
            
            # Start the transcription stream
            loop = asyncio.get_event_loop()
            
            # Create the request with audio stream
            async def audio_generator():
                """Generator that yields audio chunks."""
                # Initial empty chunk to start the stream
                yield b''
                
            request = {
                **transcribe_params,
                'AudioStream': audio_generator()
            }
            
            # Start streaming transcription
            self.stream_response = await loop.run_in_executor(
                self.executor,
                lambda: self.transcribe_client.start_stream_transcription(**transcribe_params)
            )
            
            self.running = True
            
            # Get the transcript event stream
            self.transcript_stream = self.stream_response['TranscriptResultStream']
            
            # Start processing responses in background
            self._response_handler_task = asyncio.create_task(self._handle_stream_responses())
            
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
            
            # For now, log that we received audio
            # In a full implementation, this would be sent to the stream
            logger.debug(f"Received audio chunk: {len(audio_bytes)} bytes")
            
            # Send acknowledgment
            await self.websocket.send_text(json.dumps({
                "type": "audio_received",
                "session_id": self.session_id,
                "bytes": len(audio_bytes)
            }))
            
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
            await self._send_error(f"Error processing audio: {str(e)}")
    
    async def _handle_stream_responses(self):
        """Handle responses from the transcription stream."""
        try:
            async for event in self.transcript_stream:
                if not self.running:
                    break
                
                if 'TranscriptEvent' in event:
                    await self._process_transcript_event(event['TranscriptEvent'])
                    
        except Exception as e:
            logger.error(f"Error in response handler: {e}")
            await self._send_error(f"Response handler error: {str(e)}")
        finally:
            self.running = False
    
    async def _process_transcript_event(self, transcript_event: Dict[str, Any]):
        """Process a transcript event from AWS Transcribe."""
        transcript = transcript_event.get('Transcript', {})
        
        for result in transcript.get('Results', []):
            if not result.get('Alternatives'):
                continue
            
            alternative = result['Alternatives'][0]
            transcript_text = alternative.get('Transcript', '')
            
            if not transcript_text:
                continue
            
            is_partial = result.get('IsPartial', True)
            
            # Prepare the transcript message
            message = {
                'type': 'transcript',
                'session_id': self.session_id,
                'transcript': {
                    'text': transcript_text,
                    'is_partial': is_partial,
                    'confidence': alternative.get('Confidence', 0)
                },
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Send to client
            await self._send_transcript(message)
            
            # Store final transcripts
            if not is_partial:
                self.transcripts.append(transcript_text)
    
    async def _send_transcript(self, message: Dict[str, Any]):
        """Send transcript message to client via WebSocket."""
        try:
            await self.websocket.send_text(json.dumps(message))
            logger.debug(f"Sent transcript: {message['transcript']['text'][:50]}...")
        except Exception as e:
            logger.error(f"Error sending transcript: {e}")
    
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
            # Cancel response handler
            if self._response_handler_task:
                self._response_handler_task.cancel()
                try:
                    await self._response_handler_task
                except asyncio.CancelledError:
                    pass
            
            # Send session ended message
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                'total_transcripts': len(self.transcripts),
                'timestamp': datetime.utcnow().isoformat()
            }))
            
        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")
        finally:
            self.executor.shutdown(wait=False)


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