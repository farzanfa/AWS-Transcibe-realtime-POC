# Medical WebSocket InvalidStateError Fix

## Problem
When closing a medical transcription WebSocket connection, the following error was occurring:
```
concurrent.futures._base.InvalidStateError: CANCELLED: <Future at 0x7fbeef05f550 state=cancelled>
```

This error happens in the AWS SDK's HTTP handler when it tries to set a result on a future that has already been cancelled during the shutdown process.

## Root Cause
The issue occurs because:
1. When the WebSocket closes, the transcription session's cleanup process cancels various async tasks
2. The AWS SDK's internal HTTP handler continues to receive responses and tries to set results on futures
3. These futures have already been cancelled, causing the InvalidStateError

## Solution
The fix implements multiple layers of protection:

### 1. Improved Cleanup Sequence
- Modified `stop_transcription()` to handle task cancellation more gracefully
- Added parallel task cancellation instead of sequential
- Reduced timeout for waiting on cancelled tasks

### 2. Better Error Handling in Audio Writer
- Added checks for stream validity before sending audio
- Improved error detection for expected shutdown errors
- Added specific handling for "closed", "cancelled", and "invalid state" errors

### 3. Handler Task Wrapper
- Wrapped the AWS SDK's `handle_events()` method with error handling
- Catches and logs expected errors during shutdown as debug messages

### 4. Future.set_result Monkey Patch
- Added a monkey patch to catch InvalidStateError at the source
- Prevents the exception from propagating and crashing the application
- Logs the error as a debug message instead

## Code Changes

### medical_streaming.py
1. Added monkey patch for `concurrent.futures.Future.set_result`
2. Improved `stop_transcription()` method with better cancellation handling
3. Enhanced `_write_audio_chunks()` with better error detection
4. Added wrapper for handler task with error handling

## Testing
A test script (`test_medical_websocket.py`) is provided to verify the fix:
- Connects to the medical WebSocket
- Starts transcription
- Sends audio chunks
- Stops transcription and disconnects
- Verifies no InvalidStateError occurs

## Result
The error is now properly handled and logged as a debug message instead of crashing the application. The WebSocket connection closes cleanly without throwing exceptions.