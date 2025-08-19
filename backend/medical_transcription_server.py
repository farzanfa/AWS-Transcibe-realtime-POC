"""
Medical Transcription Server
Client -> WebSocket -> Backend -> AWS Transcribe Medical API -> Real-time Transcription
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import boto3
import websockets
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# AWS Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
S3_BUCKET = os.getenv('S3_BUCKET', 'medical-transcripts')

# Initialize AWS clients
session = boto3.Session()
credentials = session.get_credentials()
s3_client = boto3.client('s3', region_name=AWS_REGION)

# FastAPI app
app = FastAPI(title="Medical Transcription Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MedicalTranscriptionSession:
    """Manages a medical transcription session between client and AWS"""
    
    def __init__(self, client_websocket: WebSocket):
        self.client_ws = client_websocket
        self.aws_ws = None
        self.session_id = str(uuid.uuid4())
        self.is_active = False
        self.transcripts = []
        self.medical_entities = []
        
        # Session configuration
        self.config = {
            'language_code': 'en-US',
            'sample_rate': 16000,
            'medical_specialty': 'PRIMARYCARE',
            'type': 'CONVERSATION',
            'content_identification_type': 'PHI'
        }
        
    def _create_aws_websocket_url(self) -> str:
        """Create pre-signed WebSocket URL for AWS Transcribe Medical"""
        # Build the URL
        host = f"transcribestreaming.{AWS_REGION}.amazonaws.com"
        path = "/medical-stream-transcription-websocket"
        
        # Query parameters
        params = {
            'language-code': self.config['language_code'],
            'media-encoding': 'pcm',
            'sample-rate': str(self.config['sample_rate']),
            'specialty': self.config['medical_specialty'],
            'type': self.config['type'],
            'content-identification-type': self.config['content_identification_type']
        }
        
        # Create query string
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        url = f"wss://{host}:8443{path}?{query_string}"
        
        # Sign the request
        request = AWSRequest(method='GET', url=url)
        creds = credentials.get_frozen_credentials()
        signer = SigV4Auth(creds, 'transcribe', AWS_REGION)
        signer.add_auth(request)
        
        # Add auth parameters to URL
        auth_params = []
        if 'Authorization' in request.headers:
            auth = request.headers['Authorization']
            # Extract signature components
            if 'Credential=' in auth:
                cred = auth.split('Credential=')[1].split(',')[0]
                auth_params.append(f"X-Amz-Credential={cred}")
            if 'SignedHeaders=' in auth:
                signed = auth.split('SignedHeaders=')[1].split(',')[0]
                auth_params.append(f"X-Amz-SignedHeaders={signed}")
            if 'Signature=' in auth:
                sig = auth.split('Signature=')[1]
                auth_params.append(f"X-Amz-Signature={sig}")
        
        if 'X-Amz-Date' in request.headers:
            auth_params.append(f"X-Amz-Date={request.headers['X-Amz-Date']}")
        
        if 'X-Amz-Security-Token' in request.headers:
            auth_params.append(f"X-Amz-Security-Token={request.headers['X-Amz-Security-Token']}")
        
        auth_params.append("X-Amz-Algorithm=AWS4-HMAC-SHA256")
        
        # Combine all parameters
        all_params = query_string + '&' + '&'.join(auth_params)
        signed_url = f"wss://{host}:8443{path}?{all_params}"
        
        return signed_url
    
    async def start(self):
        """Start the transcription session"""
        try:
            # Connect to AWS Transcribe Medical
            url = self._create_aws_websocket_url()
            logger.info(f"Connecting to AWS Transcribe Medical for session {self.session_id}")
            
            self.aws_ws = await websockets.connect(url)
            self.is_active = True
            
            # Send session started to client
            await self.client_ws.send_text(json.dumps({
                'type': 'session_started',
                'session_id': self.session_id,
                'config': self.config
            }))
            
            # Start listening to AWS responses
            asyncio.create_task(self._listen_to_aws())
            
            logger.info(f"Session {self.session_id} started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            await self.client_ws.send_text(json.dumps({
                'type': 'error',
                'message': f'Failed to connect to AWS: {str(e)}'
            }))
            raise
    
    async def _listen_to_aws(self):
        """Listen for responses from AWS and forward to client"""
        try:
            while self.is_active and self.aws_ws:
                message = await self.aws_ws.recv()
                
                # Parse AWS response
                if isinstance(message, bytes):
                    # Binary message - try to parse as JSON
                    try:
                        data = json.loads(message.decode('utf-8'))
                    except:
                        logger.debug("Received binary data from AWS")
                        continue
                else:
                    data = json.loads(message)
                
                # Process transcript event
                if 'Transcript' in data:
                    await self._process_transcript(data['Transcript'])
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"AWS connection closed for session {self.session_id}")
        except Exception as e:
            logger.error(f"Error in AWS listener: {e}")
        finally:
            self.is_active = False
    
    async def _process_transcript(self, transcript_data: Dict[str, Any]):
        """Process transcript from AWS and send to client"""
        results = transcript_data.get('Results', [])
        
        for result in results:
            if not result.get('Alternatives'):
                continue
            
            alternative = result['Alternatives'][0]
            text = alternative.get('Transcript', '')
            
            if not text:
                continue
            
            is_partial = result.get('IsPartial', False)
            
            # Extract medical entities
            entities = []
            items = alternative.get('Items', [])
            for item in items:
                if 'Entities' in item:
                    for entity in item['Entities']:
                        entity_data = {
                            'text': entity.get('Content', ''),
                            'category': entity.get('Category', ''),
                            'confidence': entity.get('Confidence', 0)
                        }
                        entities.append(entity_data)
                        if not is_partial:
                            self.medical_entities.append(entity_data)
            
            # Send to client
            await self.client_ws.send_text(json.dumps({
                'type': 'transcript',
                'text': text,
                'is_partial': is_partial,
                'entities': entities,
                'timestamp': datetime.utcnow().isoformat()
            }))
            
            # Store final transcripts
            if not is_partial:
                self.transcripts.append({
                    'text': text,
                    'timestamp': datetime.utcnow().isoformat(),
                    'entities': entities
                })
    
    async def send_audio(self, audio_data: bytes):
        """Send audio from client to AWS"""
        if self.is_active and self.aws_ws:
            try:
                # AWS expects raw PCM audio
                await self.aws_ws.send(audio_data)
            except Exception as e:
                logger.error(f"Error sending audio to AWS: {e}")
                self.is_active = False
    
    async def stop(self):
        """Stop the transcription session"""
        self.is_active = False
        
        # Close AWS connection
        if self.aws_ws:
            await self.aws_ws.close()
        
        # Save session data to S3
        s3_key = await self._save_session()
        
        # Send session ended to client
        await self.client_ws.send_text(json.dumps({
            'type': 'session_ended',
            'session_id': self.session_id,
            's3_key': s3_key,
            'summary': {
                'total_transcripts': len(self.transcripts),
                'total_entities': len(self.medical_entities),
                'duration': len(self.transcripts)  # Approximate
            }
        }))
        
        logger.info(f"Session {self.session_id} ended")
    
    async def _save_session(self) -> Optional[str]:
        """Save session data to S3"""
        if not self.transcripts:
            return None
        
        try:
            # Prepare session data
            session_data = {
                'session_id': self.session_id,
                'timestamp': datetime.utcnow().isoformat(),
                'config': self.config,
                'transcripts': self.transcripts,
                'medical_entities': self.medical_entities,
                'full_text': ' '.join([t['text'] for t in self.transcripts])
            }
            
            # Save to S3
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            s3_key = f"sessions/{timestamp}_{self.session_id}.json"
            
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=json.dumps(session_data, indent=2),
                ContentType='application/json'
            )
            
            logger.info(f"Session saved to S3: {s3_key}")
            return s3_key
            
        except Exception as e:
            logger.error(f"Failed to save session to S3: {e}")
            return None


@app.websocket("/ws/medical")
async def medical_transcription_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for medical transcription
    Flow: Client -> This WebSocket -> AWS Transcribe Medical -> Real-time results
    """
    await websocket.accept()
    session = None
    
    try:
        logger.info("New client connected")
        
        # Send welcome message
        await websocket.send_text(json.dumps({
            'type': 'connected',
            'message': 'Connected to Medical Transcription Server'
        }))
        
        # Create session
        session = MedicalTranscriptionSession(websocket)
        await session.start()
        
        # Handle client messages
        while session.is_active:
            try:
                # Receive message from client
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message['type'] == 'audio':
                    # Decode base64 audio and forward to AWS
                    audio_bytes = base64.b64decode(message['data'])
                    await session.send_audio(audio_bytes)
                    
                elif message['type'] == 'stop':
                    # Stop transcription
                    await session.stop()
                    break
                    
                elif message['type'] == 'config':
                    # Update session configuration
                    session.config.update(message.get('config', {}))
                    logger.info(f"Updated config: {session.config}")
                    
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"Error handling client message: {e}")
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': str(e)
            }))
        except:
            pass
    finally:
        # Ensure session is properly closed
        if session and session.is_active:
            await session.stop()
        logger.info("WebSocket connection closed")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Medical Transcription Server",
        "status": "healthy",
        "endpoints": {
            "websocket": "/ws/medical",
            "health": "/"
        }
    }


if __name__ == "__main__":
    import uvicorn
    
    # Check for required environment variables
    if not os.getenv('AWS_ACCESS_KEY_ID'):
        logger.warning("AWS_ACCESS_KEY_ID not set - using default credentials")
    
    if not os.getenv('S3_BUCKET'):
        logger.warning(f"S3_BUCKET not set - using default: {S3_BUCKET}")
    
    logger.info(f"Starting Medical Transcription Server")
    logger.info(f"AWS Region: {AWS_REGION}")
    logger.info(f"S3 Bucket: {S3_BUCKET}")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)