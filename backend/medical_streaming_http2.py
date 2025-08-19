"""
Direct HTTP/2 implementation of AWS Transcribe Medical streaming.
Provides access to StartMedicalStreamTranscription API using HTTP/2 protocol.
"""

import asyncio
import base64
import json
import logging
import os
import struct
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, AsyncGenerator, Tuple
from urllib.parse import urlencode
import zlib

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')

# AWS Event Stream Message Types
MESSAGE_TYPE_EVENT = 0x00
MESSAGE_TYPE_ERROR = 0x01
MESSAGE_TYPE_EXCEPTION = 0x02

# Event Types
EVENT_TYPE_AUDIO = 'AudioEvent'
EVENT_TYPE_TRANSCRIPT = 'TranscriptEvent'


class EventStreamEncoder:
    """Encodes messages for AWS Event Stream protocol."""
    
    @staticmethod
    def _encode_headers(headers: Dict[str, Any]) -> bytes:
        """Encode headers for event stream."""
        encoded = b''
        
        for name, value in headers.items():
            # Encode header name
            name_bytes = name.encode('utf-8')
            encoded += struct.pack('!B', len(name_bytes))
            encoded += name_bytes
            
            # Encode header value based on type
            if isinstance(value, str):
                # String type (7)
                encoded += struct.pack('!B', 7)
                value_bytes = value.encode('utf-8')
                encoded += struct.pack('!H', len(value_bytes))
                encoded += value_bytes
            elif isinstance(value, bytes):
                # Byte array type (6)
                encoded += struct.pack('!B', 6)
                encoded += struct.pack('!H', len(value))
                encoded += value
            elif isinstance(value, int):
                # Integer type (3)
                encoded += struct.pack('!B', 3)
                encoded += struct.pack('!i', value)
            elif isinstance(value, bool):
                # Boolean type (0 or 1)
                encoded += struct.pack('!B', 0 if not value else 1)
            
        return encoded
    
    @staticmethod
    def encode_audio_event(audio_chunk: bytes) -> bytes:
        """Encode an audio event for AWS Event Stream."""
        headers = {
            ':message-type': 'event',
            ':event-type': EVENT_TYPE_AUDIO,
            ':content-type': 'application/octet-stream'
        }
        
        return EventStreamEncoder.encode_event(headers, audio_chunk)
    
    @staticmethod
    def encode_event(headers: Dict[str, str], payload: bytes = b'') -> bytes:
        """Encode a complete event stream message."""
        # Encode headers
        encoded_headers = EventStreamEncoder._encode_headers(headers)
        
        # Calculate total length
        total_length = 12 + len(encoded_headers) + len(payload) + 4  # 12 byte prelude + headers + payload + 4 byte CRC
        
        # Create prelude
        prelude = struct.pack('!I', total_length)  # Total length
        prelude += struct.pack('!I', len(encoded_headers))  # Headers length
        
        # Calculate prelude CRC
        prelude_crc = zlib.crc32(prelude) & 0xffffffff
        prelude += struct.pack('!I', prelude_crc)
        
        # Combine message
        message = prelude + encoded_headers + payload
        
        # Calculate message CRC
        message_crc = zlib.crc32(message) & 0xffffffff
        message += struct.pack('!I', message_crc)
        
        return message


