# Run Pocketful — Stage 1

From this folder, build and start the single-container API:

```sh
docker build -t pocketful-stage-1 .
docker run --rm --name pocketful-stage-1 --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 pocketful-stage-1
```

Readiness: `curl http://localhost:8080/health` returns `{"status":"ok"}`.
The process listens on `0.0.0.0`, using `PORT` (default `8080`). To use a
different internal port, change both `-e PORT=9090` and `-p 9090:9090`.

The image includes Python 3.12 and all service code. There are no third-party
Python packages or runtime downloads; no outbound network access is needed.
It runs as an unprivileged numeric user. All HTTP responses use JSON; this
stage deliberately has no browser interface.

## State and API

The service starts with empty in-memory state. Initialize it by posting the
specified fixture JSON to `/_test/reset`. Seeded balances already include
seeded payments: resetting never replays them. Currency and minor-unit scale
come from the fixture. Signup creates an account with zero balance.

Log in through `/auth/login`, then send `Authorization: Bearer <token>`.
`/payments`, `/requests`, `/requests/{id}/pay`, `/splits`, and `/settlements`
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
