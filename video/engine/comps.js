/* Both films, as data. Times are seconds. Scene `start` is absolute; everything inside a
 * scene (images, camera, cursor, labels) is relative to the scene start.
 * Capture coordinates (cam focus `f`, cursor x/y, highlight boxes) are CSS pixels of the
 * 1920×1080 captures; see assets/captures/boxes.json.
 * `vo` lists narration lines (tts/lines.json ids) and when they start. Click sounds are
 * added automatically for every cursor point with click: true. */
(function (root) {
  'use strict';

  const CTA = 'Try the PitchMirror pilot';
  const TAGLINE = 'Speak like you rehearsed.';

  const ProductOverview60 = {
    duration: 60, fps: 30,
    vo: [
      { id: 'a1', at: 1.2 }, { id: 'a2', at: 5.3 }, { id: 'a3', at: 12.3 }, { id: 'a4', at: 18.2 },
      { id: 'a5', at: 31.2 }, { id: 'a6', at: 43.5 }, { id: 'a7', at: 55.0 },
    ],
    whoosh: [11.9],
    scenes: [
      { type: 'title', start: 0, dur: 5.3, xin: 0, text: 'You know what to say.', in: 0.6 },
      { type: 'words', start: 4.8, dur: 7.5, items: [
        { text: 'Um.', t0: 0.7, t1: 2.15 },
        { text: 'Too fast.', t0: 2.15, t1: 3.35 },
        { text: 'Eyes down.', t0: 3.35, t1: 4.45 },
        { text: 'Nobody tells you.', t0: 4.45, t1: 7.3, color: '#5b6272' },
      ] },
      { type: 'brand', start: 11.8, dur: 3.8, tag: 'An AI speaking coach that listens while you talk.', tagAt: 1.2 },
      { type: 'screen', start: 15.1, dur: 15.9,
        images: [
          { t: 0, src: 'app-intake-3' }, { t: 1.6, src: 'practice-live-01' }, { t: 3.0, src: 'practice-live-02' },
          { t: 4.6, src: 'practice-live-03' }, { t: 5.6, src: 'practice-live-05' }, { t: 6.6, src: 'practice-live-06' },
          { t: 8.0, src: 'practice-live-07' }, { t: 8.8, src: 'practice-live-08' }, { t: 10.4, src: 'practice-live-10' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 6.6, s: 1 }, { t: 8.8, s: 1.55, f: [1589, 300] }, { t: 15.9, s: 1.6, f: [1589, 300] }],
        cursor: [{ t: 0, x: 700, y: 620 }, { t: 1.3, x: 224, y: 405, click: true }, { t: 2.4, x: 330, y: 560 }],
        cursorOut: 2.8,
        labels: [{ t0: 9.2, t1: 15.6, text: 'Measured, not guessed', sub: '184 wpm over the last 20 seconds' }] },
      { type: 'screen', start: 30.5, dur: 12.8,
        images: [
          { t: 0, src: 'meeting-live-02' }, { t: 1.8, src: 'meeting-live-03' }, { t: 4.0, src: 'meeting-live-05' },
          { t: 6.6, src: 'meeting-live-13' }, { t: 7.8, src: 'meeting-live-14' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 1.2, s: 1 }, { t: 3.6, s: 2.15, f: [960, 175] }, { t: 12.8, s: 2.2, f: [960, 175] }],
        labels: [{ t0: 7.2, t1: 12.6, text: 'Silent nudges', sub: 'Text only. Nobody else in the call sees them.' }] },
      { type: 'screen', start: 42.8, dur: 3.5,
        images: [{ t: 0, src: 'practice-score' }],
        cam: [{ t: 0, s: 1.44, f: [1168, 360] }, { t: 3.5, s: 1.5, f: [1168, 360] }],
        labels: [{ t0: 1.2, t1: 3.4, text: 'A scored review' }] },
      { type: 'screen', start: 45.8, dur: 2.9,
        images: [{ t: 0, src: 'review-meeting-qa' }],
        cam: [{ t: 0, s: 1.45, f: [1168, 330] }, { t: 2.9, s: 1.5, f: [1168, 330] }],
        labels: [{ t0: 0.5, t1: 2.8, text: 'Every question, a stronger answer' }] },
      { type: 'screen', start: 48.2, dur: 6.4,
        images: [{ t: 0, src: 'review-meeting-email' }],
        cam: [{ t: 0, s: 1.42, f: [1168, 270] }, { t: 6.4, s: 1.5, f: [1168, 270] }],
        labels: [{ t0: 0.6, t1: 6.0, text: 'Follow-up email, drafted', sub: 'Copy it, or open it in your mail app' }] },
      { type: 'brand', start: 54.1, dur: 5.9, xout: 0, tag: TAGLINE, cta: CTA, tagAt: 1.1, ctaAt: 1.9, holdToEnd: true, fadeOutAt: 4.9 },
    ],
  };

  const ProductWalkthrough90 = {
    duration: 90, fps: 30,
    vo: [
      { id: 'b1', at: 0.9 }, { id: 'b2', at: 6.3 }, { id: 'b3', at: 15.5 }, { id: 'b4', at: 25.6 }, { id: 'b5', at: 38.6 },
      { id: 'b6', at: 50.4 }, { id: 'b7', at: 64.4 }, { id: 'b8', at: 78.1 }, { id: 'b9', at: 86.6 },
    ],
    whoosh: [],
    scenes: [
      { type: 'brand', start: 0, dur: 6.3, xin: 0, tag: 'Coached while you speak.', tagAt: 1.8 },
      { type: 'chips', start: 5.8, dur: 9.6, head: 'Measured live, while you talk', items: [
        { text: 'Fillers', t: 4.3 }, { text: 'Pace', t: 4.8 }, { text: 'Eye contact', t: 5.3 },
      ] },
      { type: 'screen', start: 14.9, dur: 10.7,
        images: [
          { t: 0, src: 'app-idle' }, { t: 3.55, src: 'app-intake-1' }, { t: 4.65, src: 'app-intake-2' },
          { t: 5.95, src: 'app-intake-3' }, { t: 7.75, src: 'practice-live-01' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 1.0, s: 1 }, { t: 2.8, s: 1.55, f: [300, 860] }, { t: 6.3, s: 1.55, f: [300, 860] },
          { t: 7.2, s: 1.3, f: [420, 520] }, { t: 8.4, s: 1, f: null }, { t: 10.7, s: 1 }],
        cursor: [{ t: 0.4, x: 900, y: 640 }, { t: 3.4, x: 224, y: 825, click: true }, { t: 4.5, x: 224, y: 891, click: true },
          { t: 5.8, x: 224, y: 1024, click: true }, { t: 7.6, x: 224, y: 405, click: true }, { t: 8.6, x: 360, y: 560 }],
        cursorIn: 1.0, cursorOut: 9.0,
        labels: [{ t0: 3.4, t1: 7.4, text: 'Pick your coach and goal', sub: 'Brutal VC · Pitch · Improve pacing', right: true }] },
      { type: 'screen', start: 25.1, dur: 13.5,
        images: [
          { t: 0, src: 'practice-live-02' }, { t: 1.4, src: 'practice-live-03' }, { t: 2.0, src: 'practice-live-04' },
          { t: 2.8, src: 'practice-live-05' }, { t: 3.8, src: 'practice-live-06' }, { t: 5.2, src: 'practice-live-07' },
          { t: 5.8, src: 'practice-live-08' }, { t: 6.6, src: 'practice-live-09' }, { t: 8.2, src: 'practice-live-10' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 1.6, s: 1 }, { t: 3.2, s: 1.45, f: [880, 300] }, { t: 4.6, s: 1.45, f: [880, 300] },
          { t: 6.2, s: 1.6, f: [1589, 320] }, { t: 13.5, s: 1.66, f: [1589, 320] }],
        labels: [
          { t0: 2.6, t1: 4.7, text: 'Live transcript' },
          { t0: 6.4, t1: 13.2, text: 'What was measured', sub: 'and how to fix it', right: true },
        ] },
      { type: 'screen', start: 38.1, dur: 12.3,
        images: [
          { t: 0, src: 'meeting-setup-1' }, { t: 3.15, src: 'meeting-setup-2' }, { t: 3.6, src: 'meeting-notes-0.35' },
          { t: 4.0, src: 'meeting-notes-0.7' }, { t: 4.4, src: 'meeting-notes-1' }, { t: 4.8, src: 'meeting-points-1' },
          { t: 5.2, src: 'meeting-points-2' }, { t: 5.6, src: 'meeting-points-3' }, { t: 6.15, src: 'meeting-setup-3' },
          { t: 7.15, src: 'meeting-setup-4' }, { t: 10.6, src: 'meeting-live-01' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 1.4, s: 1 }, { t: 3.0, s: 1.4, f: [960, 470] }, { t: 6.4, s: 1.4, f: [960, 500] },
          { t: 7.6, s: 1.5, f: [1000, 700] }, { t: 9.8, s: 1.5, f: [1000, 700] }, { t: 11.2, s: 1, f: null }],
        cursor: [{ t: 0.6, x: 1300, y: 600 }, { t: 3.0, x: 640, y: 364, click: true }, { t: 3.6, x: 740, y: 470 },
          { t: 4.6, x: 1180, y: 480 }, { t: 6.0, x: 742, y: 585, click: true }, { t: 7.0, x: 1286, y: 711, click: true },
          { t: 10.4, x: 609, y: 824, click: true }, { t: 11.2, x: 760, y: 900 }],
        cursorIn: 0.6, cursorOut: 11.4,
        labels: [
          { t0: 3.4, t1: 6.6, text: 'Pre-call brief', sub: 'Context and the points you must cover' },
          { t0: 7.4, t1: 10.3, text: 'Hear both sides', sub: "Share the meeting tab's audio" },
        ] },
      { type: 'screen', start: 49.9, dur: 14.5,
        images: [
          { t: 0, src: 'meeting-live-02' }, { t: 2.6, src: 'meeting-live-03' }, { t: 5.1, src: 'meeting-live-05' },
          { t: 7.6, src: 'meeting-live-09' }, { t: 8.9, src: 'meeting-live-13' }, { t: 10.0, src: 'meeting-live-14' },
          { t: 11.5, src: 'meeting-peek' }, { t: 12.75, src: 'meeting-help-ask' },
        ],
        cam: [{ t: 0, s: 1 }, { t: 0.6, s: 1 }, { t: 2.6, s: 2.15, f: [960, 175] }, { t: 14.5, s: 2.2, f: [960, 175] }],
        cursor: [{ t: 11.0, x: 1110, y: 300 }, { t: 12.4, x: 1002, y: 145, click: true }, { t: 13.6, x: 1060, y: 260 }],
        cursorIn: 11.0, cursorOut: 14.3,
        labels: [
          { t0: 5.4, t1: 7.5, text: 'Answer outline', sub: 'When the client asks a question' },
          { t0: 7.7, t1: 11.2, text: 'Points & nudges', sub: 'Text only. Nobody else sees them.' },
          { t0: 12.9, t1: 14.4, text: 'Help on demand', sub: 'Answer · Catch up · Ask' },
        ] },
      { type: 'screen', start: 63.9, dur: 14.2,
        images: [
          { t: 0, src: 'review-meeting-top' }, { t: 2.6, src: 'review-meeting-recap', xf: 0.6 },
          { t: 5.6, src: 'review-meeting-email', xf: 0.6 }, { t: 8.65, src: 'review-meeting-email-copied' },
        ],
        cam: [{ t: 0, s: 1.46, f: [1168, 420] }, { t: 2.4, s: 1.48, f: [1168, 420] }, { t: 3.6, s: 1.5, f: [1168, 420] }, { t: 5.4, s: 1.5, f: [1168, 420] },
          { t: 6.8, s: 1.5, f: [1168, 270] }, { t: 7.6, s: 1.5, f: [1168, 270] }, { t: 8.6, s: 1.85, f: [1520, 200] }, { t: 14.2, s: 1.9, f: [1520, 200] }],
        cursor: [{ t: 6.6, x: 1300, y: 420 }, { t: 8.5, x: 1708, y: 114, click: true }, { t: 9.6, x: 1740, y: 170 }],
        cursorIn: 6.6, cursorOut: 13.8,
        labels: [
          { t0: 3.3, t1: 5.6, text: 'Meeting recap', sub: 'Decisions, action items, points covered' },
          { t0: 6.4, t1: 13.8, text: 'Follow-up email', sub: 'Drafted for you. Copy in one click.' },
        ] },
      { type: 'screen', start: 77.6, dur: 9.0,
        images: [{ t: 0, src: 'review-meeting-qa' }, { t: 4.9, src: 'review-meeting-transcript', xf: 0.5 }],
        cam: [{ t: 0, s: 1.45, f: [1168, 320] }, { t: 4.6, s: 1.5, f: [1168, 320] }, { t: 5.4, s: 1.46, f: [1168, 330] }, { t: 9.0, s: 1.5, f: [1168, 330] }],
        highlights: [{ t0: 5.8, t1: 8.9, box: [1070, 107, 90, 22], r: 6 }],
        labels: [
          { t0: 0.6, t1: 4.6, text: 'Question-by-question review', sub: 'A rating and a stronger sample answer' },
          { t0: 5.6, t1: 8.8, text: 'Full transcript', sub: 'Download as .txt' },
        ] },
      { type: 'brand', start: 86.1, dur: 3.9, xout: 0, tag: TAGLINE, cta: CTA, tagAt: 0.8, ctaAt: 1.3, holdToEnd: true, fadeOutAt: 3.1 },
    ],
  };

  const COMPS = { ProductOverview60, ProductWalkthrough90 };
  if (typeof module !== 'undefined' && module.exports) module.exports = COMPS;
  root.COMPS = COMPS;
})(typeof window !== 'undefined' ? window : globalThis);