class EventStreamDecoder:
    """Decodes messages from AWS Event Stream protocol."""
    
    @staticmethod
    def decode_event(data: bytes) -> Tuple[Dict[str, Any], bytes]:
        """Decode an event stream message."""
        if len(data) < 16:  # Minimum message size
            raise ValueError("Insufficient data for event stream message")
        
        # Parse prelude
        total_length = struct.unpack('!I', data[0:4])[0]
        headers_length = struct.unpack('!I', data[4:8])[0]
        prelude_crc = struct.unpack('!I', data[8:12])[0]
        
        # Verify prelude CRC
        prelude_data = data[0:8]
        calculated_crc = zlib.crc32(prelude_data) & 0xffffffff
        if calculated_crc != prelude_crc:
            raise ValueError("Prelude CRC mismatch")
        
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
            
            # Parse header value
            value_type = header_data[offset]
            offset += 1
            
            if value_type == 7:  # String
                value_length = struct.unpack('!H', header_data[offset:offset + 2])[0]
                offset += 2
                value = header_data[offset:offset + value_length].decode('utf-8')
                offset += value_length
            elif value_type == 6:  # Byte array
                value_length = struct.unpack('!H', header_data[offset:offset + 2])[0]
                offset += 2
                value = header_data[offset:offset + value_length]
                offset += value_length
            elif value_type == 3:  # Integer
                value = struct.unpack('!i', header_data[offset:offset + 4])[0]
                offset += 4
            elif value_type in (0, 1):  # Boolean
                value = value_type == 1
            else:
                raise ValueError(f"Unknown header value type: {value_type}")
            
            headers[name] = value
        
        # Extract payload
        payload_start = 12 + headers_length
        payload_end = total_length - 4  # Exclude message CRC
        payload = data[payload_start:payload_end]
        
        # Verify message CRC
        message_data = data[0:total_length - 4]
        message_crc = struct.unpack('!I', data[total_length - 4:total_length])[0]
        calculated_crc = zlib.crc32(message_data) & 0xffffffff
        if calculated_crc != message_crc:
            raise ValueError("Message CRC mismatch")
        
        return headers, payload


