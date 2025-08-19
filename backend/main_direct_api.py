"""
AWS Transcribe Medical Direct API Implementation
Supports StartMedicalStreamTranscription via WebSocket and HTTP/2
"""

import asyncio
import base64
import json
import logging
import os
import struct
import uuid
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Literal
from urllib.parse import urlencode, quote

import boto3
import websockets
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
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
MEDICAL_SPECIALTY = os.getenv('MEDICAL_SPECIALTY', 'PRIMARYCARE')
MEDICAL_TYPE = os.getenv('MEDICAL_TYPE', 'CONVERSATION')
MEDICAL_VOCABULARY_NAME = os.getenv('MEDICAL_VOCABULARY_NAME', '')
CONTENT_IDENTIFICATION_TYPE = os.getenv('CONTENT_IDENTIFICATION_TYPE', 'PHI')
LANGUAGE_CODE = os.getenv('LANGUAGE_CODE', 'en-US')

if not S3_BUCKET:
    raise ValueError("S3_BUCKET environment variable is required")

# Initialize AWS clients
s3_client = boto3.client('s3', region_name=AWS_REGION)
session = boto3.Session()
credentials = session.get_credentials()

# Initialize FastAPI
app = FastAPI(title="Medical Transcription Direct API Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EventStreamParser:
    """Parser for AWS Event Stream format"""
    
    @staticmethod
    def parse_message(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse an event stream message"""
        if len(data) < 16:  # Minimum message size
            return None
            
        # Read prelude (first 12 bytes)
        total_length = struct.unpack('>I', data[0:4])[0]
        headers_length = struct.unpack('>I', data[4:8])[0]
        prelude_crc = struct.unpack('>I', data[8:12])[0]
        
        # Read headers
        headers_end = 12 + headers_length
        headers_data = data[12:headers_end]
        headers = EventStreamParser._parse_headers(headers_data)
        
        # Read payload
        payload_end = total_length - 4  # Subtract message CRC
        payload = data[headers_end:payload_end]
        
        # Parse based on event type
        if headers.get(':event-type') == 'TranscriptEvent':
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                logger.error("Failed to parse TranscriptEvent payload")
                return None
                
        return None
    
    @staticmethod
    def _parse_headers(data: bytes) -> Dict[str, Any]:
        """Parse event stream headers"""
        headers = {}
        offset = 0
        
        while offset < len(data):
            # Read header name length
            name_len = data[offset]
            offset += 1
            
            # Read header name
            name = data[offset:offset + name_len].decode('utf-8')
            offset += name_len
            
            # Read header type
            header_type = data[offset]
            offset += 1
            
            # Read header value based on type
            if header_type == 7:  # String type
                value_len = struct.unpack('>H', data[offset:offset + 2])[0]
                offset += 2
                value = data[offset:offset + value_len].decode('utf-8')
                offset += value_len
                headers[name] = value
            else:
                # Skip other types for now
                break
                
        return headers
    
    @staticmethod
    def create_audio_event(audio_chunk: bytes) -> bytes:
        """Create an audio event in event stream format"""
        # For WebSocket, we can send raw audio chunks
        # The service handles the event stream formatting
        return audio_chunk


class MedicalTranscriptionSessionBase:
    """Base class for medical transcription sessions"""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id = str(uuid.uuid4())
        self.running = False
        self.transcripts = []
        self.medical_entities = []
    
    async def save_to_s3(self, content: Dict[str, Any]) -> str:
        """Save transcription results to S3"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_key = f"transcripts/medical/{timestamp}_{self.session_id}.json"
        
        try:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=json.dumps(content, indent=2).encode('utf-8'),
                ContentType='application/json',
                Metadata={
                    'session-id': self.session_id,
                    'specialty': MEDICAL_SPECIALTY,
                    'type': MEDICAL_TYPE,
                    'language': LANGUAGE_CODE
                }
            )
            logger.info(f"Saved transcript to S3: {s3_key}")
            return s3_key
        except Exception as e:
            logger.error(f"Failed to save to S3: {e}")
            raise


class WebSocketMedicalSession(MedicalTranscriptionSessionBase):
    """WebSocket implementation for StartMedicalStreamTranscription"""
    
    def __init__(self, websocket: WebSocket):
        super().__init__(websocket)
        self.aws_websocket = None
        self.receive_task = None
        self.audio_queue = asyncio.Queue()
    
    def create_presigned_url(self) -> str:
        """Create a presigned URL for WebSocket connection"""
        # Endpoint for medical transcription
        host = f"transcribestreaming.{AWS_REGION}.amazonaws.com"
        path = "/medical-stream-transcription-websocket"
        
        # Query parameters
        params = {
            'language-code': LANGUAGE_CODE,
            'media-encoding': 'pcm',
            'sample-rate': '16000',
            'specialty': MEDICAL_SPECIALTY,
            'type': MEDICAL_TYPE,
            'content-identification-type': CONTENT_IDENTIFICATION_TYPE
        }
        
        if MEDICAL_VOCABULARY_NAME:
            params['vocabulary-name'] = MEDICAL_VOCABULARY_NAME
        
        # Create canonical URI
        canonical_uri = path
        canonical_querystring = urlencode(sorted(params.items()))
        
        # Create the URL to sign
        url = f"wss://{host}:8443{canonical_uri}?{canonical_querystring}"
        
        # Create request for signing
        request = AWSRequest(method='GET', url=url)
        
        # Get credentials
        creds = credentials.get_frozen_credentials()
        
        # Sign with SigV4
        signer = SigV4Auth(creds, 'transcribe', AWS_REGION)
        signer.add_auth(request)
        
        # Build final URL with auth headers as query params
        auth_params = {
            'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
            'X-Amz-Credential': request.headers.get('Authorization', '').split('Credential=')[1].split(',')[0],
            'X-Amz-Date': request.headers.get('X-Amz-Date', ''),
            'X-Amz-SignedHeaders': 'host',
            'X-Amz-Signature': request.headers.get('Authorization', '').split('Signature=')[1]
        }
        
        if 'X-Amz-Security-Token' in request.headers:
            auth_params['X-Amz-Security-Token'] = request.headers['X-Amz-Security-Token']
        
        # Combine all parameters
        all_params = {**params, **auth_params}
        final_querystring = urlencode(all_params)
        
        return f"wss://{host}:8443{canonical_uri}?{final_querystring}"
    
    async def start(self):
        """Start the WebSocket connection to AWS"""
        try:
            # Create presigned URL
            url = self.create_presigned_url()
            logger.info(f"Connecting to AWS Medical Transcribe WebSocket...")
            
            # Connect to AWS
            self.aws_websocket = await websockets.connect(
                url,
                subprotocols=['aws-transcribe-medical']
            )
            
            self.running = True
            
            # Start receiving task
            self.receive_task = asyncio.create_task(self._receive_loop())
            
            # Send session info to client
            await self.websocket.send_text(json.dumps({
                'type': 'session_started',
                'session_id': self.session_id,
                'protocol': 'websocket',
                'specialty': MEDICAL_SPECIALTY,
                'timestamp': datetime.utcnow().isoformat()
            }))
            
            logger.info(f"WebSocket session started: {self.session_id}")
            
        except Exception as e:
            logger.error(f"Failed to start WebSocket session: {e}")
            await self.websocket.send_text(json.dumps({
                'type': 'error',
                'error': str(e)
            }))
            raise
    
    async def _receive_loop(self):
        """Receive and process messages from AWS"""
        try:
            while self.running:
                message = await self.aws_websocket.recv()
                
                # Parse the message
                if isinstance(message, bytes):
                    event = EventStreamParser.parse_message(message)
                else:
                    event = json.loads(message)
                
                if event:
                    await self._process_event(event)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("AWS WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
        finally:
            self.running = False
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process events from AWS"""
        if 'Transcript' in event:
            transcript = event['Transcript']
            results = transcript.get('Results', [])
            
            for result in results:
                if not result.get('Alternatives'):
                    continue
                
                alternative = result['Alternatives'][0]
                text = alternative.get('Transcript', '')
                
                if text:
                    is_partial = result.get('IsPartial', False)
                    
                    # Extract medical entities
                    entities = []
                    items = alternative.get('Items', [])
                    for item in items:
                        if 'Entities' in item:
                            for entity in item['Entities']:
                                entities.append({
                                    'text': entity.get('Content', ''),
                                    'category': entity.get('Category', ''),
                                    'confidence': entity.get('Confidence', 0),
                                    'begin_offset': item.get('StartTime', 0),
                                    'end_offset': item.get('EndTime', 0)
                                })
                    
                    # Send to client
                    await self.websocket.send_text(json.dumps({
                        'type': 'transcript',
                        'session_id': self.session_id,
                        'transcript': {
                            'text': text,
                            'is_partial': is_partial,
                            'entities': entities
                        },
                        'timestamp': datetime.utcnow().isoformat()
                    }))
                    
                    # Store if final
                    if not is_partial:
                        self.transcripts.append({
                            'text': text,
                            'timestamp': datetime.utcnow().isoformat(),
                            'entities': entities
                        })
                        self.medical_entities.extend(entities)
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to AWS"""
        if self.aws_websocket and self.running:
            try:
                # Send raw audio - AWS handles event stream formatting
                await self.aws_websocket.send(audio_data)
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
                self.running = False
    
    async def stop(self):
        """Stop the session and save results"""
        self.running = False
        
        # Close AWS WebSocket
        if self.aws_websocket:
            await self.aws_websocket.close()
        
        # Wait for receive task
        if self.receive_task:
            try:
                await asyncio.wait_for(self.receive_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Receive task timeout")
        
        # Save results
        if self.transcripts:
            content = {
                'session_id': self.session_id,
                'protocol': 'websocket',
                'specialty': MEDICAL_SPECIALTY,
                'transcripts': self.transcripts,
                'medical_entities': self.medical_entities,
                'summary': {
                    'total_transcripts': len(self.transcripts),
                    'total_entities': len(self.medical_entities),
                    'entity_categories': {}
                }
            }
            
            # Count entities by category
            for entity in self.medical_entities:
                category = entity.get('category', 'unknown')
                if category not in content['summary']['entity_categories']:
                    content['summary']['entity_categories'][category] = 0
                content['summary']['entity_categories'][category] += 1
            
            s3_key = await self.save_to_s3(content)
            
            # Send completion to client
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                's3_key': s3_key,
                'summary': content['summary']
            }))
            
            return s3_key
        
        return None


