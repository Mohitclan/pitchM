// Demo data for the walkthrough captures. Fictional company and people; nothing here is
// real user data. It is played into the REAL PitchMirror pages through a mocked
// WebSocket / API, so no Gemini or Claude call is made while capturing.

export const NOW = Math.floor(Date.UTC(2026, 9, 20, 10, 30) / 1000);

/* ── Practice session (Brutal VC · Pitch · Improve pacing) ── */
export const practiceScript = [
  // [seconds into the session, message]
  [0.0, { type: 'status', state: 'connected', session_id: 'demo-practice-01', coach_mode: 'pitch', delivery_context: 'virtual', primary_goal: 'improve_pacing' }],
  [0.4, { type: 'status', state: 'listening' }],
  [1.0, { type: 'transcript', speaker: 'user', text: "Hi, I'm Alex, and this is Brightpath. Um, small clinics lose hours every week on the phone just booking appointments." }],
  [2.0, { type: 'transcript_end', speaker: 'user' }],
  [2.4, { type: 'transcript', speaker: 'user', text: 'So, uh, we built a booking assistant that, like, fills the schedule and, um, sends reminders automatically.' }],
  [2.8, { type: 'metric', key: 'filler', value: 4 }],
  [3.0, { type: 'coach_tip', index: 0, issue_type: 'filler', ts: 14, source: 'sensor',
    description: 'Four fillers in the last thirty seconds — "um", "uh", "like".',
    evidence: { metric: 'fillers_30s', measured: 4, threshold: '≥3', sensor: 'transcript' } }],
  [3.3, { type: 'coach_said', index: 0, text: 'Four fillers in thirty seconds. Pause instead.' }],
  [3.4, { type: 'transcript', speaker: 'coach', text: 'Four fillers in thirty seconds. Pause instead.' }],
  [3.6, { type: 'transcript_end', speaker: 'coach' }],
  [4.2, { type: 'transcript', speaker: 'user', text: 'It takes ten minutes to set up and clinics see fewer no-shows and the front desk can focus on patients instead of the phone and we already have three clinics live and' }],
  [4.8, { type: 'metric', key: 'pace', value: 1 }],
  [5.0, { type: 'coach_tip', index: 1, issue_type: 'pace', ts: 31, source: 'sensor',
    description: 'You are speaking at 184 words per minute over the last 20 seconds.',
    evidence: { metric: 'wpm_20s', measured: 184, threshold: '>170', sensor: 'transcript' } }],
  [5.3, { type: 'coach_said', index: 1, text: 'Slow down. Finish the sentence, then breathe.' }],
  [5.4, { type: 'transcript_end', speaker: 'user' }],
  [5.5, { type: 'transcript', speaker: 'coach', text: 'Slow down. Finish the sentence, then breathe.' }],
  [5.7, { type: 'transcript_end', speaker: 'coach' }],
  [6.4, { type: 'transcript', speaker: 'user', text: "Let me slow down. We're starting with dental clinics. Three are using Brightpath today." }],
];

const practiceTranscript = [
  ['u', 2, "Hi, I'm Alex, and this is Brightpath. Um, small clinics lose hours every week on the phone just booking appointments."],
  ['u', 9, 'So, uh, we built a booking assistant that, like, fills the schedule and, um, sends reminders automatically.'],
  ['c', 15, 'Four fillers in thirty seconds. Pause instead.'],
  ['u', 18, 'It takes ten minutes to set up and clinics see fewer no-shows and the front desk can focus on patients instead of the phone and we already have three clinics live and'],
  ['c', 32, 'Slow down. Finish the sentence, then breathe.'],
  ['u', 35, "Let me slow down. We're starting with dental clinics. Three are using Brightpath today."],
  ['u', 44, 'Each clinic pays a flat monthly fee per location. We are raising a seed round to reach fifty clinics by next summer.'],
  ['u', 55, "The ask today is simple: we'd love your feedback on our go-to-market, and an intro to one dental group."],
];

