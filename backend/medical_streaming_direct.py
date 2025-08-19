"""
Direct implementation of AWS Transcribe Medical streaming using WebSocket protocol.
This provides guaranteed access to StartMedicalStreamTranscription API.
"""

import asyncio
import base64
import json
import logging
import os
import struct
import uuid
import zlib
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import boto3
import websockets
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')


class DirectMedicalTranscriptionSession:
    """Direct WebSocket implementation for AWS Transcribe Medical streaming."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.aws_websocket = None
        self.running = False
        self.transcripts = []
        self._receive_task = None
        self._send_task = None
        self.audio_queue = asyncio.Queue()
        
        # AWS credentials
        self.session = boto3.Session()
        self.credentials = self.session.get_credentials()
    
    def _create_presigned_url(self, config: Dict[str, Any]) -> str:
        """Create a pre-signed URL for WebSocket connection."""
        # Parameters for the transcription
        params = {
            'language-code': config.get('language_code', 'en-US'),
            'sample-rate': str(config.get('sample_rate', 16000)),
            'media-encoding': 'pcm',
            'specialty': config.get('specialty', MEDICAL_SPECIALTY),
            'type': config.get('type', MEDICAL_TYPE)
        }
        
        # Add optional parameters
        if config.get('vocabulary_name'):
            params['vocabulary-name'] = config['vocabulary_name']
        
        if config.get('show_speaker_label', False):
            params['show-speaker-label'] = 'true'
        
        if config.get('content_identification_type'):
            params['content-identification-type'] = config['content_identification_type']
        
        # Create the WebSocket URL
        query_string = urlencode(params)
        url = f"wss://transcribestreaming.{AWS_REGION}.amazonaws.com:8443/medical-stream-transcription-websocket?{query_string}"
        
        # Create the request for signing
        request = AWSRequest(method='GET', url=url)
        
        # Sign the request
        signer = SigV4Auth(self.credentials, 'transcribe', AWS_REGION)
        signer.add_auth(request)
        
        # Get the signed URL
        signed_url = f"{url}&{urlencode(dict(request.headers))}"
        
        return signed_url
    
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """Start AWS Transcribe Medical streaming via direct WebSocket."""
        try:
            config = config or {}
            
            logger.info(f"Starting direct medical transcription - Session: {self.session_id}")
            
            # Create pre-signed URL
            signed_url = self._create_presigned_url(config)
            
            # Connect to AWS Transcribe Medical WebSocket
            self.aws_websocket = await websockets.connect(
                signed_url,
                subprotocols=['aws-transcribe-medical-websocket-static'],
                extra_headers={
                    'Content-Type': 'application/json',
                    'X-Amz-Target': 'com.amazonaws.transcribe.Transcribe.StartMedicalStreamTranscription'
                }
            )
            
            self.running = True
            
            # Start receive and send tasks
            self._receive_task = asyncio.create_task(self._receive_from_aws())
            self._send_task = asyncio.create_task(self._send_to_aws())
            
            # Send session started message
            await self.websocket.send_text(json.dumps({
                "type": "session_started",
                "session_id": self.session_id,
                "config": config,
                "method": "direct_websocket",
                "timestamp": datetime.utcnow().isoformat()
            }))
            
            logger.info("Direct medical WebSocket connection established")
            
        except Exception as e:
            error_msg = f"Failed to start direct medical transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
    
    async def _receive_from_aws(self):
        """Receive and process messages from AWS Transcribe Medical."""
        try:
            while self.running:
                message = await self.aws_websocket.recv()
                
                # Parse the event stream message
                event = self._parse_event_stream(message)
                
                if event and event.get('Transcript'):
                    await self._process_transcript(event['Transcript'])
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("AWS WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error receiving from AWS: {e}")
        finally:
            self.running = False
    
    async def _send_to_aws(self):
        """Send audio data to AWS Transcribe Medical."""
        try:
            while self.running:
                try:
                    # Get audio chunk from queue
                    audio_chunk = await asyncio.wait_for(
                        self.audio_queue.get(),
                        timeout=1.0
                    )
                    
                    if audio_chunk is None:  # Sentinel
                        break
                    
                    # Create audio event
                    audio_event = self._create_audio_event(audio_chunk)
                    
                    # Send to AWS
                    await self.aws_websocket.send(audio_event)
                    
                except asyncio.TimeoutError:
                    continue
                    
        except Exception as e:
            logger.error(f"Error sending to AWS: {e}")
        finally:
            # Send end frame
            try:
                if self.aws_websocket:
                    await self.aws_websocket.send(self._create_end_frame())
            except:
                pass
    
    def _create_audio_event(self, audio_data: bytes) -> bytes:
        """Create an audio event in AWS event stream format."""
        # Event stream encoding for audio event
        headers = {
            ':message-type': 'event',
            ':event-type': 'AudioEvent',
            ':content-type': 'application/octet-stream'
        }
        
        return self._encode_event_stream(headers, audio_data)
    
    def _create_end_frame(self) -> bytes:
        """Create an end frame event."""
        headers = {
            ':message-type': 'event',
            ':event-type': 'AudioEvent',
            ':content-type': 'application/octet-stream'
        }
        
        # Empty payload signals end of stream
        return self._encode_event_stream(headers, b'')
    
    def _encode_event_stream(self, headers: Dict[str, str], payload: bytes) -> bytes:
        """Encode a message in AWS event stream format."""
        encoded_headers = self._encode_headers(headers)
        
        # Calculate total length
        total_length = 12 + len(encoded_headers) + len(payload) + 4
        
        # Create prelude
        prelude = struct.pack('!I', total_length)
        prelude += struct.pack('!I', len(encoded_headers))
        
        # Calculate prelude CRC
        prelude_crc = zlib.crc32(prelude) & 0xffffffff
        prelude += struct.pack('!I', prelude_crc)
        
        # Combine message
        message = prelude + encoded_headers + payload
        
        # Calculate message CRC
        message_crc = zlib.crc32(message) & 0xffffffff
        message += struct.pack('!I', message_crc)
        
        return message
    
    def _encode_headers(self, headers: Dict[str, str]) -> bytes:
        """Encode headers for event stream."""
        encoded = b''
        
        for name, value in headers.items():
            # Encode header name
            name_bytes = name.encode('utf-8')
            encoded += struct.pack('!B', len(name_bytes))
            encoded += name_bytes
            
            # Encode header value (string type = 7)
            encoded += struct.pack('!B', 7)
            value_bytes = value.encode('utf-8')
            encoded += struct.pack('!H', len(value_bytes))
            encoded += value_bytes
        
        return encoded
    
    def _parse_event_stream(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Parse AWS event stream message."""
        try:
            if len(data) < 16:  # Minimum message size
                return None
            
            # Parse prelude
            total_length = struct.unpack('!I', data[0:4])[0]
            headers_length = struct.unpack('!I', data[4:8])[0]
            prelude_crc = struct.unpack('!I', data[8:12])[0]
            
            # Verify prelude CRC
            prelude_data = data[0:8]
            calculated_crc = zlib.crc32(prelude_data) & 0xffffffff
            if calculated_crc != prelude_crc:
                logger.warning("Prelude CRC mismatch")
                return None
            
            # Parse headers
            headers = {}
            header_data = data[12:12 + headers_length]
            offset = 0
            
            while offset < headers_length:
                # Parse header name
                name_length = header_data[offset]
                offset += 1
                name = header_data[offset:offset + name_length].decode('utf-8')
                offset += name_length
                
                # Parse header value (assuming string type)
                value_type = header_data[offset]
                offset += 1
                
                if value_type == 7:  # String
                    value_length = struct.unpack('!H', header_data[offset:offset + 2])[0]
                    offset += 2
                    value = header_data[offset:offset + value_length].decode('utf-8')
                    offset += value_length
                    headers[name] = value
            
            # Extract payload
            payload_start = 12 + headers_length
            payload_end = total_length - 4
            payload = data[payload_start:payload_end]
            
            # Check if it's a transcript event
            if headers.get(':event-type') == 'TranscriptEvent':
                return json.loads(payload.decode('utf-8'))
            
            return None
            
        except Exception as e:
            logger.debug(f"Error parsing event stream: {e}")
            return None
    
    async def _process_transcript(self, transcript: Dict[str, Any]):
        """Process transcript from AWS Transcribe Medical."""
        for result in transcript.get('Results', []):
            if not result.get('Alternatives'):
                continue
            
            alternative = result['Alternatives'][0]
            text = alternative.get('Transcript', '')
            
            if not text:
                continue
            
            # Extract medical entities
            entities = []
            for entity in alternative.get('Entities', []):
                entities.append({
                    'text': entity.get('Content'),
                    'category': entity.get('Category'),
                    'confidence': entity.get('Confidence'),
                    'type': entity.get('Type')
                })
            
            # Create message
            message = {
                'type': 'transcript',
                'session_id': self.session_id,
                'transcript': {
                    'text': text,
                    'is_partial': result.get('IsPartial', True),
                    'confidence': alternative.get('Confidence'),
                    'medical_entities': entities
                },
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Add speaker label if present
            if result.get('SpeakerLabel'):
                message['transcript']['speaker'] = result['SpeakerLabel']
            
            # Send to client
            await self.websocket.send_text(json.dumps(message))
            
            # Store final transcripts
            if not result.get('IsPartial'):
                self.transcripts.append({
                    'text': text,
                    'entities': entities,
                    'timestamp': datetime.utcnow().isoformat()
                })
    
    async def send_audio_chunk(self, audio_data: str):
        """Queue audio data for sending to AWS."""
        if not self.running:
            return
        
        try:
            # Decode base64
            audio_bytes = base64.b64decode(audio_data)
            
            # Add to queue
            await self.audio_queue.put(audio_bytes)
            
            # Send acknowledgment
            await self.websocket.send_text(json.dumps({
                "type": "audio_received",
                "session_id": self.session_id,
                "bytes": len(audio_bytes)
            }))
            
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
    
    async def _send_error(self, error_message: str):
        """Send error message to client."""
        try:
            await self.websocket.send_text(json.dumps({
                'type': 'error',
                'error': error_message,
                'session_id': self.session_id,
                'timestamp': datetime.utcnow().isoformat()
            }))
        except:
            pass
    
    async def stop_transcription(self):
        """Stop the transcription session."""
        logger.info(f"Stopping direct medical transcription: {self.session_id}")
        self.running = False
        
        try:
            # Send sentinel
            await self.audio_queue.put(None)
            
            # Close AWS WebSocket
            if self.aws_websocket:
                await self.aws_websocket.close()
            
            # Wait for tasks
            if self._receive_task:
                self._receive_task.cancel()
            if self._send_task:
                await asyncio.wait_for(self._send_task, timeout=5.0)
            
            # Send summary
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                'summary': {
                    'total_transcripts': len(self.transcripts),
                    'medical_entities_found': sum(len(t['entities']) for t in self.transcripts)
                },
                'timestamp': datetime.utcnow().isoformat()
            }))
            
        except Exception as e:
            logger.error(f"Error stopping: {e}")


async def handle_direct_medical_websocket(websocket: WebSocket):
    """Handle WebSocket using direct AWS Transcribe Medical connection."""
    await websocket.accept()
    session = None
    
    try:
        session = DirectMedicalTranscriptionSession(websocket)
        
        await websocket.send_text(json.dumps({
            "type": "info",
            "message": "Connected to direct AWS Transcribe Medical WebSocket service",
            "method": "direct_websocket_implementation"
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get('type') == 'start':
                await session.start_transcription(message.get('config', {}))
            elif message.get('type') == 'audio':
                await session.send_audio_chunk(message.get('data'))
            elif message.get('type') == 'stop':
                await session.stop_transcription()
                break
                
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if session:
            await session._send_error(str(e))
    finally:
        if session and session.running:
            await session.stop_transcription()