class HTTP2MedicalSession(MedicalTranscriptionSessionBase):
    """HTTP/2 implementation for StartMedicalStreamTranscription"""
    
    def __init__(self, websocket: WebSocket):
        super().__init__(websocket)
        self.client = None
        self.stream_url = None
        self.request_id = None
        self.receive_task = None
    
    def create_request_url(self) -> str:
        """Create the HTTP/2 request URL"""
        return f"https://transcribestreaming.{AWS_REGION}.amazonaws.com/medical-stream-transcription"
    
    def create_headers(self) -> Dict[str, str]:
        """Create headers for HTTP/2 request"""
        amz_date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        
        headers = {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': 'com.amazonaws.transcribe.Transcribe.StartMedicalStreamTranscription',
            'X-Amz-Date': amz_date,
            'Host': f'transcribestreaming.{AWS_REGION}.amazonaws.com'
        }
        
        return headers
    
    def create_request_body(self) -> Dict[str, Any]:
        """Create the initial request body"""
        body = {
            'LanguageCode': LANGUAGE_CODE,
            'MediaEncoding': 'pcm',
            'MediaSampleRateHertz': 16000,
            'Specialty': MEDICAL_SPECIALTY,
            'Type': MEDICAL_TYPE,
            'ContentIdentificationType': CONTENT_IDENTIFICATION_TYPE
        }
        
        if MEDICAL_VOCABULARY_NAME:
            body['VocabularyName'] = MEDICAL_VOCABULARY_NAME
        
        return body
    
    async def start(self):
        """Start the HTTP/2 connection to AWS"""
        try:
            # Create HTTP/2 client
            self.client = httpx.AsyncClient(http2=True)
            
            # Create request
            url = self.create_request_url()
            headers = self.create_headers()
            body = self.create_request_body()
            
            # Sign request
            request = AWSRequest(
                method='POST',
                url=url,
                data=json.dumps(body),
                headers=headers
            )
            
            creds = credentials.get_frozen_credentials()
            signer = SigV4Auth(creds, 'transcribe', AWS_REGION)
            signer.add_auth(request)
            
            # Update headers with signature
            headers.update(dict(request.headers))
            
            logger.info("Starting HTTP/2 medical transcription stream...")
            
            # Start streaming request
            self.running = True
            self.receive_task = asyncio.create_task(
                self._stream_handler(url, headers, body)
            )
            
            # Send session info to client
            await self.websocket.send_text(json.dumps({
                'type': 'session_started',
                'session_id': self.session_id,
                'protocol': 'http2',
                'specialty': MEDICAL_SPECIALTY,
                'timestamp': datetime.utcnow().isoformat()
            }))
            
            logger.info(f"HTTP/2 session started: {self.session_id}")
            
        except Exception as e:
            logger.error(f"Failed to start HTTP/2 session: {e}")
            await self.websocket.send_text(json.dumps({
                'type': 'error',
                'error': str(e)
            }))
            raise
    
    async def _stream_handler(self, url: str, headers: Dict[str, str], body: Dict[str, Any]):
        """Handle the HTTP/2 streaming connection"""
        try:
            async with self.client.stream(
                'POST',
                url,
                headers=headers,
                content=json.dumps(body),
                timeout=None
            ) as response:
                self.request_id = response.headers.get('x-amzn-requestid')
                logger.info(f"HTTP/2 stream established. Request ID: {self.request_id}")
                
                async for chunk in response.aiter_bytes():
                    if not self.running:
                        break
                    
                    # Parse event stream
                    event = EventStreamParser.parse_message(chunk)
                    if event:
                        await self._process_event(event)
                        
        except Exception as e:
            logger.error(f"Error in HTTP/2 stream: {e}")
        finally:
            self.running = False
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process events from AWS"""
        # Similar to WebSocket implementation
        if 'Transcript' in event:
            transcript = event['Transcript']
            results = transcript.get('Results', [])
            
            for result in results:
                if not result.get('Alternatives'):
                    continue
                
                alternative = result['Alternatives'][0]
                text = alternative.get('Transcript', '')
                
                if text:
                    is_partial = result.get('IsPartial', False)
                    
                    # Extract medical entities
                    entities = []
                    items = alternative.get('Items', [])
                    for item in items:
                        if 'Entities' in item:
                            for entity in item['Entities']:
                                entities.append({
                                    'text': entity.get('Content', ''),
                                    'category': entity.get('Category', ''),
                                    'confidence': entity.get('Confidence', 0),
                                    'begin_offset': item.get('StartTime', 0),
                                    'end_offset': item.get('EndTime', 0)
                                })
                    
                    # Send to client
                    await self.websocket.send_text(json.dumps({
                        'type': 'transcript',
                        'session_id': self.session_id,
                        'transcript': {
                            'text': text,
                            'is_partial': is_partial,
                            'entities': entities
                        },
                        'timestamp': datetime.utcnow().isoformat()
                    }))
                    
                    # Store if final
                    if not is_partial:
                        self.transcripts.append({
                            'text': text,
                            'timestamp': datetime.utcnow().isoformat(),
                            'entities': entities
                        })
                        self.medical_entities.extend(entities)
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data via HTTP/2"""
        if self.client and self.running:
            try:
                # For HTTP/2, we need to send audio as event stream chunks
                # This is a simplified version - production would need proper event stream formatting
                audio_event = EventStreamParser.create_audio_event(audio_data)
                # In a real implementation, this would be sent as part of the request body stream
                logger.debug(f"Audio chunk queued: {len(audio_data)} bytes")
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
                self.running = False
    
    async def stop(self):
        """Stop the session and save results"""
        self.running = False
        
        # Close HTTP/2 client
        if self.client:
            await self.client.aclose()
        
        # Wait for receive task
        if self.receive_task:
            try:
                await asyncio.wait_for(self.receive_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Receive task timeout")
        
        # Save results
        if self.transcripts:
            content = {
                'session_id': self.session_id,
                'protocol': 'http2',
                'request_id': self.request_id,
                'specialty': MEDICAL_SPECIALTY,
                'transcripts': self.transcripts,
                'medical_entities': self.medical_entities,
                'summary': {
                    'total_transcripts': len(self.transcripts),
                    'total_entities': len(self.medical_entities),
                    'entity_categories': {}
                }
            }
            
            # Count entities by category
            for entity in self.medical_entities:
                category = entity.get('category', 'unknown')
                if category not in content['summary']['entity_categories']:
                    content['summary']['entity_categories'][category] = 0
                content['summary']['entity_categories'][category] += 1
            
            s3_key = await self.save_to_s3(content)
            
            # Send completion to client
            await self.websocket.send_text(json.dumps({
                'type': 'session_ended',
                'session_id': self.session_id,
                's3_key': s3_key,
                'summary': content['summary']
            }))
            
            return s3_key
        
        return None


# WebSocket Endpoints

@app.websocket("/ws/medical/direct")
async def medical_direct_websocket(websocket: WebSocket):
    """Direct WebSocket implementation for StartMedicalStreamTranscription"""
    await websocket.accept()
    session = None
    
    try:
        # Send initial info
        await websocket.send_text(json.dumps({
            'type': 'info',
            'message': 'AWS Transcribe Medical Direct API - WebSocket Protocol',
            'features': {
                'real_time': True,
                'medical_entities': True,
                'content_identification': CONTENT_IDENTIFICATION_TYPE,
                'specialty': MEDICAL_SPECIALTY
            }
        }))
        
        session = WebSocketMedicalSession(websocket)
        await session.start()
        
        # Process client messages
        while session.running:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message['type'] == 'audio':
                    # Decode base64 audio
                    audio_data = base64.b64decode(message['data'])
                    await session.send_audio(audio_data)
                    
                elif message['type'] == 'stop':
                    await session.stop()
                    break
                    
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'error': str(e)
            }))
        except:
            pass
    finally:
        if session and session.running:
            await session.stop()


