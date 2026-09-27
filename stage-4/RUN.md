# Run Pocketful — Stage 3

From this folder, build and start the single-container API:

```sh
docker build -t pocketful-stage-3 .
docker run --rm --name pocketful-stage-3 --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 pocketful-stage-3
```

Readiness: `curl http://localhost:8080/health` returns `{"status":"ok"}`.
The process listens on `0.0.0.0`, using `PORT` (default `8080`). To use a
different internal port, change both `-e PORT=9090` and `-p 9090:9090`.

The image includes Python 3.12 and all service code. There are no third-party
Python packages or runtime downloads; no outbound network access is needed.
It runs as an unprivileged numeric user. Open `http://localhost:8080/` for the
wallet. All scripts, styles and runtime assets are included in the image, with
system fonts and no external CDN. API responses are JSON. `/requests` and
`/authorizations` serve HTML for `Accept: text/html`, and JSON otherwise.

## State and API

The service starts with empty in-memory state. Initialize it by posting the
specified fixture JSON to `/_test/reset`. Seeded balances already include
seeded payments: resetting never replays them. Currency and minor-unit scale
come from the fixture. Signup creates an account with zero balance.

Log in through `/auth/login`, then send `Authorization: Bearer <token>`.
`/payments`, `/requests`, `/requests/{id}/pay`, `/splits`, `/settlements`,
`/authorizations`, `/authorizations/{id}/capture`, and `/payments/{id}/corrections`
require `Idempotency-Key`. Monetary amounts are integer minor units. Settlement
operator permissions come from the fixture.

`GET /_test/export` creates an atomic snapshot; `POST /_test/import` restores
that complete JSON object into another service instance. Test controls are
intentionally unauthenticated. Exports contain password hashes and live session
tokens: keep them private and do not commit or print them. Reset replaces all
state. Restarting the container discards state; no volume or setup step is needed.

The engine serializes state operations under one reentrant lock. Passwords use
salted PBKDF2-HMAC-SHA256 digests. Sessions and successful retry receipts belong
to exportable state rather than process-local caches. No deposits, external
payment integrations, or later-stage endpoints are included.

## Browser flows and upgrades

Create an account at `/signup` or use a seeded account at `/login`. The wallet
at `/` supports payments, requests, and authorizations. `/requests` shows both
directions, `/split` previews exact shares before creating requests, and
`/authorizations` supports capture (including optional nonfinal captures) and
release. Available funds are the headline; total and held funds remain visible.

The browser retains form values after a write or refresh. A changed input starts
a new payment attempt; unchanged retries reuse the original body and key.
A missing response is marked uncertain, not refused. Click the same action to
recover its receipt without paying twice. Refresh uses only the latest requested
read, and never clears the payment form. There is no background polling.

Stage-1 and stage-2 exports can be imported without regenerating sessions or retry receipts.
An already-open browser retains its form and pending attempt through an import
between requests. The session is stored in the browser’s local storage; signing
out removes it from this browser without invalidating other active sessions.
No page-reload recovery of an unfinished form is required or promised.

## Statements, history and corrections

Open `/statements` for a saved, paginated view of your own money movements. The
JSON API stays at `/statement`. From is inclusive, to is exclusive; optional
known-at selects the information available at that instant. Previous/next keep
the same server snapshot and full-window balances even after new payments or
corrections. Create a fresh statement to include later changes.

Open `/history` to read original receipts and revision history, or to query total,
held and available funds at an inclusive as-of instant. All time filters accept
RFC 3339 timestamps with `Z` or an explicit offset. Current wallet funds remain
separate from the labeled historical view.

Original senders can correct eligible direct/request payments from their receipt.
The amount replaces the current amount; zero reverses it. The original receipt,
activity entry and earlier retry responses stay unchanged. A stale revision
refusal keeps the draft editable: review the latest history, choose "Use latest
revision", then submit. An uncertain response keeps the original body and key
for a safe retry, including on ordinary HTTP origins. Captures and settlement
members cannot be corrected individually. Refunds and correction batches are
not included in this stage.
