# Turkish Translation Bot

بوت تيليجرام لترجمة احترافية بين العربية والتركية عبر OpenRouter، مع لوحة admin تعرض تحليل كل طبقة.

## Features

- Telegram bot with Arabic -> Turkish and Turkish -> Arabic buttons.
- `/guide` command explaining the workflow and translation layers.
- Seven-layer AI translation pipeline through OpenRouter.
- Final Telegram answer in a Markdown code block for easy copying.
- FastAPI admin dashboard protected by one admin username/password.
- Full persistence of source text, final translation, layer analysis, status, timings, and errors.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data
uvicorn app.main:app --reload
```

For local development, keep `DATABASE_URL=sqlite+aiosqlite:///./data/app.db`.

## Environment Variables

- `TELEGRAM_BOT_TOKEN`: Telegram bot token from BotFather.
- `OPENROUTER_API_KEY`: OpenRouter API key.
- `ADMIN_USERNAME`: Dashboard username.
- `ADMIN_PASSWORD`: Dashboard password.
- `POSTGRES_DB`: PostgreSQL database name for Docker deployment.
- `POSTGRES_USER`: PostgreSQL user for Docker deployment.
- `POSTGRES_PASSWORD`: PostgreSQL password for Docker deployment.
- `DATABASE_URL`: Optional PostgreSQL or SQLite SQLAlchemy URL. Docker Compose builds it from the PostgreSQL variables.
- `OPENROUTER_MODEL`: Model used for all layers unless code config is changed.
- `APP_BASE_URL`: Public app URL, sent to OpenRouter as metadata.
- `SESSION_SECRET`: Random secret for dashboard sessions.
- `BOT_POLLING`: `true` to run Telegram polling in the FastAPI process.

## VPS Deployment

Docker Compose:

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

The production Compose file is designed for the VPS layout under `/opt/projects/turkish-translation`.
It joins the existing external `proxy` network and exposes the app through Traefik at:

```text
https://turkish-translation.junkpro.duckdns.org
```

systemd:

```bash
sudo cp deploy/turkish-translation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now turkish-translation
```

## Tests

```bash
pytest
```
