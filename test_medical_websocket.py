#!/usr/bin/env python3
"""
Test script to verify the medical WebSocket error fix.
This script simulates connecting to the medical WebSocket, sending audio, and then disconnecting.
"""

import asyncio
import json
import base64
import websockets
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_medical_websocket():
    """Test the medical WebSocket connection and disconnection."""
    uri = "ws://localhost:8000/ws/medical"
    
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("Connected to medical WebSocket")
            
            # Wait for initial info message
            initial_msg = await websocket.recv()
            logger.info(f"Received initial message: {initial_msg}")
            
            # Start transcription
            start_msg = {
                "type": "start",
                "config": {
                    "language_code": "en-US",
                    "sample_rate": 16000,
                    "specialty": "PRIMARYCARE",
                    "type": "CONVERSATION"
                }
            }
            await websocket.send(json.dumps(start_msg))
            logger.info("Sent start message")
            
            # Wait for session started confirmation
            response = await websocket.recv()
            logger.info(f"Received response: {response}")
            
            # Send a few audio chunks (simulated)
            for i in range(3):
                # Create fake audio data (silent PCM)
                audio_data = base64.b64encode(b'\x00' * 1024).decode('utf-8')
                audio_msg = {
                    "type": "audio",
                    "data": audio_data
                }
                await websocket.send(json.dumps(audio_msg))
                logger.info(f"Sent audio chunk {i + 1}")
                
                # Small delay between chunks
                await asyncio.sleep(0.5)
            
            # Stop transcription
            stop_msg = {"type": "stop"}
            await websocket.send(json.dumps(stop_msg))
            logger.info("Sent stop message")
            
            # Wait for any final messages
            try:
                final_msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                logger.info(f"Received final message: {final_msg}")
            except asyncio.TimeoutError:
                logger.info("No final message received (timeout)")
            
    except Exception as e:
        logger.error(f"Error during test: {e}")
        return False
    
    logger.info("Test completed successfully - no InvalidStateError!")
    return True

async def main():
    """Run the test."""
    logger.info("Starting medical WebSocket test...")
    
    # Give the server a moment to be ready
    await asyncio.sleep(1)
    
    success = await test_medical_websocket()
    
    if success:
        logger.info("✅ Test passed - the fix appears to be working!")
    else:
        logger.error("❌ Test failed")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())