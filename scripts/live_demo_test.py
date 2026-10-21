"""
Live end-to-end test for Pitchlight (macOS only: uses `say` and `afconvert`).

Speaks scripted lines into the RUNNING app (http://localhost:8080) over the real WebSocket protocol, in real
time, and records every message the server sends back. It makes real Gemini + Claude calls
(about $0.30 of Claude credits per run) and saves sessions under the test user "user-demo-test", so your own
History is untouched.

  .venv/bin/python scripts/live_demo_test.py meeting   -> Meeting Mode call (client voice on the meeting-audio stream)
  .venv/bin/python scripts/live_demo_test.py practice  -> Practice with the AI role-play investor

Outputs: scripts/live_test_out/<name>_messages.jsonl and <name>_scorecard.json.
"""
import asyncio
import audioop
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import websockets

HERE = Path(__file__).parent / "live_test_out"      # recorded messages + scorecards land here
AUDIO = HERE / "audio"                               # cached text-to-speech
AUDIO.mkdir(parents=True, exist_ok=True)
HOST = "localhost:8080"
USER = "user-demo-test"
CHUNK_S = 0.1
CHUNK_BYTES = 3200            # 100 ms of 16 kHz mono int16
T_AUDIO_IN, T_STOP, T_OTHER = 0x01, 0x03, 0x06
T_AUDIO_OUT = 0x10


def tts(text: str, voice: str, rate: int = 172) -> bytes:
    key = f"{abs(hash((text, voice, rate)))}"
    wav_path = AUDIO / f"{key}.wav"
    if not wav_path.exists():
        aiff = AUDIO / f"{key}.aiff"
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav_path)], check=True)
        aiff.unlink(missing_ok=True)
    with wave.open(str(wav_path)) as w:
        pcm = w.readframes(w.getnframes())
    peak = audioop.max(pcm, 2) or 1
    pcm = audioop.mul(pcm, 2, min(3.0, 14000 / peak))      # normalise to a healthy mic level
    pad = (-len(pcm)) % CHUNK_BYTES
    return pcm + b"\0" * pad


