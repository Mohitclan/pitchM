# Pitchlight for a whole company — accounts and rollout

Pitchlight can run for many people, each with their own sign-in and their own private history, goals,
pitches, clients and recordings. Nobody can see anyone else's data.

## How it works for people
1. They open the landing page, click **Sign in**, then **Create account** (name, a username or work email, password).
2. They land in the app. Their History, goals, pitch versions and past-call memory are theirs alone.
3. Next time: **Sign in**. **Sign out** is in the name menu (top right).

**Sign-up is open:** anyone who can reach the app can create an account with a username (or an email) and a
password. That is the default, locally and in production — leave `ALLOWED_EMAIL_DOMAINS` unset.
(If you ever want to limit sign-up to your company, set `ALLOWED_EMAIL_DOMAINS=yourcompany.com`; people then
have to use an email in that domain.)

## Testing on one computer (what we did first)
1. Put `AUTH_MODE=accounts` in `.env` (leave `ALLOWED_EMAIL_DOMAINS` out) and run `docker compose up -d --build`.
2. Open http://localhost:8080 → **Sign in** → **Create account**. **The first account created becomes the admin**,
   so create yours first.
3. Your friend signs out (name menu → Sign out) and creates their own account, or signs in if they have one.
   Everything each person does is saved under their own account.
4. To bring your earlier history into your account:
   `docker compose exec pitchmirror python -m backend.auth adopt-data user-9a47e525-fbb yourusername`
5. To go back to the old single-person picker, delete the `AUTH_MODE` line and rebuild.

## Switch it on
In `.env` (see `.env.example`):

```
AUTH_MODE=accounts
ADMIN_EMAILS=yourusername               # optional: who may open the pilot-feedback summary
```

Then recreate the container: `docker compose up -d --build`. Leaving `AUTH_MODE` out keeps the old
single-computer "Who's testing?" picker.

## Things you may need to do
| Task | Command (run in the project folder) |
|---|---|
| See everyone with an account | `docker compose exec pitchmirror python -m backend.auth list` |
| Someone forgot their password | `docker compose exec pitchmirror python -m backend.auth reset-password their@company.com` |
| Make someone an admin | `docker compose exec pitchmirror python -m backend.auth make-admin their@company.com` |
| Attach history saved *before* accounts existed to a person | `docker compose exec pitchmirror python -m backend.auth adopt-data OLD_USER_ID their@company.com` |

Old history belongs to the id shown by the old picker (for example `user-9a47e525-fbb`). Run `adopt-data`
once per person who wants it.

## Before going live for the whole company
- **Use HTTPS** on your own address (for example `pitchlight.company.com`). Browsers only allow the
  microphone and camera on HTTPS, and sign-in cookies are marked Secure automatically behind HTTPS.
- Set `ENVIRONMENT=production` and `CORS_ALLOWED_ORIGINS=https://your-address`. (Production refuses to start
  without `AUTH_MODE=accounts` or an `API_BEARER_TOKEN`.)
- **Keep the data volume** (`pitchmirror_data`) and back it up. It holds accounts and everyone's history.
  Accounts live in the same SQLite file as the sessions.
- **Open sign-up means everyone who can reach the address can use the app, and every session costs credits**
  (about $0.30 of Claude per meeting). If the server is open to the internet, watch the usage on your Claude and
  Gemini accounts, or keep the address on your company network / VPN.
- Use company keys, not personal ones: a paid Gemini key (the free tier can't serve many people talking
  at once) and an Anthropic key for the company workspace.
- Tell staff that Meeting Mode transcribes the other people on a call, and agree when it may be used.
- Pilot with 5–10 people first. The feedback summary (`/pilot`, admins only) collects their notes.

## Limits of this simple version
- No email verification and no "forgot password" email; an admin resets passwords with the command above.
- No two-step sign-in. Passwords are stored hashed (scrypt), never in plain text.
- Admins can see the feedback summary and the account list, but not other people's transcripts.
