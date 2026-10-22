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

## If the server already runs nginx (ports 80/443 taken)
Don't use `docker-compose.prod.yml` (its Caddy can't bind those ports). Keep `docker-compose.yml`, and:
1. Point the DNS A record for your subdomain at the server.
2. Copy `deploy/nginx-pitchlight.conf` into nginx, replace `YOUR_DOMAIN`, run `sudo nginx -t`, reload, then `sudo certbot --nginx -d YOUR_DOMAIN`.
3. In `.env` set `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `CORS_ALLOWED_ORIGINS=https://YOUR_DOMAIN`, then `docker compose up -d --force-recreate`.
4. Close port 8080 to the internet (cloud firewall / security group) so people can only use https.