class Run:
    def __init__(self, path: str, name: str):
        self.url = f"ws://{HOST}/ws?{path}&user={USER}"
        self.name = name
        self.msgs: list[dict] = []
        self.t0 = time.monotonic()
        self.audio_out_bytes = 0
        self.you: asyncio.Queue = asyncio.Queue()
        self.them: asyncio.Queue = asyncio.Queue()
        self.ws = None
        self.done = asyncio.Event()
        self.scorecard = None
        self.partner_count = 0
        self.partner_last = 0.0
        self.streaming = True

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 1)

    def log(self, *a):
        print(f"[{self.now():6.1f}s]", *a, flush=True)

    async def recv(self):
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    if raw and raw[0] == T_AUDIO_OUT:
                        self.audio_out_bytes += len(raw) - 1
                    continue
                msg = json.loads(raw)
                msg["_t"] = self.now()
                self.msgs.append(msg)
                if msg.get("type") == "transcript" and msg.get("speaker") == "partner":
                    self.partner_count += 1
                    self.partner_last = time.monotonic()
                if msg.get("type") == "scorecard":
                    self.scorecard = msg["data"]
                    self.done.set()
        except Exception as exc:
            self.log("recv ended:", repr(exc)[:120])
        self.done.set()

    async def pump(self):
        """One 100 ms tick at a time: mic stream always, meeting-audio stream when used."""
        tick = time.monotonic()
        silence = b"\0" * CHUNK_BYTES
        while self.streaming:
            tick += CHUNK_S
            try:
                you = self.you.get_nowait()
            except asyncio.QueueEmpty:
                you = silence
            await self.ws.send(bytes([T_AUDIO_IN]) + you)
            if self.use_them:
                try:
                    them = self.them.get_nowait()
                except asyncio.QueueEmpty:
                    them = silence
                await self.ws.send(bytes([T_OTHER]) + them)
            await asyncio.sleep(max(0.0, tick - time.monotonic()))

    use_them = False

    async def speak(self, who: str, text: str, voice: str, rate: int = 172):
        pcm = await asyncio.to_thread(tts, text, voice, rate)     # don't stall the live audio stream
        q = self.you if who == "you" else self.them
        self.log(f"{who.upper():5} says ({len(pcm) / 32000:.1f}s): {text[:90]}")
        for i in range(0, len(pcm), CHUNK_BYTES):
            q.put_nowait(pcm[i:i + CHUNK_BYTES])
        while not q.empty():
            await asyncio.sleep(0.1)

    async def send(self, obj: dict):
        await self.ws.send(json.dumps(obj))

    async def gaze(self, state: str, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await self.send({"type": "gaze", "state": state})
            await asyncio.sleep(1.0)

    async def connect(self):
        self.ws = await websockets.connect(self.url, max_size=None, ping_interval=20)
        self.rx = asyncio.create_task(self.recv())
        for _ in range(100):
            if any(m.get("type") == "status" and m.get("state") == "connected" for m in self.msgs):
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("never connected")
        self.log("connected", [m for m in self.msgs if m.get("type") == "status"][0].get("session_id"))
        self.tx = asyncio.create_task(self.pump())

    async def finish(self, wait_s: int = 300):
        await asyncio.sleep(3.0)                    # a little trailing silence so the last line is flushed
        self.streaming = False
        await asyncio.sleep(0.3)
        await self.ws.send(bytes([T_STOP]))
        self.log("stop sent — waiting for the post-call analysis…")
        try:
            await asyncio.wait_for(self.done.wait(), wait_s)
        except asyncio.TimeoutError:
            self.log("TIMEOUT waiting for scorecard")
        out = HERE / f"{self.name}_messages.jsonl"
        out.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in self.msgs))
        if self.scorecard:
            (HERE / f"{self.name}_scorecard.json").write_text(json.dumps(self.scorecard, ensure_ascii=False, indent=1))
        self.log(f"done. messages={len(self.msgs)} audio_out={self.audio_out_bytes}B scorecard={bool(self.scorecard)}")
        await self.ws.close()


# ─────────────────────────── MEETING ───────────────────────────
YOU, THEM = "Samantha", "Daniel"
MEETING_BRIEF = {
    "type": "brief",
    "client": "Northwind Dental",
    "notes": "Client: Northwind Dental (Priya, head of operations), 40 clinics. They bill with spreadsheets and Zoho "
             "today. Goal: agree a 30-day pilot on five clinics.",
    "points": ["Billing Hub centralizes payments", "Stripe and Razorpay routing", "Offer a 30 day pilot",
               "Agree the next meeting date"],
    "planned_minutes": 7,
    "objections": "Price: pays back in about three months for a forty clinic chain\n"
                  "Competitor: we plug into Zoho, so there is nothing to replace",
}


