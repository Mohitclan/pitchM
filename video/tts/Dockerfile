# Local Kokoro TTS (https://github.com/hexgrad/kokoro), CPU only.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir "kokoro>=0.9.4" soundfile
# Pre-download the model weights and the voices we compare, so renders work offline.
RUN python -c "from kokoro import KPipeline; p=KPipeline(lang_code='a'); [list(p('Warm up.', voice=v)) for v in ('af_heart','af_bella','am_michael')]"
WORKDIR /work
