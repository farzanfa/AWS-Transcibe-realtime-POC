#!/usr/bin/env python3
"""
Unified Medical Transcription Client
Supports both web server for HTML clients and Python streaming client
"""

import asyncio
import base64
import json
import logging
import sys
import os
import wave
import http.server
import socketserver
from typing import Optional, Dict, Any
from datetime import datetime

# Optional imports for Python client mode
try:
    import pyaudio
    import websockets
    import numpy as np
    AUDIO_LIBS_AVAILABLE = True
except ImportError:
    AUDIO_LIBS_AVAILABLE = False
    print("Note: pyaudio, websockets, or numpy not available. Python streaming client disabled.")
    print("To enable: pip install pyaudio websockets numpy")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MedicalStreamingClient:
    """Client for real-time medical transcription via WebSocket."""
    
    def __init__(self, websocket_url: str = "ws://localhost:8000/ws/medical"):
        if not AUDIO_LIBS_AVAILABLE:
            raise ImportError("Required audio libraries not available. Install with: pip install pyaudio websockets numpy")
            
        self.websocket_url = websocket_url
        self.websocket = None
        self.audio = None
        self.stream = None
        self.is_recording = False
        self.session_id = None
        self.transcripts = []
        self.medical_entities = {}
        
        # Audio configuration
        self.chunk_size = 1024
        self.audio_format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 16000
        
    async def connect(self):
        """Connect to the WebSocket server."""
        try:
            self.websocket = await websockets.connect(self.websocket_url)
            logger.info(f"Connected to {self.websocket_url}")
            
            # Start listening for messages
            asyncio.create_task(self._listen())
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise
    
    async def _listen(self):
        """Listen for messages from the server."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._handle_message(data)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error in listener: {e}")
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming messages from the server."""
        msg_type = data.get('type')
        
        if msg_type == 'session_started':
            self.session_id = data.get('session_id')
            logger.info(f"Session started: {self.session_id}")
            
        elif msg_type == 'transcript':
            transcript_data = data.get('transcript', {})
            text = transcript_data.get('text', '')
            is_partial = transcript_data.get('is_partial', True)
            
            # Display transcript
            if is_partial:
                print(f"\r[PARTIAL] {text}", end='', flush=True)
            else:
                print(f"\r[FINAL] {text}")
                self.transcripts.append(text)
            
            # Handle medical entities
            entities = transcript_data.get('entities', [])
            for entity in entities:
                category = entity.get('category', 'Unknown')
                if category not in self.medical_entities:
                    self.medical_entities[category] = []
                self.medical_entities[category].append({
                    'text': entity.get('text'),
                    'confidence': entity.get('confidence')
                })
            
        elif msg_type == 'session_ended':
            s3_key = data.get('s3_key')
            logger.info(f"Session ended. Transcript saved to: {s3_key}")
            self._print_summary()
            
        elif msg_type == 'error':
            error = data.get('error', 'Unknown error')
            logger.error(f"Server error: {error}")
    
    def _print_summary(self):
        """Print session summary."""
        print("\n" + "="*50)
        print("SESSION SUMMARY")
        print("="*50)
        
        print(f"\nTotal transcripts: {len(self.transcripts)}")
        
        if self.medical_entities:
            print("\nMedical Entities Detected:")
            for category, entities in self.medical_entities.items():
                print(f"\n{category}:")
                unique_entities = {}
                for entity in entities:
                    text = entity['text']
                    if text not in unique_entities:
                        unique_entities[text] = entity['confidence']
                    else:
                        unique_entities[text] = max(unique_entities[text], entity['confidence'])
                
                for text, confidence in unique_entities.items():
                    print(f"  - {text} (confidence: {confidence:.2f})")
        
        print("\n" + "="*50)
    
    async def start_recording(self):
        """Start recording and streaming audio."""
        if not self.websocket:
            raise RuntimeError("Not connected to server")
        
        self.is_recording = True
        self.audio = pyaudio.PyAudio()
        
        # Open audio stream
        self.stream = self.audio.open(
            format=self.audio_format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )
        
        logger.info("Recording started...")
        
        # Start streaming
        await self._stream_audio()
    
    async def _stream_audio(self):
        """Stream audio to the server."""
        try:
            while self.is_recording:
                # Read audio chunk
                audio_chunk = self.stream.read(self.chunk_size, exception_on_overflow=False)
                
                # Convert to base64
                audio_base64 = base64.b64encode(audio_chunk).decode('utf-8')
                
                # Send to server
                message = {
                    'type': 'audio',
                    'payload': audio_base64
                }
                
                await self.websocket.send(json.dumps(message))
                
                # Small delay to prevent overwhelming the connection
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"Error streaming audio: {e}")
            self.is_recording = False
    
    async def stop_recording(self):
        """Stop recording and close the stream."""
        self.is_recording = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
        if self.audio:
            self.audio.terminate()
        
        if self.websocket:
            # Send stop message
            message = {
                'type': 'control',
                'action': 'stop'
            }
            await self.websocket.send(json.dumps(message))
        
        logger.info("Recording stopped")
    
    async def disconnect(self):
        """Disconnect from the server."""
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from server")
    
    async def run_interactive(self):
        """Run an interactive session."""
        print("\n🎤 Medical Transcription Client")
        print("="*50)
        print("Commands:")
        print("  'start' - Start recording")
        print("  'stop'  - Stop recording")
        print("  'quit'  - Exit")
        print("="*50)
        
        await self.connect()
        
        try:
            while True:
                command = input("\nEnter command: ").strip().lower()
                
                if command == 'start':
                    if not self.is_recording:
                        asyncio.create_task(self.start_recording())
                    else:
                        print("Already recording!")
                        
                elif command == 'stop':
                    if self.is_recording:
                        await self.stop_recording()
                    else:
                        print("Not recording!")
                        
                elif command == 'quit':
                    if self.is_recording:
                        await self.stop_recording()
                    break
                    
                else:
                    print("Unknown command. Use 'start', 'stop', or 'quit'")
                    
        finally:
            await self.disconnect()


class HTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler with CORS support."""
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()


def serve_web_client(port: int = 8080):
    """Serve the HTML client via HTTP server."""
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    with socketserver.TCPServer(("", port), HTTPRequestHandler) as httpd:
        print(f"\n🌐 Medical Transcription Web Client")
        print(f"="*50)
        print(f"Server running at:")
        print(f"  👉 http://localhost:{port}/client.html")
        print(f"")
        print(f"Features:")
        print(f"  ✅ Real-time speech-to-text transcription")
        print(f"  ✅ Medical entity detection")
        print(f"  ✅ Multiple transcription endpoints")
        print(f"  ✅ WebSocket & HTTP/2 support")
        print(f"")
        print(f"Press Ctrl+C to stop the server")
        print(f"="*50)
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n👋 Server stopped")


async def run_python_client(url: str = None):
    """Run the Python streaming client."""
    if not AUDIO_LIBS_AVAILABLE:
        print("Error: Audio libraries not available.")
        print("Install with: pip install pyaudio websockets numpy")
        return
        
    client = MedicalStreamingClient(url or "ws://localhost:8000/ws/medical")
    await client.run_interactive()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Medical Transcription Client")
    parser.add_argument(
        '--mode', 
        choices=['web', 'python'], 
        default='web',
        help='Client mode: web (serve HTML) or python (streaming client)'
    )
    parser.add_argument(
        '--port', 
        type=int, 
        default=8080,
        help='Port for web server (default: 8080)'
    )
    parser.add_argument(
        '--url',
        default='ws://localhost:8000/ws/medical',
        help='WebSocket URL for Python client'
    )
    
    args = parser.parse_args()
    
    if args.mode == 'web':
        serve_web_client(args.port)
    else:
        asyncio.run(run_python_client(args.url))


if __name__ == "__main__":
    main()