async def meeting():
    r = Run("kind=meeting&platform=teams&mode=sales_demo&persona=coach&context=virtual&chatty=active", "meeting")
    r.use_them = True
    await r.connect()
    await r.send(MEETING_BRIEF)
    await asyncio.sleep(1.5)

    await r.speak("them", "Hi, thanks for making the time. I'm Priya, head of operations at Northwind Dental. "
                          "We run forty clinics, and we want to go live by March.", THEM)
    await asyncio.sleep(1.2)
    gz = asyncio.create_task(r.gaze("notes", 7.5))              # looking down at notes while talking
    await r.speak("you", "Thanks Priya, great to meet you. So, um, basically Billing Hub is, uh, a centralized "
                         "payments platform. It routes every payment through Stripe or Razorpay, so each clinic "
                         "gets paid the right way, you know, wherever the patient is.", YOU, 178)
    await gz
    await r.send({"type": "gaze", "state": "camera"})
    await asyncio.sleep(1.5)
    await r.speak("them", "How long does setup take for forty clinics?", THEM)
    await asyncio.sleep(3.5)
    await r.send({"type": "help", "kind": "answer"})
    await asyncio.sleep(1.0)
    await r.speak("you", "About two weeks. One week to import your data, and one week of staff training. "
                         "I'll send you the rollout plan by Friday.", YOU)
    await asyncio.sleep(1.5)
    await r.speak("them", "Honestly, that seems too expensive for us right now. We already use Zoho for our billing.", THEM)
    await asyncio.sleep(1.2)
    await r.speak("you", "That's fair. We plug into Zoho, so there is nothing to replace, and it pays back in about "
                         "three months. What does your billing process look like today, and how many people touch it?", YOU)
    await asyncio.sleep(1.5)
    await r.speak("them", "Three people reconcile spreadsheets every week, and it takes about two days. "
                          "We will share our clinic list by next Tuesday.", THEM)
    await asyncio.sleep(2.0)
    await r.send({"type": "help", "kind": "ask"})
    await asyncio.sleep(1.0)
    await r.speak("you", "That is a lot of time. Let's start with a thirty day pilot on five clinics. "
                         "Can we book a call next Wednesday to agree the start date?", YOU)
    await asyncio.sleep(1.2)
    await r.speak("them", "Yes, Wednesday works. Send me an invite.", THEM)
    await asyncio.sleep(1.0)
    await r.speak("you", "Perfect. I'll send the invite today. Thank you, Priya.", YOU)
    # What the browser would report about talk time at the end of the call.
    await r.send({"type": "talk_time", "kind": "monologue", "seconds": 95})
    await r.send({"type": "talk_time", "kind": "stats", "you_s": 70.0, "them_s": 38.0, "longest_monologue_s": 18})
    await asyncio.sleep(2.0)
    await r.finish()


# ─────────────────────────── PRACTICE (role-play) ───────────────────────────
PITCH_POINTS = ["Billing Hub powers payments for 30+ ventures", "Stripe and Razorpay routing by country",
                "One dashboard with churn tracking"]
ME = "Samantha"


async def wait_partner(r: Run, timeout: float = 50.0, quiet: float = 3.0):
    """Wait for the partner to start talking and finish (no new text for `quiet` seconds)."""
    start_count, t_end = r.partner_count, time.monotonic() + timeout
    while time.monotonic() < t_end:
        await asyncio.sleep(0.5)
        if r.partner_count > start_count and time.monotonic() - r.partner_last > quiet:
            return True
    r.log("partner did not (finish) speaking within timeout")
    return False


async def practice():
    r = Run("kind=practice&mode=pitch&persona=coach&context=virtual&goal=balanced&roleplay=investor", "practice")
    await r.connect()
    await r.send({"type": "pitch", "name": "Billing Hub pitch", "points": PITCH_POINTS})
    await wait_partner(r, 40)
    await r.speak("you", "Okay, ready, let's start.", ME)                                # setup chatter (not scored)
    await asyncio.sleep(1.0)
    gz = asyncio.create_task(r.gaze("notes", 7.5))
    await r.speak("you", "Hi everyone, I'm Poorab. Today I'm pitching Billing Hub. So, um, Billing Hub is a "
                         "centralized payments platform that powers payments for more than thirty ventures. "
                         "It it works on two routes: Stripe for users in the US, and Razorpay for users in India.", ME, 180)
    await gz
    await r.send({"type": "gaze", "state": "camera"})
    await wait_partner(r)
    await r.speak("you", "Great question. We have thirty ventures live today, and every payment goes through one "
                         "dashboard where you can see all revenue in real time. We are adding churn tracking next quarter.", ME)
    await asyncio.sleep(4.0)
    await r.send({"type": "say_better"})
    await asyncio.sleep(6.0)
    await wait_partner(r)
    await r.speak("you", "Our pricing is a small percentage per transaction, and it replaces three separate "
                         "integrations for every venture. Thank you.", ME)
    await wait_partner(r, 25)
    await r.finish()


if __name__ == "__main__":
    asyncio.run({"meeting": meeting, "practice": practice}[sys.argv[1]]())
