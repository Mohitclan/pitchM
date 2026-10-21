"""
Pitchlight — the built-in practice meeting ("test mode") for Meeting Mode.

Every new user can rehearse a dummy sales call before a real client call. The fictional company, its documents, the
script and the client's questions all live here, so everyone gets the same thing and one edit updates it for all.
Nothing here is ever stored in the real company hub or a person's own documents: a practice meeting loads this kit
in memory only (see `practice_meeting` in main.py).

The client's voice lines are pre-rendered files in frontend/demo/audio/<step id>.m4a (see demo_kit_audio.py).
"""
from __future__ import annotations

CLIENT = "Northwind Dental"
VOICE = {"engine": "kokoro", "voice": "af_bella", "speed": 0.95}

NOTES = (
    "NORTHWIND DENTAL: CLIENT NOTES (practice meeting, fictional)\n"
    "Contact: Priya Sharma, Head of Operations. 22 clinics across three cities.\n"
    "Today they use spreadsheets plus Zoho Books. Month-end close takes the finance team about six days.\n"
    "Priya's worries: price, setup time (we cannot disrupt the front desk) and whether it works with Zoho Books.\n"
    "Her CFO, Rahul Mehta, approves anything over 20,000 dollars a year.\n"
    "Goal of the call: agree a 30-day pilot on three clinics and a follow-up call next Wednesday."
)

POINTS = [
    "Pricing: 49 dollars per clinic per month",
    "Works with Zoho Books",
    "Setup takes about two weeks",
    "Free 30 day pilot on three clinics",
    "Agree a follow up call next Wednesday",
]

OBJECTIONS = (
    "Price: Annual plans get two months free, and Brightsmile cut billing time by 40 percent.\n"
    "Competitor: We sync to Zoho Books every night, so there is nothing to replace.\n"
    "Timing: Setup takes two weeks and runs in parallel for one week.\n"
    "Authority: I can send a one page summary for your CFO, Rahul."
)

SCRIPT = """## Opening
Thanks for making the time today, Priya.
I will show you how Billing Hub puts every clinic payment and insurance claim in one dashboard.
We will finish with a clear next step, if it makes sense for you.

## Their situation
Today your team tracks payments in spreadsheets and Zoho Books.
Month end close takes your finance team about six days.
Is that still the biggest pain for you?

## What we do
Billing Hub collects every patient payment across all your clinics in one dashboard.
It tracks every open insurance claim and flags the ones that are overdue.
Payments sync to Zoho Books every night, so your ledger stays up to date.

## Proof
Brightsmile Dental Group runs Billing Hub across fourteen clinics.
They cut billing time by forty percent in the first quarter.

## Price and fit
Billing Hub costs forty nine dollars per clinic per month.
Annual plans get two months free.
Setup takes about two weeks and does not disrupt your front desk.

## Next step
I suggest a free thirty day pilot on three of your clinics.
Can we book a follow up call next Wednesday to agree the details?
"""

# The company hub of the fictional company (searched like the real hub, but only inside a practice meeting).
HUB_DOCS = [
    {"doc_id": "demo1", "venture": "Billing Hub", "title": "Billing Hub · About", "text": (
        "Billing Hub centralises patient payments and insurance claims for dental and medical clinics. It replaces card "
        "machines, spreadsheets and manual claim follow-ups with one dashboard showing every payment, every open claim "
        "and every outstanding balance across all of a group's clinics. It is sold per clinic per month to clinic groups "
        "with five or more locations.")},
    {"doc_id": "demo2", "venture": "Billing Hub", "title": "Billing Hub · Pricing and FAQ", "text": (
        "Pricing\nBilling Hub costs 49 US dollars per clinic per month. Annual plans get two months free. There is no "
        "setup fee for groups with ten or more clinics; smaller groups pay a one-time setup fee of 500 dollars. A 30-day "
        "pilot on up to three clinics is free.\n\n"
        "Setup\nSetup takes about two weeks. We import patient balances and open claims from your current system, train "
        "the front-desk team in one 60-minute session, and run in parallel for one week before you switch over.\n\n"
        "Integrations\nBilling Hub integrates with Zoho Books, QuickBooks Online and Xero. Payments sync to your ledger "
        "every night. It also connects to Clinic Suite with a one-click sync.\n\n"
        "Proof\nBrightsmile Dental Group runs Billing Hub across 14 clinics. They cut the time their team spends on "
        "billing by 40 percent in the first quarter and reduced unpaid claims older than 60 days by a third.\n\n"
        "Data and security\nAll data is encrypted at rest and in transit. Servers are hosted in the EU. If you stop using "
        "Billing Hub you can export all of your data as CSV files at any time and for 30 days after you cancel.\n\n"
        "Support\nSupport is included: email and chat during business hours, and a named customer success manager for "
        "groups with ten or more clinics.")},
    {"doc_id": "demo3", "venture": "Clinic Suite", "title": "Clinic Suite · About and pricing", "text": (
        "Clinic Suite is practice management software for dental clinics. It handles appointment booking, patient "
        "charting, treatment plans and automatic appointment reminders by text and email. Clinic Suite costs 199 US "
        "dollars per clinic per month and includes onboarding. It works with Billing Hub through a one-click sync, so "
        "completed treatments appear in Billing Hub as ready-to-bill items without re-typing. Clinic Suite is a separate "
        "product with its own team, but customers can buy both together at a ten percent bundle discount.")},
]

