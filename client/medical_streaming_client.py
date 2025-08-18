#!/usr/bin/env python3
"""
Medical Transcription Streaming Client
Example Python client for real-time medical transcription using WebSocket
"""

import asyncio
import base64
import json
import logging
import sys
from typing import Optional, Dict, Any
import wave
import pyaudio
import websockets
import numpy as np
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MedicalStreamingClient:
    """Client for real-time medical transcription via WebSocket."""
    
    def __init__(self, websocket_url: str = "ws://localhost:8000/ws/medical"):
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
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from the WebSocket server."""
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from server")
    
    async def start_transcription(self, config: Optional[Dict[str, Any]] = None):
        """Start the medical transcription session."""
        if not self.websocket:
            raise RuntimeError("Not connected to server")
        
        # Default configuration
        default_config = {
            'specialty': 'PRIMARYCARE',
            'type': 'CONVERSATION',
            'language_code': 'en-US',
            'sample_rate': self.sample_rate,
            'show_speaker_labels': True,
            'content_identification_type': 'PHI'
        }
        
        # Merge with provided config
        if config:
            default_config.update(config)
        
        # Send start message
        await self.websocket.send(json.dumps({
            'type': 'start',
            'config': default_config
        }))
        
        logger.info(f"Started transcription with config: {default_config}")
    
    async def send_audio_chunk(self, audio_data: bytes):
        """Send audio chunk to the server."""
        if not self.websocket:
            raise RuntimeError("Not connected to server")
        
        # Convert to base64
        base64_audio = base64.b64encode(audio_data).decode('utf-8')
        
        # Send to server
        await self.websocket.send(json.dumps({
            'type': 'audio',
            'data': base64_audio
        }))
    
    async def stop_transcription(self):
        """Stop the transcription session."""
        if not self.websocket:
            return
        
        await self.websocket.send(json.dumps({'type': 'stop'}))
        logger.info("Stopped transcription")
    
    async def get_full_transcript(self, format: str = 'plain'):
        """Request the full transcript from the server."""
        if not self.websocket:
            raise RuntimeError("Not connected to server")
        
        await self.websocket.send(json.dumps({
            'type': 'get_transcript',
            'format': format
        }))
    
    async def handle_messages(self):
        """Handle incoming messages from the server."""
        if not self.websocket:
            return
        
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._process_message(data)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by server")
        except Exception as e:
            logger.error(f"Error handling messages: {e}")
    
    async def _process_message(self, data: Dict[str, Any]):
        """Process a message from the server."""
        message_type = data.get('type')
        
        if message_type == 'session_started':
            self.session_id = data.get('session_id')
            logger.info(f"Session started: {self.session_id}")
            logger.info(f"Configuration: {data.get('config')}")
            
        elif message_type == 'transcript':
            transcript = data.get('transcript', {})
            entities = data.get('medical_entities', [])
            
            # Store transcript
            if not transcript.get('is_partial'):
                self.transcripts.append({
                    'text': transcript.get('text'),
                    'speaker': transcript.get('speaker'),
                    'timestamp': data.get('timestamp'),
                    'entities': entities
                })
            
            # Update entities
            for entity in entities:
                category = entity.get('category')
                if category not in self.medical_entities:
                    self.medical_entities[category] = []
                
                # Add unique entities
                entity_text = entity.get('text')
                if not any(e['text'] == entity_text for e in self.medical_entities[category]):
                    self.medical_entities[category].append({
                        'text': entity_text,
                        'confidence': entity.get('confidence', 0)
                    })
            
            # Display transcript
            self._display_transcript(transcript, entities)
            
        elif message_type == 'error':
            logger.error(f"Server error: {data.get('error')}")
            
        elif message_type == 'session_ended':
            logger.info(f"Session ended. Total transcripts: {data.get('total_transcripts')}")
            
        elif message_type == 'full_transcript':
            logger.info("Full transcript received")
            print("\n=== Full Transcript ===")
            print(data.get('transcript', ''))
            print("\n=== Medical Entities ===")
            for category, entities in data.get('entities', {}).items():
                print(f"\n{category}:")
                for entity in entities:
                    print(f"  - {entity['text']} (confidence: {entity['confidence']:.2f})")
    
    def _display_transcript(self, transcript: Dict[str, Any], entities: list):
        """Display transcript in real-time."""
        text = transcript.get('text', '')
        is_partial = transcript.get('is_partial', True)
        speaker = transcript.get('speaker')
        
        # Format output
        prefix = "[PARTIAL] " if is_partial else "[FINAL]   "
        if speaker:
            prefix += f"Speaker {speaker}: "
        
        print(f"{prefix}{text}")
        
        # Display entities if final
        if not is_partial and entities:
            entity_str = ", ".join([f"{e['category']}: {e['text']}" for e in entities])
            print(f"          Entities: {entity_str}")
    
    async def stream_from_microphone(self, duration: Optional[int] = None):
        """Stream audio from microphone to the server."""
        self.audio = pyaudio.PyAudio()
        
        try:
            # Open audio stream
            self.stream = self.audio.open(
                format=self.audio_format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            
            logger.info("Started recording from microphone...")
            self.is_recording = True
            
            start_time = asyncio.get_event_loop().time()
            
            while self.is_recording:
                # Check duration limit
                if duration and (asyncio.get_event_loop().time() - start_time) > duration:
                    break
                
                # Read audio chunk
                try:
                    audio_data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                    await self.send_audio_chunk(audio_data)
                    await asyncio.sleep(0.01)  # Small delay to prevent overwhelming the server
                except Exception as e:
                    logger.error(f"Error reading audio: {e}")
                    break
            
        finally:
            self.stop_recording()
    
    def stop_recording(self):
        """Stop recording from microphone."""
        self.is_recording = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        
        if self.audio:
            self.audio.terminate()
            self.audio = None
        
        logger.info("Stopped recording")
    
    async def stream_from_file(self, file_path: str):
        """Stream audio from a WAV file to the server."""
        try:
            with wave.open(file_path, 'rb') as wav_file:
                # Verify format
                if wav_file.getnchannels() != 1:
                    raise ValueError("Only mono audio is supported")
                
                if wav_file.getsampwidth() != 2:
                    raise ValueError("Only 16-bit audio is supported")
                
                sample_rate = wav_file.getframerate()
                if sample_rate != self.sample_rate:
                    logger.warning(f"File sample rate ({sample_rate}) differs from expected ({self.sample_rate})")
                
                # Read and send chunks
                chunk_duration = self.chunk_size / sample_rate
                
                while True:
                    frames = wav_file.readframes(self.chunk_size)
                    if not frames:
                        break
                    
                    await self.send_audio_chunk(frames)
                    await asyncio.sleep(chunk_duration)  # Simulate real-time streaming
                
                logger.info("Finished streaming file")
                
        except Exception as e:
            logger.error(f"Error streaming file: {e}")
            raise
    
    def print_summary(self):
        """Print a summary of the transcription session."""
        print("\n=== Transcription Summary ===")
        print(f"Session ID: {self.session_id}")
        print(f"Total transcripts: {len(self.transcripts)}")
        
        # Print full conversation
        print("\n=== Conversation ===")
        current_speaker = None
        for transcript in self.transcripts:
            speaker = transcript.get('speaker')
            text = transcript.get('text')
            
            if speaker != current_speaker:
                current_speaker = speaker
                if speaker:
                    print(f"\n[Speaker {speaker}]: {text}")
                else:
                    print(f"\n{text}")
            else:
                print(f" {text}")
        
        # Print medical entities summary
        print("\n=== Medical Entities Summary ===")
        for category, entities in self.medical_entities.items():
            print(f"\n{category}:")
            for entity in sorted(entities, key=lambda x: x['confidence'], reverse=True):
                print(f"  - {entity['text']} (confidence: {entity['confidence']:.2f})")


async def main():
    """Main function to demonstrate the streaming client."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Medical Transcription Streaming Client')
    parser.add_argument('--url', default='ws://localhost:8000/ws/medical', help='WebSocket URL')
    parser.add_argument('--specialty', default='PRIMARYCARE', 
                       choices=['PRIMARYCARE', 'CARDIOLOGY', 'NEUROLOGY', 'ONCOLOGY', 'RADIOLOGY', 'UROLOGY'],
                       help='Medical specialty')
    parser.add_argument('--type', default='CONVERSATION',
                       choices=['CONVERSATION', 'DICTATION'],
                       help='Transcription type')
    parser.add_argument('--file', help='WAV file to stream (if not provided, uses microphone)')
    parser.add_argument('--duration', type=int, help='Recording duration in seconds (microphone only)')
    
    args = parser.parse_args()
    
    # Create client
    client = MedicalStreamingClient(args.url)
    
    try:
        # Connect to server
        if not await client.connect():
            return
        
        # Start message handler
        message_handler = asyncio.create_task(client.handle_messages())
        
        # Start transcription
        config = {
            'specialty': args.specialty,
            'type': args.type,
            'show_speaker_labels': args.type == 'CONVERSATION'
        }
        await client.start_transcription(config)
        
        # Stream audio
        if args.file:
            logger.info(f"Streaming from file: {args.file}")
            await client.stream_from_file(args.file)
        else:
            logger.info("Streaming from microphone...")
            print("Press Ctrl+C to stop recording")
            try:
                await client.stream_from_microphone(args.duration)
            except KeyboardInterrupt:
                logger.info("Recording interrupted by user")
        
        # Stop transcription
        await client.stop_transcription()
        
        # Wait a bit for final messages
        await asyncio.sleep(2)
        
        # Get full transcript
        await client.get_full_transcript('with_speakers')
        await asyncio.sleep(1)
        
        # Cancel message handler
        message_handler.cancel()
        
        # Print summary
        client.print_summary()
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await client.disconnect()


if __name__ == '__main__':
    # Check dependencies
    try:
        import pyaudio
        import websockets
        import numpy
    except ImportError as e:
        print("Missing required dependencies. Please install:")
        print("pip install pyaudio websockets numpy")
        sys.exit(1)
    
    # Run the client
    asyncio.run(main())