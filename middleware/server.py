import asyncio
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydub import AudioSegment
import websockets
from starlette.websockets import WebSocketState
from io import BytesIO

app = FastAPI()
BACKEND_WS_URL = os.getenv("BACKEND_WS_URL", "ws://backend:8082/ingest")

@app.get("/", response_class=PlainTextResponse)
def health():
    return "middleware: ok"

@app.websocket("/ws")
async def ws_proxy(client_ws: WebSocket):
    await client_ws.accept()
    try:
        async with websockets.connect(BACKEND_WS_URL, max_size=10 * 1024 * 1024) as backend_ws:
            send_task = asyncio.create_task(_forward_audio(client_ws, backend_ws))
            recv_task = asyncio.create_task(_pipe_transcripts(client_ws, backend_ws))
            done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending: task.cancel()
    except Exception as e:
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.send_text(json.dumps({"type":"error", "message": f"backend connect failed: {e}"}))
            await client_ws.close()

async def _pipe_transcripts(client_ws: WebSocket, backend_ws):
    async for msg in backend_ws:
        await client_ws.send_text(msg)

async def _forward_audio(client_ws: WebSocket, backend_ws):
    await backend_ws.send(json.dumps({"type": "start", "format": "pcm_s16le", "rate": 16000, "channels": 1}))
    try:
        while True:
            data = await client_ws.receive_bytes()
            segment = AudioSegment.from_file(
                io_bytes := BytesIO(data), format="webm"
            ).set_frame_rate(16000).set_channels(1).set_sample_width(2)
            await backend_ws.send(segment.raw_data)
    except WebSocketDisconnect:
        await backend_ws.send(json.dumps({"type":"stop"}))
    except Exception as e:
        await backend_ws.send(json.dumps({"type":"error","message":str(e)}))