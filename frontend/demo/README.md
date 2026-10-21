The client's lines are rendered from `backend/demo_kit.py` STEPS (`client` text) with the local Kokoro image used for
the product films (`pitchmirror-kokoro`, voice af_bella, speed 0.95):

    # lines.json = {"voice": "af_bella", "speed": 0.95, "lines": {"c1": "...", ...}}
    docker run --rm -v "$PWD":/work pitchmirror-kokoro python speak.py lines.json out   # speak.py is video/tts/speak.py
    for f in out/c*.wav; do afconvert -f m4af -d aac -b 64000 $f frontend/demo/audio/$(basename $f .wav).m4a; done

Re-render whenever a `client` line changes.