export const practiceScorecard = {
  session_id: 'demo-practice-01', user_id: 'demo', coach_mode: 'pitch', session_kind: 'practice', platform: '',
  delivery_context: 'virtual', primary_goal: 'improve_pacing', screen_enabled: false, demo_mode: false,
  total_slides: 0, current_slide_index: 0, created_at: NOW - 3600, duration_seconds: 68,
  overall_score: 74,
  categories: {
    filler_words: { score: 62, count: 4, label: '4 fillers — mostly in the opening' },
    eye_contact: { score: 84, drops: 0, label: 'Steady on camera' },
    pace: { score: 62, violations: 1, label: 'One burst at 184 wpm' },
    clarity: { score: 84, contradictions: 0, flags: 0, label: 'Clear problem and ask' },
    visual_delivery: { score: 84, slide_clarity_flags: 0, slide_mismatch_flags: 0, label: 'No slides used' },
    prosody: { score: 84, pitch_variance_hz: 31.5, pause_ratio_20s: 0.18, speaking_energy: 0.6, monotony_score: 0.3, label: 'Good variety' },
  },
  coaching_events: [
    { timestamp: 14, type: 'filler', text: 'Four fillers in the last thirty seconds — "um", "uh", "like".', source: 'sensor',
      evidence: { metric: 'fillers_30s', measured: 4, threshold: '≥3', sensor: 'transcript' }, coach_said: 'Four fillers in thirty seconds. Pause instead.', user_rating: 'up' },
    { timestamp: 31, type: 'pace', text: 'You are speaking at 184 words per minute over the last 20 seconds.', source: 'sensor',
      evidence: { metric: 'wpm_20s', measured: 184, threshold: '>170', sensor: 'transcript' }, coach_said: 'Slow down. Finish the sentence, then breathe.', user_rating: 'up' },
  ],
  timeline_events: [],
  transcript: practiceTranscript.map(([s, ts, t]) => ({ s, t, ts, te: ts + 6 })),
  final_report: [
    '## What worked',
    '- A clear problem in the first sentence: clinics lose hours to phone booking.',
    '- A specific ask at the end — feedback on go-to-market and one intro.',
    '',
    '## Fix next',
    '- **Fillers in the opening.** Four in thirty seconds. Replace each with a silent pause.',
    '- **Pace under pressure.** You hit 184 wpm when listing benefits. One benefit per sentence.',
    '',
    '## Try this next run',
    '1. Open with the problem, then pause for one beat.',
    '2. Say the three benefits as three short sentences.',
  ].join('\n'),
  research_tips: '', generated_assets: [], answer_hints: [], qa_review: [],
  brief: { notes: '', points: [], planned_minutes: 0 }, recap: {}, client_moods: [], interruptions: 0,
};

/* ── Meeting session (Sales call · Google Meet) ── */
export const meetingBrief = {
  notes: 'Intro call with Northwind Dental (3 clinics). They book by phone today and see a lot of no-shows.',
  points: ['Ten-minute setup', 'Price per clinic', 'Agree a pilot clinic'],
  minutes: '30',
};

const P = (covered) => meetingBrief.points.map((text, i) => ({ text, covered: !!covered[i] }));