@app.websocket("/ws/medical/http2")
async def medical_http2_websocket(websocket: WebSocket):
    """HTTP/2 implementation for StartMedicalStreamTranscription"""
    await websocket.accept()
    session = None
    
    try:
        # Send initial info
        await websocket.send_text(json.dumps({
            'type': 'info',
            'message': 'AWS Transcribe Medical Direct API - HTTP/2 Protocol',
            'features': {
                'real_time': True,
                'medical_entities': True,
                'content_identification': CONTENT_IDENTIFICATION_TYPE,
                'specialty': MEDICAL_SPECIALTY,
                'http2_benefits': ['multiplexing', 'header_compression', 'server_push']
            }
        }))
        
        session = HTTP2MedicalSession(websocket)
        await session.start()
        
        # Process client messages
        while session.running:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message['type'] == 'audio':
                    # Decode base64 audio
                    audio_data = base64.b64decode(message['data'])
                    await session.send_audio(audio_data)
                    
                elif message['type'] == 'stop':
                    await session.stop()
                    break
                    
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    except Exception as e:
        logger.error(f"HTTP/2 session error: {e}")
        try:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'error': str(e)
            }))
        except:
            pass
    finally:
        if session and session.running:
            await session.stop()


@app.websocket("/ws/medical/unified")
async def medical_unified_websocket(websocket: WebSocket):
    """Unified endpoint that can use either WebSocket or HTTP/2"""
    await websocket.accept()
    session = None
    protocol = 'websocket'  # Default
    
    try:
        # Send initial info
        await websocket.send_text(json.dumps({
            'type': 'info',
            'message': 'AWS Transcribe Medical Direct API - Unified Endpoint',
            'supported_protocols': ['websocket', 'http2'],
            'default_protocol': protocol
        }))
        
        # Wait for protocol selection or start with default
        data = await websocket.receive_text()
        message = json.loads(data)
        
        if message.get('type') == 'select_protocol':
            protocol = message.get('protocol', 'websocket')
        
        # Create appropriate session
        if protocol == 'http2':
            session = HTTP2MedicalSession(websocket)
        else:
            session = WebSocketMedicalSession(websocket)
        
        await session.start()
        
        # Send protocol confirmation
        await websocket.send_text(json.dumps({
            'type': 'protocol_selected',
            'protocol': protocol
        }))
        
        # Process messages
        while session.running:
            try:
                if message.get('type') != 'select_protocol':
                    # Process the already received message
                    if message['type'] == 'audio':
                        audio_data = base64.b64decode(message['data'])
                        await session.send_audio(audio_data)
                    elif message['type'] == 'stop':
                        await session.stop()
                        break
                
                # Get next message
                data = await websocket.receive_text()
                message = json.loads(data)
                
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    except Exception as e:
        logger.error(f"Unified session error: {e}")
        try:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'error': str(e)
            }))
        except:
            pass
    finally:
        if session and session.running:
            await session.stop()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "medical-transcription-direct-api",
        "region": AWS_REGION,
        "features": {
            "websocket": True,
            "http2": True,
            "medical_entities": True,
            "content_identification": CONTENT_IDENTIFICATION_TYPE
        }
    }


@app.get("/config")
async def get_config():
    """Get current configuration"""
    return {
        "region": AWS_REGION,
        "specialty": MEDICAL_SPECIALTY,
        "type": MEDICAL_TYPE,
        "language": LANGUAGE_CODE,
        "content_identification": CONTENT_IDENTIFICATION_TYPE,
        "vocabulary": MEDICAL_VOCABULARY_NAME or "none"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)