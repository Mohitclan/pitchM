/* Backup transcription for YOUR microphone, used only while the server's transcriber is down
   (for example the Gemini quota ran out). Chrome's built-in recognition turns your speech into text and
   sends each finished sentence to the server, which handles it like a normal transcript line.
   It cannot hear other people's meeting audio, so the client's side has no backup. */
window.STTFallback = (function () {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let send = null, rec = null, wanted = false, onChange = null;

  function start() {
    if (!Rec || rec) return;
    rec = new Rec();
    rec.continuous = true;
    rec.interimResults = false;
    rec.lang = document.documentElement.dataset.sttLang || 'en-IN';
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (!e.results[i].isFinal) continue;
        const text = (e.results[i][0].transcript || '').trim();
        if (text && send) send({ type: 'browser_transcript', text: text.slice(0, 600) });
      }
    };
    rec.onend = () => {            // Chrome ends the session now and then; restart while still needed
      rec = null;
      if (wanted) setTimeout(start, 400);
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') wanted = false;
    };
    try { rec.start(); } catch (_) { rec = null; }
  }

  function stop() {
    wanted = false;
    if (rec) { try { rec.stop(); } catch (_) { /* ignore */ } rec = null; }
  }

  return {
    supported: !!Rec,
    init(sendFn) { send = sendFn; },
    /** Call with each server stt_status for the presenter: 'ok' | 'recovering' | 'failed'. */
    onStatus(state) {
      if (!Rec) return;
      const need = state === 'failed' || state === 'recovering';
      if (need && !wanted) { wanted = true; start(); if (onChange) onChange(true); }
      if (!need && wanted) { stop(); if (onChange) onChange(false); }
    },
    stop,
    onChange(fn) { onChange = fn; },
  };
})();