# What each step expects to see; the browser ticks it when the matching server message arrives.
#   expect: script (script lines covered), mood, answer_company, answer_clinic, objection, not_in_notes, lost, talk,
#           answer_data, commit, finish, none
STEPS = [
    {"id": "c1", "feature": "Script follow-along", "expect": "script",
     "client": "Hi, thanks for joining. I only have about thirty minutes, so please go ahead.",
     "you": "Read the Opening and Their situation sections of the script (6 lines). Your own words are fine.",
     "watch": "Script lines fade as you speak and the next line is highlighted."},
    {"id": "c2", "feature": "Client mood and coach advice", "expect": "mood",
     "client": "Yes, month end is painful. It takes us six days, sometimes seven.",
     "you": "Read the What we do section (3 lines).",
     "watch": "A client mood chip appears, and the coach may suggest a next move."},
    {"id": "c3", "feature": "Answer from the company hub", "expect": "answer_company",
     "client": "How much does Billing Hub cost per clinic, and is there a setup fee?",
     "you": "Wait for the answer card in the Answers panel, then answer from it. You can also tap Answer in the coach bar.",
     "watch": "An outline with 49 dollars and no setup fee for 10+ clinics, sourced from the company hub."},
    {"id": "c4", "feature": "A business you did not pick", "expect": "answer_clinic",
     "client": "We also use Clinic Suite for appointments. Does it work with Billing Hub, and what does Clinic Suite cost?",
     "you": "Answer from the card: one-click sync, 199 dollars per clinic per month, 10 percent bundle discount.",
     "watch": "The client named another business, so the answer comes from that business's documents."},
    {"id": "c5", "feature": "Objection card", "expect": "objection",
     "client": "Honestly, forty nine dollars a clinic feels expensive. We already have Zoho Books, so I am not sure we need another tool.",
     "you": "Use the card: annual plans get two months free, we sync to Zoho Books every night, Brightsmile cut billing time by forty percent. Then tap Catch up and Ask in the coach bar.",
     "watch": "An objection card with your prepared answer first. Catch up and Ask give a short summary and a question to ask back."},
    {"id": "c6", "feature": "No invented facts", "expect": "not_in_notes",
     "client": "Do you have SOC 2 certification? Our compliance team will definitely ask about that.",
     "you": "Say: Good question. I will confirm that and send you the details by Friday.",
     "watch": "The answer says it is not in your notes or the company hub and suggests confirming. Nothing is invented. Your promise lands in Commitments."},
    {"id": "c7", "feature": "I'm lost button", "expect": "lost",
     "client": "Hmm. I am still not convinced this would really save us any time.",
     "you": "Skip the Proof section on purpose. Read Price and fit instead. Then say 'um, so, like, you know, um, basically' for ten seconds. Finally press I'm lost in the Script panel.",
     "watch": "A skeptical client mood, a filler-word nudge, and I'm lost pointing you to the next unspoken line."},
    {"id": "c8", "feature": "Talk-time nudge", "expect": "talk",
     "client": "Tell me more about how the setup would work for twenty two clinics.",
     "you": "Talk without stopping for about 75 seconds: describe the rollout (import balances, one 60 minute training, one week in parallel, then switch).",
     "watch": "After about a minute a nudge asks you to check in or let the client speak."},
    {"id": "c9", "feature": "Answer about your data", "expect": "answer_data",
     "client": "What happens to our data if we ever stop using it?",
     "you": "Answer from the card: CSV export at any time and for 30 days after cancelling.",
     "watch": "An outline sourced from the Billing Hub FAQ."},
    {"id": "c10", "feature": "Commitments and next step", "expect": "commit",
     "client": "Okay, that sounds reasonable. Can you send me a proposal by Friday? And let us talk again next Wednesday.",
     "you": "Read the Next step section. Add: I will send the proposal by Friday.",
     "watch": "The Commitments list shows your promises and the points to cover tick off."},
    {"id": "c11", "feature": "Review after the call", "expect": "finish",
     "client": "Great, thank you. Talk soon.",
     "you": "Say thanks and goodbye, then press End meeting.",
     "watch": "A recap, a follow-up email, a question review, and a readiness check that flags the skipped Proof section."},
]


def kit_payload() -> dict:
    """What the browser needs to run a practice meeting."""
    return {
        "client": CLIENT, "notes": NOTES, "points": POINTS, "objections": OBJECTIONS, "minutes": 15,
        "mode": "sales_demo", "persona": "coach", "chatty": "active", "script_text": SCRIPT,
        "steps": STEPS, "audio_base": "/static/demo/audio/",
    }


def hub_library():
    from backend import library
    return library.Library(HUB_DOCS)


def script_sections() -> list[dict]:
    from backend import script_follow
    return script_follow.parse_script_text(SCRIPT)
