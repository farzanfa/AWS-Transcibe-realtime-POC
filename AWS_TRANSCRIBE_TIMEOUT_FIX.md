# AWS Transcribe 15-Second Timeout Fix

## Problem
AWS Transcribe Streaming has a built-in timeout mechanism that closes the connection if no audio data is received for 15 seconds. This causes the following error:
```
Your request timed out because no new audio was received for 15 seconds.
```

This is particularly problematic in scenarios where:
- Users pause speaking for extended periods
- There are natural gaps in conversation
- Audio capture is temporarily interrupted
- The initial connection is established but audio streaming hasn't started yet

## Solution
Implemented a heartbeat mechanism that sends small amounts of silence periodically to keep the connection alive when no real audio is being received.

### Key Changes in `medical_streaming.py`

1. **Added Heartbeat Task**
   - New `_heartbeat_task` that runs alongside audio writer and handler tasks
   - Sends silence every 10 seconds if no real audio has been received
   - Prevents the 15-second timeout from triggering

2. **Track Last Audio Time**
   - Added `_last_audio_time` to track when real audio was last received
   - Updates whenever actual audio chunks are sent from the client
   - Heartbeat only sends silence if no recent audio activity

3. **Heartbeat Implementation**
   ```python
   async def _heartbeat_sender(self):
       """Send silence periodically to prevent AWS Transcribe timeout."""
       while self.running:
           await asyncio.sleep(10)
           current_time = asyncio.get_event_loop().time()
           if self._last_audio_time is None or (current_time - self._last_audio_time) > 10:
               # Send 100ms of silence at 16kHz
               silence = b'\x00' * 3200
               await self.audio_queue.put(silence)
   ```

4. **Proper Cleanup**
   - Heartbeat task is properly cancelled during session shutdown
   - Added to the list of tasks to cancel in `stop_transcription()`

## Benefits
1. **Prevents Timeouts**: Connection stays alive during natural pauses in speech
2. **Minimal Impact**: Only sends silence when needed, not continuously
3. **Transparent**: AWS Transcribe ignores silence, so it doesn't affect transcription quality
4. **Configurable**: Easy to adjust heartbeat interval if needed

## Technical Details
- Heartbeat interval: 10 seconds (well under the 15-second timeout)
- Silence duration: 100ms (minimal but sufficient)
- Silence format: PCM 16-bit, matching the expected audio format
- Only active during transcription session

## Testing
The fix can be tested by:
1. Starting a transcription session
2. Not sending any audio for 20+ seconds
3. Verifying the connection remains active
4. Confirming transcription still works when audio resumes

## Future Improvements
- Make heartbeat interval configurable via environment variable
- Add metrics to track how often heartbeat is needed
- Consider adaptive heartbeat based on audio activity patterns