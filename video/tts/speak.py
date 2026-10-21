"""Generate narration with Kokoro (local, CPU). Runs inside the pitchmirror-kokoro image.

    docker run --rm -v "$PWD":/work pitchmirror-kokoro python tts/speak.py tts/lines.json assets/audio/vo

lines.json: {"voice": "af_heart", "speed": 1.0, "lines": {"id": "text", ...}}
Writes <out>/<id>.wav (24 kHz mono) and <out>/timing.json with each line's duration and
word timestamps (when Kokoro provides them).
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

SR = 24000


def main(spec_path: str, out_dir: str) -> None:
    spec = json.loads(Path(spec_path).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    voice = spec.get("voice", "af_heart")
    speed = float(spec.get("speed", 1.0))
    pipe = KPipeline(lang_code="a")
    timing = {"voice": voice, "speed": speed, "sample_rate": SR, "lines": {}}
    only = set(sys.argv[3:])
    for line_id, text in spec["lines"].items():
        if only and line_id not in only:
            continue
        chunks, words, offset = [], [], 0.0
        for result in pipe(text, voice=voice, speed=speed):
            audio = result.audio.cpu().numpy() if hasattr(result.audio, "cpu") else np.asarray(result.audio)
            for tok in (getattr(result, "tokens", None) or []):
                if getattr(tok, "start_ts", None) is not None:
                    words.append({"w": tok.text, "s": round(offset + tok.start_ts, 3), "e": round(offset + (tok.end_ts or tok.start_ts), 3)})
            chunks.append(audio)
            offset += len(audio) / SR
        audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        # Trim leading/trailing near-silence so scene timing is exact.
        idx = np.where(np.abs(audio) > 0.004)[0]
        lead = 0
        if len(idx):
            lead = max(0, idx[0] - int(0.02 * SR))
            audio = audio[lead: idx[-1] + int(0.12 * SR)]
        shift = lead / SR
        for w in words:
            w["s"] = round(max(0.0, w["s"] - shift), 3)
            w["e"] = round(max(0.0, w["e"] - shift), 3)
        sf.write(out / f"{line_id}.wav", audio, SR, subtype="PCM_16")
        timing["lines"][line_id] = {"text": text, "duration": round(len(audio) / SR, 3), "words": words}
        print(f"{line_id}: {len(audio) / SR:.2f}s")
    prev = out / "timing.json"
    if only and prev.exists():
        old = json.loads(prev.read_text())
        old["lines"].update(timing["lines"])
        timing = old
    prev.write_text(json.dumps(timing, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