export const meetingScript = [
  [0.0, { type: 'status', state: 'connected', session_id: 'demo-meeting-01' }],
  [0.3, { type: 'brief_points', points: P([0, 0, 0]) }],
  [1.0, { type: 'transcript', speaker: 'other', text: 'Thanks for making time. Before we go further — how long does setup actually take?' }],
  [4.2, { type: 'transcript_end', speaker: 'other' }],
  [4.5, { type: 'answer_hint', ts: 42, question: 'How long does setup actually take?',
    points: ['About ten minutes: connect the calendar, import services', 'We move existing bookings over for them', 'Offer to set up one clinic together this week'] }],
  [8.0, { type: 'transcript', speaker: 'user', text: 'Great question. It takes about ten minutes — you connect your calendar, import your services, and we move your existing bookings over for you.' }],
  [10.0, { type: 'brief_points', points: P([1, 0, 0]) }],
  [11.0, { type: 'transcript_end', speaker: 'user' }],
  // The outline folds away by itself after 25 s (HINT_HOLD_MS); the next exchange starts after that.
  [31.0, { type: 'transcript', speaker: 'other', text: 'Okay. And what would it cost us for three locations?' }],
  [31.6, { type: 'client_mood', mood: 'skeptical', reason: 'Asking about cost before seeing value' }],
  [33.6, { type: 'transcript_end', speaker: 'other' }],
  [34.6, { type: 'transcript', speaker: 'user', text: "Um, so, it's, uh, per clinic, so it's, like — it's a flat monthly fee for each location." }],
  [35.6, { type: 'coach_tip', index: 0, issue_type: 'filler', ts: 71, source: 'sensor',
    description: 'Four fillers while answering the price question.',
    evidence: { metric: 'fillers_30s', measured: 4, threshold: '≥3', sensor: 'transcript' } }],
  [35.9, { type: 'coach_cue', index: 0, text: 'Pause. Say the price plainly.' }],
  [39.5, { type: 'transcript', speaker: 'user', text: ' One forty-nine per clinic, per month, and the first month is free.' }],
  [40.0, { type: 'brief_points', points: P([1, 1, 0]) }],
  [40.6, { type: 'transcript_end', speaker: 'user' }],
  [41.5, { type: 'client_mood', mood: 'engaged', reason: 'Asking about next steps' }],
];

export const meetingHelpReplies = {
  ask: { type: 'help_reply', kind: 'ask', lines: ['Which of your three clinics would be the best place to start a pilot?'] },
  catchup: { type: 'help_reply', kind: 'catchup', lines: ['They asked about setup time and cost for three clinics', 'They sounded unsure about price — now asking about next steps'] },
  answer: { type: 'help_reply', kind: 'answer', lines: ['Flat fee per clinic, first month free', 'Tie it to time saved at the front desk', 'Suggest a one-clinic pilot'] },
};

const meetingTranscript = [
  ['o', 40, 'Thanks for making time. Before we go further — how long does setup actually take?'],
  ['u', 47, 'Great question. It takes about ten minutes — you connect your calendar, import your services, and we move your existing bookings over for you.'],
  ['o', 63, 'Okay. And what would it cost us for three locations?'],
  ['u', 68, "Um, so, it's, uh, per clinic, so it's, like — it's a flat monthly fee for each location. One forty-nine per clinic, per month, and the first month is free."],
  ['o', 84, 'That is reasonable. What would a trial look like?'],
  ['u', 89, 'We would start with one clinic for four weeks. I set it up with your front desk, and we review no-shows together at the end.'],
  ['o', 104, 'Let us start with the Elm Street clinic. Can you send something over?'],
  ['u', 109, "Absolutely. I'll send a summary and a pilot plan today."],
];

