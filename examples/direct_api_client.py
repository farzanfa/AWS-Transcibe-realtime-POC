#!/usr/bin/env python3
"""
Example client for testing Direct API implementations of AWS Transcribe Medical.
Supports both WebSocket and HTTP/2 protocols.
"""

import asyncio
import base64
import json
import logging
import sys
import wave
from pathlib import Path
from typing import Optional, Literal

import websockets
import pyaudio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024
AUDIO_FORMAT = pyaudio.paInt16


class DirectAPIMedicalClient:
    """Client for Direct API medical transcription."""
    
    def __init__(self, url: str):
        self.url = url
        self.websocket = None
        self.running = False
        self.transcripts = []
    
    async def connect(self):
        """Connect to the WebSocket server."""
        logger.info(f"Connecting to {self.url}")
        self.websocket = await websockets.connect(self.url)
        
        # Start receiving messages
        self.receive_task = asyncio.create_task(self._receive_messages())
    
    async def disconnect(self):
        """Disconnect from the server."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
        if hasattr(self, 'receive_task'):
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass
    
    async def _receive_messages(self):
        """Receive messages from the server."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._handle_message(data)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        except Exception as e:
            logger.error(f"Error receiving messages: {e}")
    
    async def _handle_message(self, data: dict):
        """Handle received message."""
        msg_type = data.get('type')
        
        if msg_type == 'info':
            logger.info(f"Server info: {data.get('message')}")
            if 'supported_protocols' in data:
                logger.info(f"Supported protocols: {data['supported_protocols']}")
        
        elif msg_type == 'session_started':
            logger.info(f"Session started: {data.get('session_id')}")
            logger.info(f"Config: {json.dumps(data.get('config', {}), indent=2)}")
        
        elif msg_type == 'transcript':
            transcript = data.get('transcript', {})
            text = transcript.get('text', '')
            is_partial = transcript.get('is_partial', True)
            
            if text:
                if is_partial:
                    print(f"\r[PARTIAL] {text}", end='', flush=True)
                else:
                    print(f"\r[FINAL] {text}")
                    self.transcripts.append(text)
                    
                    # Show medical entities if present
                    entities = transcript.get('medical_entities', [])
                    if entities:
                        print("  Medical Entities:")
                        for entity in entities:
                            print(f"    - {entity['text']} ({entity['category']}) - Confidence: {entity.get('confidence', 'N/A')}")
        
        elif msg_type == 'audio_received':
            logger.debug(f"Audio acknowledged: {data.get('bytes')} bytes")
        
        elif msg_type == 'error':
            logger.error(f"Server error: {data.get('error')}")
        
        elif msg_type == 'session_ended':
            logger.info("Session ended")
            summary = data.get('summary', {})
            logger.info(f"Summary: {json.dumps(summary, indent=2)}")
        
        elif msg_type == 'protocol_selected':
            logger.info(f"Active protocol: {data.get('protocol')}")
    
    async def start_transcription(
        self,
        protocol: Optional[Literal["websocket", "http2", "auto"]] = None,
        specialty: str = "PRIMARYCARE",
        type_: str = "CONVERSATION",
        show_speaker_labels: bool = False,
        content_identification: str = "PHI"
    ):
        """Start transcription session."""
        config = {
            'specialty': specialty,
            'type': type_,
            'language_code': 'en-US',
            'sample_rate': SAMPLE_RATE,
            'show_speaker_labels': show_speaker_labels,
            'content_identification_type': content_identification
        }
        
        message = {
            'type': 'start',
            'config': config
        }
        
        # Add protocol preference if specified
        if protocol:
            message['protocol'] = protocol
        
        await self.websocket.send(json.dumps(message))
        self.running = True
        logger.info(f"Started transcription with config: {json.dumps(config, indent=2)}")
    
    async def stop_transcription(self):
        """Stop transcription session."""
        if self.running:
            await self.websocket.send(json.dumps({'type': 'stop'}))
            self.running = False
            logger.info("Stopped transcription")
    
    async def send_audio_chunk(self, audio_data: bytes):
        """Send audio chunk to server."""
        if self.running:
            # Encode to base64
            encoded = base64.b64encode(audio_data).decode('utf-8')
            
            message = {
                'type': 'audio',
                'data': encoded
            }
            
            await self.websocket.send(json.dumps(message))
    
    async def stream_from_microphone(self, duration: Optional[int] = None):
        """Stream audio from microphone."""
        p = pyaudio.PyAudio()
        
        stream = p.open(
            format=AUDIO_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        logger.info("Streaming from microphone... Press Ctrl+C to stop")
        
        try:
            start_time = asyncio.get_event_loop().time()
            
            while self.running:
                # Check duration limit
                if duration and (asyncio.get_event_loop().time() - start_time) > duration:
                    break
                
                # Read audio chunk
                audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                
                # Send to server
                await self.send_audio_chunk(audio_data)
                
                # Small delay to prevent overwhelming
                await asyncio.sleep(0.01)
                
        except KeyboardInterrupt:
            logger.info("Microphone streaming interrupted")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
    
    async def stream_from_file(self, file_path: str):
        """Stream audio from WAV file."""
        with wave.open(file_path, 'rb') as wav_file:
            # Verify format
            if wav_file.getnchannels() != CHANNELS:
                raise ValueError(f"Expected {CHANNELS} channel(s), got {wav_file.getnchannels()}")
            if wav_file.getframerate() != SAMPLE_RATE:
                raise ValueError(f"Expected {SAMPLE_RATE} Hz, got {wav_file.getframerate()}")
            if wav_file.getsampwidth() != 2:  # 16-bit
                raise ValueError(f"Expected 16-bit audio, got {wav_file.getsampwidth()*8}-bit")
            
            logger.info(f"Streaming from file: {file_path}")
            
            # Read and send chunks
            while self.running:
                frames = wav_file.readframes(CHUNK_SIZE)
                if not frames:
                    break
                
                await self.send_audio_chunk(frames)
                
                # Simulate real-time streaming
                chunk_duration = CHUNK_SIZE / SAMPLE_RATE
                await asyncio.sleep(chunk_duration)
            
            logger.info("File streaming complete")


async def test_direct_api(
    endpoint: str = "unified",
    protocol: Optional[str] = None,
    source: str = "microphone",
    file_path: Optional[str] = None,
    duration: Optional[int] = None,
    specialty: str = "PRIMARYCARE"
):
    """Test Direct API implementation."""
    # Determine URL based on endpoint
    base_url = "ws://localhost:8000/ws/medical"
    
    if endpoint == "websocket":
        url = f"{base_url}/direct"
    elif endpoint == "http2":
        url = f"{base_url}/http2"
    elif endpoint == "unified":
        url = f"{base_url}/unified"
    else:
        url = base_url  # Default medical endpoint
    
    # Create client
    client = DirectAPIMedicalClient(url)
    
    try:
        # Connect
        await client.connect()
        
        # Start transcription
        await client.start_transcription(
            protocol=protocol,
            specialty=specialty
        )
        
        # Stream audio
        if source == "microphone":
            await client.stream_from_microphone(duration)
        elif source == "file" and file_path:
            await client.stream_from_file(file_path)
        else:
            raise ValueError("Invalid source or missing file path")
        
        # Stop transcription
        await client.stop_transcription()
        
        # Wait a bit for final messages
        await asyncio.sleep(2)
        
        # Print summary
        print("\n\n=== Transcription Summary ===")
        print(f"Total segments: {len(client.transcripts)}")
        if client.transcripts:
            print("\nFull transcript:")
            print(" ".join(client.transcripts))
        
    finally:
        await client.disconnect()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test Direct API medical transcription")
    parser.add_argument(
        "--endpoint",
        choices=["websocket", "http2", "unified"],
        default="unified",
        help="API endpoint to use"
    )
    parser.add_argument(
        "--protocol",
        choices=["websocket", "http2", "auto"],
        help="Protocol preference (for unified endpoint)"
    )
    parser.add_argument(
        "--source",
        choices=["microphone", "file"],
        default="microphone",
        help="Audio source"
    )
    parser.add_argument(
        "--file",
        help="Path to WAV file (required if source=file)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Duration in seconds (for microphone)"
    )
    parser.add_argument(
        "--specialty",
        choices=["PRIMARYCARE", "CARDIOLOGY", "NEUROLOGY", "ONCOLOGY", "RADIOLOGY", "UROLOGY"],
        default="PRIMARYCARE",
        help="Medical specialty"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.source == "file" and not args.file:
        parser.error("--file is required when source=file")
    
    # Run test
    asyncio.run(test_direct_api(
        endpoint=args.endpoint,
        protocol=args.protocol,
        source=args.source,
        file_path=args.file,
        duration=args.duration,
        specialty=args.specialty
    ))