class HTTP2MedicalTranscriptionSession:
    """HTTP/2 implementation for AWS Transcribe Medical streaming."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.client = None
        self.stream = None
        self.running = False
        self.transcripts = []
        self.audio_queue = asyncio.Queue()
        self._send_task = None
        self._receive_task = None
        
        # AWS credentials
        self.session = boto3.Session()
        self.credentials = self.session.get_credentials()
    
    def _create_signed_headers(self, method: str, url: str, headers: Dict[str, str], body: bytes = b'') -> Dict[str, str]:
        """Create signed headers for AWS request."""
        # Create AWS request
        request = AWSRequest(method=method, url=url, data=body, headers=headers)
        
        # Sign the request
        signer = SigV4Auth(self.credentials, 'transcribe', AWS_REGION)
        signer.add_auth(request)
        
        return dict(request.headers)
    
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """Start AWS Transcribe Medical streaming via HTTP/2."""
        try:
            config = config or {}
            
            logger.info(f"Starting HTTP/2 medical transcription - Session: {self.session_id}")
            
            # Prepare parameters
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
            
            # Create URL
            base_url = f"https://transcribestreaming.{AWS_REGION}.amazonaws.com"
            path = "/medical-stream-transcription-http2"
            query_string = urlencode(params)
            full_url = f"{base_url}{path}?{query_string}"
            
            # Prepare headers
            headers = {
                'content-type': 'application/vnd.amazon.eventstream',
                'x-amzn-transcribe-language-code': params['language-code'],
                'x-amzn-transcribe-sample-rate': params['sample-rate'],
                'x-amzn-transcribe-media-encoding': params['media-encoding'],
                'x-amzn-transcribe-specialty': params['specialty'],
                'x-amzn-transcribe-type': params['type']
            }
            
            # Sign the request
            signed_headers = self._create_signed_headers('POST', full_url, headers)
            
            # Create HTTP/2 client
            self.client = httpx.AsyncClient(http2=True)
            
            # Start streaming request
            self.stream = self.client.stream(
                'POST',
                full_url,
                headers=signed_headers,
                timeout=None
            )
            
            self.running = True
            
            # Start send and receive tasks
            self._send_task = asyncio.create_task(self._send_audio_stream())
            self._receive_task = asyncio.create_task(self._receive_transcripts())
            
            # Send session started message
            await self.websocket.send_text(json.dumps({
                "type": "session_started",
                "session_id": self.session_id,
                "config": config,
                "method": "http2",
                "timestamp": datetime.utcnow().isoformat()
            }))
            
            logger.info("HTTP/2 medical stream connection established")
            
        except Exception as e:
            error_msg = f"Failed to start HTTP/2 medical transcription: {e}"
            logger.error(error_msg)
            await self._send_error(error_msg)
            raise
    
    async def _send_audio_stream(self):
        """Send audio data stream via HTTP/2."""
        encoder = EventStreamEncoder()
        
        try:
            async with self.stream as response:
                while self.running:
                    try:
                        # Get audio chunk from queue
                        audio_chunk = await asyncio.wait_for(
                            self.audio_queue.get(),
                            timeout=1.0
                        )
                        
                        if audio_chunk is None:  # Sentinel
                            break
                        
                        # Encode audio event
                        event_data = encoder.encode_audio_event(audio_chunk)
                        
                        # Send via HTTP/2 stream
                        await response.awrite(event_data)
                        
                    except asyncio.TimeoutError:
                        continue
                
                # Send empty audio event to signal end
                end_event = encoder.encode_audio_event(b'')
                await response.awrite(end_event)
                
        except Exception as e:
            logger.error(f"Error in audio sender: {e}")
        finally:
            self.running = False
    
    async def _receive_transcripts(self):
        """Receive and process transcripts from HTTP/2 stream."""
        decoder = EventStreamDecoder()
        buffer = b''
        
        try:
            async with self.stream as response:
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    
                    # Process complete messages in buffer
                    while len(buffer) >= 16:  # Minimum message size
                        try:
                            # Try to decode a message
                            headers, payload = decoder.decode_event(buffer)
                            
                            # Process the event
                            if headers.get(':event-type') == EVENT_TYPE_TRANSCRIPT:
                                await self._process_transcript_event(payload)
                            elif headers.get(':message-type') == 'error':
                                error_msg = payload.decode('utf-8')
                                logger.error(f"Stream error: {error_msg}")
                                await self._send_error(error_msg)
                            
                            # Calculate message length and remove from buffer
                            total_length = struct.unpack('!I', buffer[0:4])[0]
                            buffer = buffer[total_length:]
                            
                        except ValueError as e:
                            # Not enough data or corrupt message
                            logger.debug(f"Decode error: {e}")
                            break
                    
        except Exception as e:
            logger.error(f"Error receiving transcripts: {e}")
        finally:
            self.running = False
    
    async def _process_transcript_event(self, payload: bytes):
        """Process a transcript event payload."""
        try:
            # Parse JSON payload
            transcript_data = json.loads(payload.decode('utf-8'))
            
            for result in transcript_data.get('results', []):
                if not result.get('alternatives'):
                    continue
                
                alternative = result['alternatives'][0]
                text = alternative.get('transcript', '')
                
                if not text:
                    continue
                
                # Extract medical entities
                entities = []
                for entity in alternative.get('entities', []):
                    entities.append({
                        'text': entity.get('content'),
                        'category': entity.get('category'),
                        'confidence': entity.get('confidence'),
                        'type': entity.get('type')
                    })
                
                # Create message
                message = {
                    'type': 'transcript',
                    'session_id': self.session_id,
                    'transcript': {
                        'text': text,
                        'is_partial': result.get('is_partial', True),
                        'confidence': alternative.get('confidence'),
                        'medical_entities': entities
                    },
                    'timestamp': datetime.utcnow().isoformat()
                }
                
                # Add speaker label if present
                if result.get('speaker_label'):
                    message['transcript']['speaker'] = result['speaker_label']
                
                # Send to client
                await self.websocket.send_text(json.dumps(message))
                
                # Store final transcripts
                if not result.get('is_partial'):
                    self.transcripts.append({
                        'text': text,
                        'entities': entities,
                        'timestamp': datetime.utcnow().isoformat()
                    })
                    
        except Exception as e:
            logger.error(f"Error processing transcript: {e}")
    
    async def send_audio_chunk(self, audio_data: str):
        """Queue audio data for sending via HTTP/2."""
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
        logger.info(f"Stopping HTTP/2 medical transcription: {self.session_id}")
        self.running = False
        
        try:
            # Send sentinel
            await self.audio_queue.put(None)
            
            # Wait for tasks
            if self._send_task:
                await asyncio.wait_for(self._send_task, timeout=5.0)
            if self._receive_task:
                await asyncio.wait_for(self._receive_task, timeout=5.0)
            
            # Close HTTP/2 client
            if self.client:
                await self.client.aclose()
            
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


async def handle_http2_medical_websocket(websocket: WebSocket):
    """Handle WebSocket using HTTP/2 AWS Transcribe Medical connection."""
    await websocket.accept()
    session = None
    
    try:
        session = HTTP2MedicalTranscriptionSession(websocket)
        
        await websocket.send_text(json.dumps({
            "type": "info",
            "message": "Connected to HTTP/2 AWS Transcribe Medical streaming service",
            "method": "http2_implementation"
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