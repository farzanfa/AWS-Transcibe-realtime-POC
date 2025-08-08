const WS_URL = (window.VITE_MIDDLEWARE_WS || "ws://localhost:8081/ws");
const startBtn = document.getElementById("startBtn");
const stopBtn  = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");

let mediaStream, mediaRecorder, ws;

function append(text) {
  transcriptEl.textContent += text + "\n";
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

startBtn.onclick = async () => {
  startBtn.disabled = true;
  stopBtn.disabled = false;

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => { statusEl.textContent = "Streaming…"; statusEl.className = "live"; };
  ws.onclose = () => { statusEl.textContent = "Disconnected"; statusEl.className = ""; };
  ws.onerror = (e) => { console.error(e); append("[Client] WebSocket error"); };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "transcript") append(msg.text);
      if (msg.type === "info") append(`[info] ${msg.message}`);
      if (msg.type === "error") append(`[error] ${msg.message}`);
    } catch {
      append(evt.data);
    }
  };

  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus" : "audio/webm";

  mediaRecorder = new MediaRecorder(mediaStream, { mimeType, audioBitsPerSecond: 16000 * 16 });
  mediaRecorder.ondataavailable = (e) => { if (e.data.size && ws?.readyState === 1) e.data.arrayBuffer().then(buf => ws.send(buf)); };
  mediaRecorder.start(250); // 250ms chunks
};

stopBtn.onclick = () => {
  stopBtn.disabled = true;
  startBtn.disabled = false;

  mediaRecorder?.stop();
  mediaStream?.getTracks().forEach(t => t.stop());
  ws?.close(1000, "client-stop");
  statusEl.textContent = "Idle"; statusEl.className = "";
};