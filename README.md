# Personal Finance App

A private finance tracker that runs only on this computer. No cloud, no login,
no other users. The spec is `finance-app-brief.md`.

## Running it

    run.bat

Then open http://127.0.0.1:8000 in a browser.

## Setting up Plaid (not needed yet)

1. Copy `.env.example` to `.env`
2. Paste the client_id and secret from dashboard.plaid.com into it
3. `.env` is gitignored and must never be committed

## Where things are

- `app/db.py` is the database schema
- `app/main.py` is the web server
- `app/static/` is the page you see in the browser
- `finance.db` is your data, a single file, created on first run
