import os
import json
import asyncio
import websockets
import boto3
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import AudioEvent, StartMedicalStreamTranscriptionRequest

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET = os.getenv("S3_BUCKET_NAME")
SPECIALTY = os.getenv("TRANSCRIBE_MEDICAL_SPECIALTY", "PRIMARYCARE")
MTYPE = os.getenv("TRANSCRIBE_MEDICAL_TYPE", "DICTATION")

app = FastAPI()

@app.get("/", response_class=PlainTextResponse)
def health():
    return "backend: ok"

@app.websocket("/ingest")
async def ingest(ws: WebSocket):
    await ws.accept()
    start_msg = await ws.receive_text()
    ctrl = json.loads(start_msg)
    assert ctrl.get("type") == "start", "expected start control message"

    client = TranscribeStreamingClient(region=AWS_REGION)
    stream = await client.start_medical_stream_transcription(
        request=StartMedicalStreamTranscriptionRequest(
            language_code="en-US",
            media_sample_rate_hz=int(ctrl.get("rate", 16000)),
            media_encoding="pcm",
            specialty=SPECIALTY,
            type=MTYPE,
            show_speaker_label=False,
            enable_channel_identification=False
        )
    )

    async def write_audio():
        try:
            while True:
                msg = await ws.receive()
                if "bytes" in msg:
                    await stream.input_stream.send_audio_event(audio_chunk=msg["bytes"])
                else:
                    payload = json.loads(msg["text"])
                    if payload.get("type") == "stop":
                        break
        finally:
            await stream.input_stream.end_stream()

    s3 = boto3.client("s3", region_name=AWS_REGION)
    session_key = f"sessions/{os.urandom(8).hex()}.txt"
    transcript_buffer = []

    class Handler(TranscriptResultStreamHandler):
        async def handle_transcript_event(self, transcript_event):
            results = transcript_event.transcript.results
            for result in results:
                if result.is_partial:
                    alt = result.alternatives[0]
                    await ws.send_text(json.dumps({"type":"transcript", "text": alt.transcript}))
                else:
                    alt = result.alternatives[0]
                    text = alt.transcript.strip()
                    if text:
                        transcript_buffer.append(text + "\n")
                        await ws.send_text(json.dumps({"type":"transcript", "text": text}))
                        s3.put_object(Bucket=S3_BUCKET, Key=session_key, Body="".join(transcript_buffer).encode("utf-8"))

    handler = Handler(stream.output_stream)

    tasks = [asyncio.create_task(write_audio()), asyncio.create_task(handler.handle_events())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    if transcript_buffer:
        s3.put_object(Bucket=S3_BUCKET, Key=session_key, Body="".join(transcript_buffer).encode("utf-8"))

    await ws.close()