export const meetingScorecard = {
  session_id: 'demo-meeting-01', user_id: 'demo', coach_mode: 'sales_demo', session_kind: 'meeting', platform: 'google_meet',
  delivery_context: 'virtual', primary_goal: 'balanced', screen_enabled: false, demo_mode: false,
  total_slides: 0, current_slide_index: 0, created_at: NOW, duration_seconds: 1394,
  overall_score: 81,
  categories: {
    filler_words: { score: 62, count: 4, label: '4 fillers — all on the price question' },
    eye_contact: { score: 84, drops: 0, label: 'Camera not tracked' },
    pace: { score: 84, violations: 0, label: 'Steady, 142 wpm average' },
    clarity: { score: 96, contradictions: 0, flags: 0, label: 'Clear, specific answers' },
    visual_delivery: { score: 84, slide_clarity_flags: 0, slide_mismatch_flags: 0, label: '—' },
    prosody: { score: 84, pitch_variance_hz: 28.1, pause_ratio_20s: 0.2, speaking_energy: 0.55, monotony_score: 0.3, label: 'Good variety' },
  },
  coaching_events: [
    { timestamp: 71, type: 'filler', text: 'Four fillers while answering the price question.', source: 'sensor',
      evidence: { metric: 'fillers_30s', measured: 4, threshold: '≥3', sensor: 'transcript' }, coach_said: 'Pause. Say the price plainly.', user_rating: 'up' },
  ],
  timeline_events: [],
  transcript: meetingTranscript.map(([s, ts, t]) => ({ s, t, ts, te: ts + 6 })),
  final_report: [
    '## How the call went',
    'You answered both buying questions directly and closed on a concrete pilot.',
    '',
    '## Fix next',
    '- **Price answers.** Four fillers before the number. Lead with the price, then the reason.',
  ].join('\n'),
  research_tips: '', generated_assets: [],
  answer_hints: [{ ts: 42, question: 'How long does setup actually take?', points: ['About ten minutes: connect the calendar, import services', 'We move existing bookings over for them', 'Offer to set up one clinic together this week'] }],
  qa_review: [
    { ts: 40, question: 'How long does setup actually take?', rating: 5,
      answer: 'About ten minutes — connect your calendar, import services, and we move existing bookings over.',
      verdict: 'Direct and concrete.', worked: 'You gave a number first, then the steps.',
      improve: 'Close with an offer: "Want me to set up one clinic with you this week?"',
      hint_points: ['About ten minutes: connect the calendar, import services', 'We move existing bookings over for them', 'Offer to set up one clinic together this week'],
      better_answer: "About ten minutes. You connect your calendar, import your services, and we move your existing bookings for you. Want to set up one clinic together this week?" },
    { ts: 63, question: 'What would it cost us for three locations?', rating: 3,
      answer: "Um, so, it's, uh, per clinic… a flat monthly fee for each location. One forty-nine per clinic, per month, first month free.",
      verdict: 'Right answer, hesitant delivery.', worked: 'Clear price and a free first month.',
      improve: 'Say the number first. Drop the four fillers before it.',
      better_answer: "It's $149 per clinic per month, so $447 for all three — and the first month is free. Most clinics cover that with two fewer no-shows a month." },
  ],
  brief: { notes: meetingBrief.notes, points: meetingBrief.points.map((text, i) => ({ text, covered: true, ts: [52, 81, 104][i] })), planned_minutes: 30 },
  recap: {
    summary: 'Intro call with Northwind Dental. They asked about setup time and price for three clinics, and agreed to a four-week pilot at the Elm Street clinic.',
    decisions: ['Run a four-week pilot at the Elm Street clinic'],
    action_items: [
      { owner: 'Alex', task: 'Send call summary and pilot plan', due: 'Today' },
      { owner: 'Northwind', task: 'Share front-desk contact at Elm Street', due: 'This week' },
    ],
    open_questions: ['Who signs off on rolling out to the other two clinics?'],
    client_signals: ['Price-sensitive at first', 'Warmed up once the pilot was concrete'],
    follow_up_email: {
      subject: 'Brightpath pilot at Elm Street — next steps',
      body: [
        'Hi Jordan,',
        '',
        'Thanks for your time today. As discussed:',
        '',
        '• Setup takes about ten minutes, and we move your existing bookings for you.',
        '• Pricing is $149 per clinic per month, and the first month is free.',
        '• We will run a four-week pilot at the Elm Street clinic and review no-shows together at the end.',
        '',
        'Could you share the best front-desk contact at Elm Street? I will set up a time this week.',
        '',
        'Best,',
        'Alex',
      ].join('\n'),
    },
  },
  client_moods: [{ ts: 63, mood: 'skeptical', reason: 'Asking about cost' }, { ts: 84, mood: 'engaged', reason: 'Asking about next steps' }],
  interruptions: 0,
};

export const historyList = [
  { session_id: 'demo-meeting-01', overall_score: 81, created_at: NOW, duration_seconds: 1394, coach_mode: 'sales_demo', session_kind: 'meeting', platform: 'google_meet', words: 212, coaching_moments: 1 },
  { session_id: 'demo-practice-01', overall_score: 74, created_at: NOW - 3600, duration_seconds: 68, coach_mode: 'pitch', session_kind: 'practice', delivery_context: 'virtual', words: 131, coaching_moments: 2 },
];
