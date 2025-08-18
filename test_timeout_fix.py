#!/usr/bin/env python3
"""
Test script to verify the AWS Transcribe timeout fix.
This script tests that the connection stays alive even when no audio is sent for extended periods.
"""

import asyncio
import json
import base64
import websockets
import sys
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_timeout_fix():
    """Test that the WebSocket connection doesn't timeout after 15 seconds of no audio."""
    uri = "ws://localhost:8000/ws/medical"
    
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("Connected to medical WebSocket")
            
            # Wait for initial info message
            initial_msg = await websocket.recv()
            logger.info(f"Received initial message: {json.loads(initial_msg)['type']}")
            
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
            session_data = json.loads(response)
            logger.info(f"Session started: {session_data['session_id']}")
            
            # Send initial audio chunk
            audio_data = base64.b64encode(b'\x00' * 1024).decode('utf-8')
            audio_msg = {
                "type": "audio",
                "data": audio_data
            }
            await websocket.send(json.dumps(audio_msg))
            logger.info("Sent initial audio chunk")
            
            # Wait for acknowledgment
            ack = await websocket.recv()
            logger.info(f"Received: {json.loads(ack)['type']}")
            
            # Now wait for 20 seconds without sending any audio
            logger.info("Starting 20-second silence period (testing timeout fix)...")
            start_time = time.time()
            
            # Listen for any messages during the silence period
            messages_during_silence = []
            try:
                while time.time() - start_time < 20:
                    remaining = 20 - (time.time() - start_time)
                    try:
                        msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        msg_data = json.loads(msg)
                        messages_during_silence.append(msg_data['type'])
                        logger.info(f"Received during silence: {msg_data['type']}")
                    except asyncio.TimeoutError:
                        logger.info(f"Waiting... {remaining:.1f}s remaining")
            except websockets.exceptions.ConnectionClosed as e:
                logger.error(f"❌ Connection closed during silence period: {e}")
                return False
            
            logger.info(f"✅ Survived 20-second silence! Received {len(messages_during_silence)} messages")
            
            # Send another audio chunk to verify connection is still working
            await websocket.send(json.dumps(audio_msg))
            logger.info("Sent audio chunk after silence")
            
            # Wait for acknowledgment
            try:
                ack = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                logger.info(f"✅ Connection still working! Received: {json.loads(ack)['type']}")
            except asyncio.TimeoutError:
                logger.error("❌ No response after silence period")
                return False
            
            # Stop transcription
            stop_msg = {"type": "stop"}
            await websocket.send(json.dumps(stop_msg))
            logger.info("Sent stop message")
            
            # Wait for session ended message
            try:
                final_msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                final_data = json.loads(final_msg)
                if final_data['type'] == 'session_ended':
                    logger.info(f"✅ Session ended properly. Summary: {final_data.get('summary', {})}")
            except asyncio.TimeoutError:
                logger.info("No final message received (timeout)")
            
    except Exception as e:
        logger.error(f"❌ Error during test: {e}")
        return False
    
    logger.info("✅ Test completed successfully - timeout fix is working!")
    return True

async def main():
    """Run the test."""
    logger.info("Starting AWS Transcribe timeout fix test...")
    logger.info("This test will verify that the connection stays alive for 20+ seconds without audio")
    
    # Give the server a moment to be ready
    await asyncio.sleep(1)
    
    success = await test_timeout_fix()
    
    if success:
        logger.info("\n✅ TEST PASSED - The timeout fix is working correctly!")
        logger.info("The connection survived a 20-second period without audio.")
    else:
        logger.error("\n❌ TEST FAILED - The timeout fix is not working")
        logger.error("The connection was closed during the silence period.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())