"""
Real-time medical transcription using AWS Transcribe Medical streaming API.
This module provides WebSocket-based real-time audio streaming and transcription
using AWS Transcribe Medical's StartMedicalStreamTranscription API.
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
import struct

import boto3
from botocore.exceptions import ClientError
from fastapi import WebSocket, WebSocketDisconnect

# Configure logging
logger = logging.getLogger(__name__)

# Environment configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')
MEDICAL_CONTENT_IDENTIFICATION_TYPE = os.getenv('MEDICAL_CONTENT_IDENTIFICATION_TYPE', 'PHI')
SHOW_SPEAKER_LABELS = os.getenv('SHOW_SPEAKER_LABELS', 'true').lower() == 'true'


class MedicalTranscriptionSession:
    """Manages a real-time medical transcription session using AWS Transcribe Medical."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.transcribe_client = boto3.client('transcribe', region_name=AWS_REGION)
        self.stream = None
        self.input_stream = None
        self.output_stream = None
        self.running = False
        self.transcripts = []
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._response_handler_task = None
        self.audio_buffer = bytearray()
        self.sample_rate = 16000  # Default sample rate
        
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """
        Start AWS Transcribe Medical streaming session.
        
        Args:
            config: Optional configuration dictionary with:
                - specialty: Medical specialty (PRIMARYCARE, CARDIOLOGY, etc.)
                - type: Transcription type (CONVERSATION or DICTATION)
                - language_code: Language code (default: en-US)
                - sample_rate: Audio sample rate in Hz (default: 16000)
                - show_speaker_labels: Enable speaker identification
                - content_identification_type: PHI or NONE
        """
        try:
            # Apply configuration
            if config:
                specialty = config.get('specialty', MEDICAL_SPECIALTY)
                medical_type = config.get('type', MEDICAL_TYPE)
                language_code = config.get('language_code', 'en-US')
                self.sample_rate = config.get('sample_rate', 16000)
                show_speaker_labels = config.get('show_speaker_labels', SHOW_SPEAKER_LABELS)
                content_id_type = config.get('content_identification_type', MEDICAL_CONTENT_IDENTIFICATION_TYPE)
            else:
                specialty = MEDICAL_SPECIALTY
                medical_type = MEDICAL_TYPE
                language_code = 'en-US'
                show_speaker_labels = SHOW_SPEAKER_LABELS
                content_id_type = MEDICAL_CONTENT_IDENTIFICATION_TYPE
            
            logger.info(f"Starting medical transcription stream - Session: {self.session_id}")
            logger.info(f"Configuration - Specialty: {specialty}, Type: {medical_type}, Language: {language_code}")
            
            # Start the medical transcription stream
            loop = asyncio.get_event_loop()
            
            # Build the transcription parameters
            transcribe_params = {
                'language_code': language_code,
                'media_sample_rate_hz': self.sample_rate,
                'media_encoding': 'pcm',
                'specialty': specialty,
                'type': medical_type,
                'content_identification_type': content_id_type,
                'enable_channel_identification': False,
                'number_of_channels': 1
            }
            
            # Add speaker labels if requested and type is CONVERSATION
            if show_speaker_labels and medical_type == 'CONVERSATION':
                transcribe_params['show_speaker_labels'] = True
            
            # Start the stream
            self.stream = await loop.run_in_executor(
                self.executor,
                lambda: self.transcribe_client.start_medical_stream_transcription(**transcribe_params)
            )
            
            self.running = True
            
            # Get input and output streams
            self.input_stream = self.stream['AudioInputStream']
            self.output_stream = self.stream['TranscriptResultStream']
            
            # Start processing responses in background
            self._response_handler_task = asyncio.create_task(self._handle_stream_responses())
            
            # Send success message to client
            await self.websocket.send_text(json.dumps({
                "type": "session_started",
                "session_id": self.session_id,
                "config": {
                    "specialty": specialty,
                    "type": medical_type,
                    "language_code": language_code,
                    "sample_rate": self.sample_rate,
                    "content_identification_type": content_id_type
                }
            }))
            
            logger.info(f"Medical transcription stream started successfully: {self.session_id}")
            
        except ClientError as e:
            error_msg = f"AWS Client Error starting medical transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Failed to start medical transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
    
    async def send_audio_chunk(self, audio_data: str):
        """
        Send audio chunk to AWS Transcribe Medical.
        
        Args:
            audio_data: Base64 encoded audio data
        """
        if not self.running or not self.input_stream:
            logger.warning("Stream not running, cannot send audio")
            return
        
        try:
            # Decode base64 audio data
            audio_bytes = base64.b64decode(audio_data)
            
            # Send to AWS Transcribe Medical
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.input_stream.send(audio_bytes)
            )
            
            logger.debug(f"Sent audio chunk: {len(audio_bytes)} bytes")
            
        except Exception as e:
            logger.error(f"Error sending audio chunk: {e}")
            await self._send_error(f"Error processing audio: {str(e)}")
    
    async def _handle_stream_responses(self):
        """Handle responses from the medical transcription stream."""
        try:
            loop = asyncio.get_event_loop()
            
            def process_events():
                try:
                    for event in self.output_stream:
                        if not self.running:
                            break
                        
                        if 'TranscriptEvent' in event:
                            self._process_transcript_event(event['TranscriptEvent'], loop)
                        elif 'BadRequestException' in event:
                            error_msg = event['BadRequestException'].get('Message', 'Bad request')
                            logger.error(f"Bad request from AWS: {error_msg}")
                            asyncio.run_coroutine_threadsafe(
                                self._send_error(f"Transcription error: {error_msg}"),
                                loop
                            )
                            break
                        elif 'LimitExceededException' in event:
                            error_msg = event['LimitExceededException'].get('Message', 'Limit exceeded')
                            logger.error(f"Limit exceeded: {error_msg}")
                            asyncio.run_coroutine_threadsafe(
                                self._send_error(f"Rate limit exceeded: {error_msg}"),
                                loop
                            )
                            break
                        elif 'InternalFailureException' in event:
                            error_msg = event['InternalFailureException'].get('Message', 'Internal failure')
                            logger.error(f"Internal AWS failure: {error_msg}")
                            asyncio.run_coroutine_threadsafe(
                                self._send_error(f"Service error: {error_msg}"),
                                loop
                            )
                            break
                            
                except Exception as e:
                    logger.error(f"Error processing events: {e}")
                    asyncio.run_coroutine_threadsafe(
                        self._send_error(f"Stream processing error: {str(e)}"),
                        loop
                    )
            
            # Run event processing in executor
            await loop.run_in_executor(self.executor, process_events)
            
        except Exception as e:
            logger.error(f"Error in response handler: {e}")
            await self._send_error(f"Response handler error: {str(e)}")
        finally:
            self.running = False
    
    def _process_transcript_event(self, transcript_event: Dict[str, Any], loop):
        """Process a transcript event from AWS Transcribe Medical."""
        transcript = transcript_event.get('Transcript', {})
        
        for result in transcript.get('Results', []):
            if not result.get('Alternatives'):
                continue
            
            alternative = result['Alternatives'][0]
            transcript_text = alternative.get('Transcript', '')
            
            if not transcript_text:
                continue
            
            is_partial = result.get('IsPartial', True)
            start_time = result.get('StartTime', 0)
            end_time = result.get('EndTime', 0)
            
            # Extract medical entities if available
            entities = alternative.get('Entities', [])
            medical_entities = []
            
            for entity in entities:
                medical_entities.append({
                    'text': entity.get('Content', ''),
                    'category': entity.get('Category', ''),
                    'confidence': entity.get('Confidence', 0),
                    'start_time': entity.get('StartTime', 0),
                    'end_time': entity.get('EndTime', 0)
                })
            
            # Extract speaker labels if available
            speaker_label = None
            if 'Speaker' in result:
                speaker_label = result['Speaker']
            
            # Prepare the transcript message
            message = {
                'type': 'transcript',
                'session_id': self.session_id,
                'transcript': {
                    'text': transcript_text,
                    'is_partial': is_partial,
                    'start_time': start_time,
                    'end_time': end_time,
                    'speaker': speaker_label,
                    'confidence': alternative.get('Confidence', 0)
                },
                'medical_entities': medical_entities,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Send to client
            asyncio.run_coroutine_threadsafe(
                self._send_transcript(message),
                loop
            )
            
            # Store final transcripts
            if not is_partial and transcript_text.strip():
                self.transcripts.append({
                    'text': transcript_text,
                    'speaker': speaker_label,
                    'entities': medical_entities,
                    'timestamp': datetime.utcnow().isoformat()
                })
    
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
            # Close the input stream
            if self.input_stream:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    self.input_stream.close
                )
            
            # Wait for response handler to finish
            if self._response_handler_task:
                await asyncio.wait_for(self._response_handler_task, timeout=5.0)
            
            # Send session ended message
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                'total_transcripts': len(self.transcripts),
                'timestamp': datetime.utcnow().isoformat()
            }))
            
        except asyncio.TimeoutError:
            logger.warning("Response handler task timeout during shutdown")
        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")
        finally:
            self.executor.shutdown(wait=False)
    
    def get_full_transcript(self) -> str:
        """Get the complete transcript as a single string."""
        return ' '.join([t['text'] for t in self.transcripts])
    
    def get_transcript_with_speakers(self) -> str:
        """Get the complete transcript with speaker labels."""
        lines = []
        current_speaker = None
        
        for transcript in self.transcripts:
            speaker = transcript.get('speaker')
            text = transcript['text']
            
            if speaker != current_speaker:
                current_speaker = speaker
                if speaker:
                    lines.append(f"\n[Speaker {speaker}]: {text}")
                else:
                    lines.append(f"\n{text}")
            else:
                lines.append(f" {text}")
        
        return ''.join(lines).strip()
    
    def get_medical_entities_summary(self) -> Dict[str, list]:
        """Get a summary of all medical entities found in the transcription."""
        entity_summary = {}
        
        for transcript in self.transcripts:
            for entity in transcript.get('entities', []):
                category = entity['category']
                if category not in entity_summary:
                    entity_summary[category] = []
                
                # Add unique entities
                entity_text = entity['text']
                if entity_text not in [e['text'] for e in entity_summary[category]]:
                    entity_summary[category].append({
                        'text': entity_text,
                        'confidence': entity['confidence']
                    })
        
        return entity_summary


async def handle_medical_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for medical transcription.
    
    This is the main entry point for WebSocket connections requesting
    real-time medical transcription.
    """
    await websocket.accept()
    session = None
    
    try:
        logger.info("Medical WebSocket connection established")
        session = MedicalTranscriptionSession(websocket)
        
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
                
            elif message_type == 'get_transcript':
                # Send full transcript
                transcript_format = message.get('format', 'plain')
                
                if transcript_format == 'with_speakers':
                    transcript = session.get_transcript_with_speakers()
                else:
                    transcript = session.get_full_transcript()
                
                await websocket.send_text(json.dumps({
                    'type': 'full_transcript',
                    'transcript': transcript,
                    'entities': session.get_medical_entities_summary(),
                    'session_id': session.session_id
                }))
                
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