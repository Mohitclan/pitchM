"""
Load the Pitchlight end-to-end test kit for one account, so a solo tester doesn't have to upload everything by hand:
the company hub (Billing Hub + Clinic Suite), a personal client-notes document and the "Northwind call" script.

    python -m backend.seed_testkit KIT_DIR [USERNAME_OR_EMAIL]

USERNAME is only needed with AUTH_MODE=accounts (the documents and script are saved under that account).
Safe to run again: it skips anything that already exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

from backend import auth, userdata

BUSINESSES = {
    "Billing Hub": ("01-company-hub-billing-hub-summary.txt", ["02-company-hub-billing-hub-pricing-and-faq.txt",
                                                              "07-company-hub-billing-hub-deck.pdf"]),
    "Clinic Suite": ("03-company-hub-clinic-suite-summary.txt", ["03-company-hub-clinic-suite-summary.txt"]),
}
POINTS = ["Pricing: 49 dollars per clinic per month", "Works with Zoho Books", "Setup takes about two weeks",
          "Free 30 day pilot on three clinics", "Agree a follow up call next Wednesday"]
OBJECTIONS = [
    {"label": "Price", "answer": "Annual plans get two months free, and Brightsmile cut billing time by 40 percent."},
    {"label": "Competitor", "answer": "We sync to Zoho Books every night, so there is nothing to replace."},
    {"label": "Timing", "answer": "Setup takes two weeks and runs in parallel for one week."},
    {"label": "Authority", "answer": "I can send a one page summary for your CFO, Rahul."},
]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    kit = Path(argv[0]) / "docs"
    from backend.library import extract_text
    store = userdata.get_userdata()
    if auth.accounts_enabled():
        if len(argv) < 2:
            print("Accounts are on: add the username, e.g. python -m backend.seed_testkit /tmp/testkit mohit")
            return 2
        user = auth.get_store().get_user_by_email(argv[1].strip().lower())
        if not user:
            print(f"No account named {argv[1]}")
            return 1
        uid = user["user_id"]
    else:
        uid = argv[1] if len(argv) > 1 else "local"
        print(f"Accounts are off: saving under the user id {uid!r} (the one shown in the browser's 'Who's testing?' picker).")

    have = {v["name"]: v for v in store.list_ventures()}
    for name, (summary_file, doc_files) in BUSINESSES.items():
        if name not in have or not have[name]["description"]:
            store.update_venture(name, (kit / summary_file).read_text().strip())
        existing = {d["title"] for d in have.get(name, {"docs": []})["docs"]}
        for fname in doc_files:
            path = kit / fname
            title = path.stem
            if title in existing:
                continue
            store.add_doc(userdata.COMPANY_ID, title, extract_text(path.name, path.read_bytes()), name)
        print(f"Company hub: {name} ready")
    mine = {d["title"] for d in store.list_docs(uid)}
    if "Northwind client notes" not in mine:
        store.add_doc(uid, "Northwind client notes", (kit / "04-my-documents-northwind-client-notes.txt").read_text())
    print("My documents: Northwind client notes ready")
    if not any(s["name"] == "Northwind call" for s in store.list_scripts(uid)):
        store.save_script(uid, {"name": "Northwind call", "minutes": 15, "client": "Northwind Dental",
                                "text": (kit / "06-my-script-to-save.txt").read_text(), "key_points": POINTS, "objections": OBJECTIONS})
    print("Script: Northwind call ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
