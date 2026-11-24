import asyncio
import base64
import json
import os
import queue
import threading
import sounddevice as sd
import websockets

try:
    from config.persona import HENNA_SYSTEM_INSTRUCTION
except ImportError:
    from persona import HENNA_SYSTEM_INSTRUCTION

from tools.system_ops import (
    create_or_update_file,
    open_application,
    read_file,
    run_shell_command,
)

MODEL = "models/gemini-2.5-flash-native-audio-preview-09-2025"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_FRAMES = 512

TOOL_DECLARATIONS = [
    {
        "name": "create_or_update_file",
        "description": "Create a new file or overwrite an existing file inside the workspace.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "relative_path": {"type": "STRING"},
                "content": {"type": "STRING"},
            },
            "required": ["relative_path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read and return the contents of a file inside the workspace.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "relative_path": {"type": "STRING"},
            },
            "required": ["relative_path"],
        },
    },
    {
        "name": "run_shell_command",
        "description": "Run a shell command inside the workspace and return stdout/stderr.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "open_application",
        "description": "Launch an application or open a file/URL on Windows.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name_or_path": {"type": "STRING"},
            },
            "required": ["app_name_or_path"],
        },
    },
]


class HennaLiveEngine:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY missing in .env file!")

        self.mic_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.speaker_queue: queue.Queue[bytes | None] = queue.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None

        self.tools_map = {
            "create_or_update_file": create_or_update_file,
            "read_file": read_file,
            "run_shell_command": run_shell_command,
            "open_application": open_application,
        }

    def _mic_callback(self, indata, frames, time_info, status):
        if self.loop and not self.loop.is_closed():
            pcm_bytes = bytes(indata)
            self.loop.call_soon_threadsafe(self.mic_queue.put_nowait, pcm_bytes)

    def _speaker_worker(self):
        with sd.RawOutputStream(
            samplerate=OUTPUT_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_FRAMES,
        ) as out_stream:
            while True:
                chunk = self.speaker_queue.get()
                if chunk is None:
                    break
                out_stream.write(chunk)

    def _clear_speaker_queue(self):
        while not self.speaker_queue.empty():
            try:
                self.speaker_queue.get_nowait()
            except queue.Empty:
                break

    async def _send_mic_audio(self, ws):
        sent = 0
        while True:
            pcm_bytes = await self.mic_queue.get()
            payload = {
                "realtime_input": {
                    "media_chunks": [
                        {
                            "mime_type": f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                            "data": base64.b64encode(pcm_bytes).decode("ascii"),
                        }
                    ]
                }
            }
            await ws.send(json.dumps(payload))
            sent += 1
            if sent == 1:
                print("[Mic] Audio streaming started...")

    async def _handle_tool_calls(self, ws, tool_call):
        function_responses = []
        for fc in tool_call.get("functionCalls", []):
            name = fc.get("name")
            call_id = fc.get("id")
            args = fc.get("args") or {}
            fn = self.tools_map.get(name)

            print(f"\n[Henna Tool] {name}({args})")
            if fn:
                try:
                    result = await asyncio.to_thread(fn, **args)
                except Exception as e:
                    result = f"Error executing {name}: {e}"
            else:
                result = f"Unknown tool: {name}"

            print(f"[Tool Output] {result}")
            function_responses.append(
                {
                    "id": call_id,
                    "name": name,
                    "response": {"result": result},
                }
            )

        await ws.send(json.dumps({"tool_response": {"function_responses": function_responses}}))

    async def _receive_from_gemini(self, ws):
        async for raw_msg in ws:
            msg = json.loads(raw_msg)

            if "setupComplete" in msg:
                print("[Server] Setup complete! Triggering intro...")
                await ws.send(
                    json.dumps(
                        {
                            "client_content": {
                                "turns": [
                                    {
                                        "role": "user",
                                        "parts": [
                                            {
                                                "text": (
                                                    "Abhi session start hua hai. Apna chhota, sweet aur flirty intro do "
                                                    "ki tum Aditya ki girlfriend ho, cutie, aur poochho kya chahiye."
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

            if "toolCall" in msg:
                await self._handle_tool_calls(ws, msg["toolCall"])
                continue

            server_content = msg.get("serverContent")
            if not server_content:
                continue

            if server_content.get("interrupted"):
                self._clear_speaker_queue()
                continue

            if server_content.get("turnComplete"):
                print("[Henna] Finished speaking.")

            model_turn = server_content.get("modelTurn")
            if model_turn:
                for part in model_turn.get("parts", []):
                    if "text" in part:
                        print("[Henna Text]", part["text"])
                    inline_data = part.get("inlineData")
                    if inline_data and inline_data.get("data"):
                        pcm_out = base64.b64decode(inline_data["data"])
                        self.speaker_queue.put(pcm_out)

    async def run(self):
        self.loop = asyncio.get_running_loop()

        uri = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={self.api_key}"
        )

        setup_msg = {
            "setup": {
                "model": MODEL,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Aoede"
                            }
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": HENNA_SYSTEM_INSTRUCTION}]
                },
                "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],
            }
        }

        speaker_thread = threading.Thread(target=self._speaker_worker, daemon=True)
        speaker_thread.start()

        try:
            while True:
                print("Connecting to Henna...")
                try:
                    async with websockets.connect(
                        uri,
                        ping_interval=20,
                        ping_timeout=20,
                        max_size=16 * 1024 * 1024,
                    ) as ws:
                        await ws.send(json.dumps(setup_msg))
                        with sd.RawInputStream(
                            samplerate=INPUT_SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="int16",
                            blocksize=CHUNK_FRAMES,
                            callback=self._mic_callback,
                        ):
                            print("Henna is live! Start speaking (Press Ctrl+C to stop).")
                            await asyncio.gather(
                                self._send_mic_audio(ws),
                                self._receive_from_gemini(ws),
                            )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[Connection dropped: {e}] Reconnecting in 2s...")
                    await asyncio.sleep(2)
        finally:
            self.speaker_queue.put(None)