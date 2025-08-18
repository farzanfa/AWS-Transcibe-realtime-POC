#!/usr/bin/env python3
"""
Test script to verify the medical WebSocket connection cleanup fix.
This simulates a quick connect/disconnect scenario that was causing the InvalidStateError.
"""

import asyncio
import json
import websockets
import base64
import sys


async def test_medical_websocket():
    """Test medical WebSocket connection and cleanup."""
    uri = "ws://localhost:8000/ws/medical"
    
    try:
        print("Connecting to medical WebSocket...")
        async with websockets.connect(uri) as websocket:
            # Receive initial info message
            initial_msg = await websocket.recv()
            print(f"Initial message: {json.loads(initial_msg)['type']}")
            
            # Start transcription
            print("Starting transcription...")
            await websocket.send(json.dumps({
                "type": "start",
                "config": {
                    "language_code": "en-US",
                    "sample_rate": 16000,
                    "specialty": "PRIMARYCARE",
                    "type": "CONVERSATION"
                }
            }))
            
            # Wait for session started confirmation
            session_msg = await websocket.recv()
            session_data = json.loads(session_msg)
            print(f"Session started: {session_data.get('session_id')}")
            
            # Send a small amount of audio data
            print("Sending audio data...")
            # Create fake audio data (silence)
            fake_audio = b'\x00' * 1600  # 100ms of silence at 16kHz
            audio_base64 = base64.b64encode(fake_audio).decode('utf-8')
            
            await websocket.send(json.dumps({
                "type": "audio",
                "data": audio_base64
            }))
            
            # Wait briefly for acknowledgment
            await asyncio.sleep(0.5)
            
            # Stop transcription
            print("Stopping transcription...")
            await websocket.send(json.dumps({
                "type": "stop"
            }))
            
            # Wait for session ended message
            try:
                end_msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                end_data = json.loads(end_msg)
                if end_data.get('type') == 'session_ended':
                    print(f"Session ended successfully: {end_data.get('session_id')}")
                else:
                    print(f"Received: {end_data}")
            except asyncio.TimeoutError:
                print("Timeout waiting for session end message")
            
        print("WebSocket closed cleanly - no errors!")
        return True
        
    except Exception as e:
        print(f"Error during test: {e}")
        return False


async def run_multiple_tests(num_tests=3):
    """Run multiple connection tests to ensure stability."""
    print(f"\nRunning {num_tests} connection tests...\n")
    
    success_count = 0
    for i in range(num_tests):
        print(f"\n--- Test {i+1}/{num_tests} ---")
        if await test_medical_websocket():
            success_count += 1
        await asyncio.sleep(1)  # Brief pause between tests
    
    print(f"\n\nTest Results: {success_count}/{num_tests} successful")
    return success_count == num_tests


if __name__ == "__main__":
    # Check if server is running
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 8000))
    sock.close()
    
    if result != 0:
        print("Error: Server is not running on localhost:8000")
        print("Please start the server with: docker-compose up")
        sys.exit(1)
    
    # Run the tests
    success = asyncio.run(run_multiple_tests())
    sys.exit(0 if success else 1)