# Deploying Life Inbox on Oracle Cloud (Always Free)

This puts Life Inbox on the internet with HTTPS, on Oracle Cloud's **Always
Free** tier, so the public API, webhooks and MCP server can reach it.

```
Internet ──HTTPS──> Caddy (password gate + certificate) ──> Life Inbox (web) ──> PostgreSQL (db)
                    └──────────── one Oracle VM, three Docker containers ───────────┘
```

> **Cost:** everything below stays inside the Always Free limits (Ampere A1:
> up to 4 OCPUs / 24 GB RAM, 200 GB disk). The trial credit is not needed. Pick
> "Always Free eligible" shapes only.

> **Login:** real sign-in is being built by the auth teammate. Until it is
> merged, Caddy asks for one shared username/password for the whole site.
> Programs using an API token (`Bearer li_...`) skip that prompt.

## 1. Create the VM (Oracle console, ~10 minutes)

1. **Compute → Instances → Create instance**.
2. **Image:** Canonical Ubuntu 24.04.
3. **Shape:** *Ampere* → `VM.Standard.A1.Flex`, **2 OCPU, 12 GB** (plenty; free).
   - If you see *"Out of host capacity"*, try another availability domain or
     try again later. This is common for free A1 shapes.
4. **Networking:** keep "Assign a public IPv4 address" on.
5. **SSH keys:** upload your public key (`~/.ssh/id_ed25519.pub`) or download
   the generated one.
6. Create, then copy the **public IP**, e.g. `140.238.1.2`.

## 2. Open ports 80 and 443

Two firewalls must allow web traffic: Oracle's network one **and** the one
inside Ubuntu.

**Oracle network (console):** Instance → *Subnet* → *Security list* →
**Add ingress rules**: source `0.0.0.0/0`, TCP, destination ports `80,443`.

**Inside the VM** (Oracle's Ubuntu images block these by default):

```bash
ssh ubuntu@140.238.1.2
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit   # log out and back in so the group change applies
```

## 4. Get the code and write `.env`

```bash
ssh ubuntu@140.238.1.2
git clone https://github.com/missmartin99/Life-Inbox.git
cd Life-Inbox
```

Make a password hash for the site gate:

```bash
docker run --rm caddy:2 caddy hash-password --plaintext 'choose-a-strong-password'
```

Create `.env` (it is never committed or copied into the image):

```bash
nano .env
```

```bash
# Your address. sslip.io turns any IP into a hostname for free: use dashes.
DOMAIN=140-238-1-2.sslip.io

# Site password gate (single quotes keep the $ signs in the hash literal)
SITE_USER=team
SITE_PASSWORD_HASH='$2a$14$...paste the hash here...'

# Secrets: make each one long and random, e.g. `openssl rand -hex 24`
POSTGRES_PASSWORD=...
LIFE_INBOX_SESSION_SECRET=...
LIFE_INBOX_TIMEZONE=Asia/Kolkata

# Until real login is merged: dev login is safe here ONLY because the whole
# site is behind the password gate above.
LIFE_INBOX_ALLOW_DEV_LOGIN=1

# AI (optional) -- DeepSeek through the OpenAI-compatible API
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

## 5. Start it

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.oracle.yml up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.oracle.yml logs -f web
```

The web container runs `alembic upgrade head` on start, then the server.
The first semantic search downloads the ~90 MB embedding model.

Open `https://140-238-1-2.sslip.io`. You should get a padlock and a
username/password prompt.

## 6. Create an API token (for scripts and the MCP server)

```bash
SITE=https://140-238-1-2.sslip.io
# log in (dev login, behind the gate) and keep the cookie
curl -u team:choose-a-strong-password -c cookies.txt -X POST $SITE/api/auth/dev-login \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com"}'
# create a token -- copy it now, it is shown only once
curl -u team:choose-a-strong-password -b cookies.txt -X POST $SITE/api/tokens \
  -H 'Content-Type: application/json' -d '{"name":"my laptop"}'
```

Test it (no site password needed with a token):

```bash
curl -H "Authorization: Bearer li_..." "$SITE/api/search?q=rent"
```

## 7. Connect Claude Desktop (MCP)

The MCP server runs on **your laptop** and calls the server with your token.
In Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "life-inbox": {
      "command": "/path/to/Life-Inbox/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/Life-Inbox",
      "env": {
        "LIFE_INBOX_URL": "https://140-238-1-2.sslip.io",
        "LIFE_INBOX_TOKEN": "li_..."
      }
    }
  }
}
```

Restart Claude Desktop, then ask: *"What's in my Life Inbox about rent?"*

## Updating and backups

```bash
cd Life-Inbox && git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.oracle.yml up -d --build

# Database backup to a file
docker compose exec db pg_dump -U lifeinbox lifeinbox > backup-$(date +%F).sql
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser can't connect at all | Port 80/443 not open in the security list **or** the iptables step was skipped |
| Certificate error | `DOMAIN` doesn't match the IP, or port 80 is blocked (Let's Encrypt needs it) |
| `Set SITE_PASSWORD_HASH in .env` | `.env` missing or the variable name is misspelled |
| Login prompt rejects the password | The hash lost its `$` signs: put it in single quotes in `.env` |
| 401 `Invalid API token` | Token revoked or mistyped; create a new one |
