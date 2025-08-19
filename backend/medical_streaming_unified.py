"""
Unified Direct API implementation for AWS Transcribe Medical streaming.
Supports both HTTP/2 and WebSocket protocols for StartMedicalStreamTranscription.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional, Literal
from fastapi import WebSocket, WebSocketDisconnect

from medical_streaming_direct import DirectMedicalTranscriptionSession, handle_direct_medical_websocket
from medical_streaming_http2 import HTTP2MedicalTranscriptionSession, handle_http2_medical_websocket

logger = logging.getLogger(__name__)

# Protocol types
ProtocolType = Literal["websocket", "http2", "auto"]


class UnifiedMedicalTranscriptionSession:
    """
    Unified session that can use either WebSocket or HTTP/2 protocol
    for AWS Transcribe Medical streaming.
    """
    
    def __init__(self, websocket: WebSocket, protocol: ProtocolType = "auto"):
        self.websocket = websocket
        self.protocol = protocol
        self.session = None
        self.active_protocol = None
    
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """Start transcription using the specified or best available protocol."""
        config = config or {}
        
        # Determine which protocol to use
        if self.protocol == "auto":
            # Try HTTP/2 first, fall back to WebSocket
            try:
                await self._start_http2(config)
                self.active_protocol = "http2"
            except Exception as e:
                logger.warning(f"HTTP/2 failed, falling back to WebSocket: {e}")
                await self._start_websocket(config)
                self.active_protocol = "websocket"
        elif self.protocol == "http2":
            await self._start_http2(config)
            self.active_protocol = "http2"
        else:  # websocket
            await self._start_websocket(config)
            self.active_protocol = "websocket"
    
    async def _start_http2(self, config: Dict[str, Any]):
        """Start using HTTP/2 protocol."""
        self.session = HTTP2MedicalTranscriptionSession(self.websocket)
        await self.session.start_transcription(config)
    
    async def _start_websocket(self, config: Dict[str, Any]):
        """Start using WebSocket protocol."""
        self.session = DirectMedicalTranscriptionSession(self.websocket)
        await self.session.start_transcription(config)
    
    async def send_audio_chunk(self, audio_data: str):
        """Send audio chunk to the active session."""
        if self.session:
            await self.session.send_audio_chunk(audio_data)
    
    async def stop_transcription(self):
        """Stop the active transcription session."""
        if self.session:
            await self.session.stop_transcription()


async def handle_unified_medical_websocket(
    websocket: WebSocket,
    protocol: ProtocolType = "auto"
):
    """
    Handle WebSocket connection with unified protocol support.
    
    Args:
        websocket: FastAPI WebSocket connection
        protocol: Protocol to use - "websocket", "http2", or "auto"
    """
    await websocket.accept()
    session = None
    
    try:
        session = UnifiedMedicalTranscriptionSession(websocket, protocol)
        
        # Send initial info message
        await websocket.send_text(json.dumps({
            "type": "info",
            "message": "Connected to Unified AWS Transcribe Medical Direct API",
            "supported_protocols": ["websocket", "http2"],
            "selected_protocol": protocol,
            "features": {
                "websocket": {
                    "description": "Direct WebSocket connection to AWS Transcribe Medical",
                    "advantages": ["Lower latency", "Simpler protocol", "Wide browser support"]
                },
                "http2": {
                    "description": "HTTP/2 streaming connection to AWS Transcribe Medical",
                    "advantages": ["Better multiplexing", "Header compression", "Server push capable"]
                }
            }
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get('type') == 'start':
                # Start transcription with optional protocol override
                config = message.get('config', {})
                if 'protocol' in message:
                    session.protocol = message['protocol']
                await session.start_transcription(config)
                
                # Notify client of active protocol
                await websocket.send_text(json.dumps({
                    "type": "protocol_selected",
                    "protocol": session.active_protocol
                }))
                
            elif message.get('type') == 'audio':
                await session.send_audio_chunk(message.get('data'))
                
            elif message.get('type') == 'stop':
                await session.stop_transcription()
                break
                
    except WebSocketDisconnect:
        logger.info("Client disconnected")
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
        if session and session.session and hasattr(session.session, 'running') and session.session.running:
            await session.stop_transcription()


# Convenience functions for specific protocol endpoints
async def handle_websocket_only(websocket: WebSocket):
    """Handle WebSocket-only connection."""
    await handle_unified_medical_websocket(websocket, protocol="websocket")


async def handle_http2_only(websocket: WebSocket):
    """Handle HTTP/2-only connection."""
    await handle_unified_medical_websocket(websocket, protocol="http2")


# Export session classes for direct use
__all__ = [
    'UnifiedMedicalTranscriptionSession',
    'handle_unified_medical_websocket',
    'handle_websocket_only',
    'handle_http2_only',
    'DirectMedicalTranscriptionSession',
    'HTTP2MedicalTranscriptionSession'
]