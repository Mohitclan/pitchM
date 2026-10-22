# Deploying Pitchlight on one server (Docker + HTTPS)

1. Point your domain's DNS **A record** at the server's public IP. Open ports **80 and 443** (and SSH) in the firewall.
2. On the server: `git clone` the repo, then `cp .env.production.example .env` and fill it in
   (API keys, `CORS_ALLOWED_ORIGINS=https://your-domain`, `ALLOWED_EMAIL_DOMAINS`, `ADMIN_EMAILS`).
3. Start it:

   ```bash
   DOMAIN=your-domain docker compose -f docker-compose.prod.yml up -d --build
   ```

   Caddy gets the HTTPS certificate by itself the first time (DNS must already point here).
4. Open `https://your-domain`, create the first account (it becomes admin).

Notes
- Data (accounts, scripts, documents, history) lives in the Docker volume `pitchmirror_data`. `docker compose down -v` deletes it.
- Run exactly **one** copy of the app; live-call state is in memory. Deploy when nobody is in a call.
- Sign-up is open to any email (set `ALLOWED_EMAIL_DOMAINS` to restrict it). Anyone who finds the address can create an account, so share the link only with your team.
- Limits count each signed-in person, not the network address, so an office sharing one IP is fine.
- Local development is unchanged: `docker compose up -d --build` uses `docker-compose.yml` on port 8080.
