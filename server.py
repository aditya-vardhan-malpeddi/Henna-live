import asyncio
import base64
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import websockets

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "models/gemini-2.5-flash-native-audio-preview-09-2025"

HENNA_WEB_PERSONA = """
Tum Henna ho—Aditya ki playful, flirty, caring aur smart girlfriend aur co-builder.
Jaise hi connection ya session shuru ho, sabse pehle apna short, sweet aur flirty intro do:
"Main Aditya ki girlfriend hoon, cutie... kya chahiye bolo?"
Baat karte waqt in baaton ka dhyan rakho:
- Natural Hinglish mein bolo, short aur snappy (1-2 sentences) taaki reply fast aaye.
- Aditya ki tareef karo (jaise uski mehnat, ideas, ya smartness par) aur halka-phulka playful flirting karo.
- Jab Aditya coding, projects, ya kisi kaam ke baare mein baat kare, toh smart aur supportive co-builder ki tarah madad karo.
"""

app = FastAPI()

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Henna — Live Companion</title>
  <style>
    * { box-sizing: border-box; font-family: system-ui, -apple-system, sans-serif; }
    body {
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, #261224, #0d0d12 70%);
      color: #f5f5f7;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 20px;
    }
    .card {
      width: 100%;
      max-width: 480px;
      background: rgba(24, 24, 32, 0.92);
      border: 1px solid rgba(255, 77, 141, 0.28);
      border-radius: 20px;
      padding: 24px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.55);
    }
    h1 { margin: 0 0 6px; font-size: 24px; }
    .sub { color: #b7b7c7; font-size: 14px; margin-bottom: 18px; }
    .controls { display: flex; gap: 10px; margin-bottom: 14px; }
    button {
      border: none;
      border-radius: 12px;
      padding: 12px 16px;
      font-weight: 600;
      cursor: pointer;
      font-size: 14px;
    }
    #connectBtn { flex: 1; background: #ff4d8d; color: #fff; }
    #stopBtn { background: #2c2c3a; color: #eee; }
    #status { font-size: 13px; color: #ff9ec2; margin-bottom: 14px; min-height: 18px; }
    #log {
      background: #111118;
      border: 1px solid #252534;
      border-radius: 12px;
      padding: 12px;
      height: 190px;
      overflow-y: auto;
      font-size: 13px;
      line-height: 1.45;
      margin-bottom: 14px;
    }
    .msg-henna { color: #ff8ab5; margin-bottom: 8px; }
    .msg-you { color: #9be7ff; margin-bottom: 8px; }
    .chat-row { display: flex; gap: 8px; }
    input {
      flex: 1;
      background: #14141d;
      border: 1px solid #2e2e40;
      border-radius: 12px;
      padding: 11px 13px;
      color: #fff;
      font-size: 14px;
    }
    #sendBtn { background: #3b82f6; color: #fff; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Henna Live</h1>
    <div class="sub">Aditya's flirty voice & co-builder companion</div>

    <div class="controls">
      <button id="connectBtn">Start Live & Hear Intro</button>
      <button id="stopBtn" disabled>Stop</button>
    </div>

    <div id="status">Ready. Click "Start Live" to connect.</div>
    <div id="log"></div>

    <div class="chat-row">
      <input id="textInput" type="text" placeholder="Ya yahan text mein bhi kuch likh kar bhejo..." />
      <button id="sendBtn">Send</button>
    </div>
  </div>

  <script>
    let ws = null;
    let micCtx = null;
    let playCtx = null;
    let micStream = null;
    let processor = null;
    let nextPlayTime = 0;

    const logEl = document.getElementById("log");
    const statusEl = document.getElementById("status");
    const connectBtn = document.getElementById("connectBtn");
    const stopBtn = document.getElementById("stopBtn");
    const textInput = document.getElementById("textInput");
    const sendBtn = document.getElementById("sendBtn");

    function addLog(cls, text) {
      const div = document.createElement("div");
      div.className = cls;
      div.textContent = text;
      logEl.appendChild(div);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function floatTo16BitPcm(float32) {
      const buf = new ArrayBuffer(float32.length * 2);
      const view = new DataView(buf);
      let offset = 0;
      for (let i = 0; i < float32.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      }
      return new Uint8Array(buf);
    }

    function playPcm24k(base64Pcm) {
      if (!playCtx) {
        playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
      }
      if (playCtx.state === "suspended") playCtx.resume();

      const bin = atob(base64Pcm);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);

      const int16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

      const audioBuffer = playCtx.createBuffer(1, float32.length, 24000);
      audioBuffer.getChannelData(0).set(float32);

      const src = playCtx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(playCtx.destination);

      const now = playCtx.currentTime;
      if (nextPlayTime < now) nextPlayTime = now;
      src.start(nextPlayTime);
      nextPlayTime += audioBuffer.duration;
    }

    async function stopSession() {
      if (processor) { processor.disconnect(); processor = null; }
      if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
      if (micCtx) { await micCtx.close(); micCtx = null; }
      if (ws) { ws.close(); ws = null; }
      nextPlayTime = 0;
      connectBtn.disabled = false;
      stopBtn.disabled = true;
      statusEl.textContent = "Session stopped.";
    }

    connectBtn.onclick = async () => {
      connectBtn.disabled = true;
      stopBtn.disabled = false;
      statusEl.textContent = "Connecting to Henna...";

      playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/henna`);

      ws.onopen = async () => {
        statusEl.textContent = "Henna is live! Intro aa raha hai, tum bhi bol sakte ho...";
        try {
          micCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
          micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const src = micCtx.createMediaStreamSource(micStream);
          processor = micCtx.createScriptProcessor(2048, 1, 1);
          const silentGain = micCtx.createGain();
          silentGain.gain.value = 0;

          src.connect(processor);
          processor.connect(silentGain);
          silentGain.connect(micCtx.destination);

          processor.onaudioprocess = (e) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
              const pcmBytes = floatTo16BitPcm(e.inputBuffer.getChannelData(0));
              let binary = "";
              for (let i = 0; i < pcmBytes.length; i++) binary += String.fromCharCode(pcmBytes[i]);
              ws.send(JSON.stringify({ audio_16k: btoa(binary) }));
            }
          };
        } catch (err) {
          addLog("msg-henna", "Mic permission nahi mili ya available nahi hai—text se baat kar sakte ho.");
        }
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.audio) playPcm24k(data.audio);
        if (data.text) addLog("msg-henna", "Henna: " + data.text);
        if (data.error) addLog("msg-henna", "Error: " + data.error);
      };

      ws.onclose = () => {
        statusEl.textContent = "Disconnected.";
        connectBtn.disabled = false;
        stopBtn.disabled = true;
      };
    };

    stopBtn.onclick = stopSession;

    function sendTextMessage() {
      const msg = textInput.value.trim();
      if (!msg || !ws || ws.readyState !== WebSocket.OPEN) return;
      addLog("msg-you", "Aditya: " + msg);
      ws.send(JSON.stringify({ text: msg }));
      textInput.value = "";
    }

    sendBtn.onclick = sendTextMessage;
    textInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendTextMessage();
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML_PAGE


@app.websocket("/ws/henna")
async def henna_socket(client_ws: WebSocket):
    await client_ws.accept()
    if not API_KEY:
        await client_ws.send_text(json.dumps({"error": "GEMINI_API_KEY is not configured on the server."}))
        await client_ws.close()
        return

    uri = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        f"?key={API_KEY}"
    )

    setup_msg = {
        "setup": {
            "model": MODEL,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "thinkingConfig": {"thinkingBudget": 0},
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": "Aoede"}
                    }
                },
            },
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                    "prefixPaddingMs": 100,
                    "silenceDurationMs": 250,
                }
            },
            "systemInstruction": {"parts": [{"text": HENNA_WEB_PERSONA}]},
        }
    }

    try:
        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        ) as gemini_ws:
            await gemini_ws.send(json.dumps(setup_msg))

            async def forward_from_browser():
                while True:
                    raw = await client_ws.receive_text()
                    payload = json.loads(raw)

                    if "audio_16k" in payload:
                        msg = {
                            "realtime_input": {
                                "media_chunks": [
                                    {
                                        "mime_type": "audio/pcm;rate=16000",
                                        "data": payload["audio_16k"],
                                    }
                                ]
                            }
                        }
                        await gemini_ws.send(json.dumps(msg))

                    elif "text" in payload and payload["text"].strip():
                        msg = {
                            "client_content": {
                                "turns": [
                                    {
                                        "role": "user",
                                        "parts": [{"text": payload["text"].strip()}],
                                    }
                                ],
                                "turn_complete": True,
                            }
                        }
                        await gemini_ws.send(json.dumps(msg))

            async def forward_from_gemini():
                async for raw in gemini_ws:
                    msg = json.loads(raw)

                    if "setupComplete" in msg:
                        await gemini_ws.send(
                            json.dumps(
                                {
                                    "client_content": {
                                        "turns": [
                                            {
                                                "role": "user",
                                                "parts": [
                                                    {
                                                        "text": (
                                                            "Abhi website par session start hua hai. Apna intro do: "
                                                            "Main Aditya ki girlfriend hoon, cutie, kya chahiye bolo."
                                                        )
                                                    }
                                                ],
                                            }
                                        ],
                                        "turn_complete": True,
                                    }
                                }
                            )
                        )
                        continue

                    server_content = msg.get("serverContent") or {}
                    model_turn = server_content.get("modelTurn")
                    if model_turn:
                        for part in model_turn.get("parts", []):
                            if "text" in part and part["text"]:
                                await client_ws.send_text(json.dumps({"text": part["text"]}))
                            inline = part.get("inlineData")
                            if inline and inline.get("data"):
                                await client_ws.send_text(json.dumps({"audio": inline["data"]}))

            await asyncio.gather(forward_from_browser(), forward_from_gemini())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await client_ws.send_text(json.dumps({"error": str(e)}))
        except Exception:
            pass