# Stage4 reusable independent verification

These tests use disposable synthetic state and repeatedly reset/import it. Never point them at a real wallet. They do not import product implementations or organizer tests. All six Python sources are byte-identical to the independent sources executed for accepted production `42400632c8fef65555b936b987ecf1970df5f540`; earlier verification sources remain unchanged.

## Prerequisites and commands

Use Python3.12+, Git, Docker, and the prepared official `df-harness-runner` image containing Python and Playwright/Chromium. See the official harness setup for building that image. The product does not depend on the verifier image. Set `DOCKER_CONTEXT` if required by your installation. Paths below are explicit placeholders; substitute your checkout and a new output directory outside it. Each orchestrator archives committed HEAD, builds standalone stages, enforces2CPU/2GiB and no runtime network, runs sequentially, preserves logs, and removes its disposable containers/images.

```sh
python /absolute/result/verification/run_stage4_independent.py \
  --repo /absolute/result --out /absolute/checks/s4-independent-new
python /absolute/result/verification/run_stage4_upgrade_checks.py \
  --repo /absolute/result --out /absolute/checks/s4-upgrades-new --migrations-only
python /absolute/result/verification/run_stage4_browser_upgrade.py \
  --repo /absolute/result --out /absolute/checks/s4-browser-upgrade-new
```

The independent runner executes **148 distinct groups**: inherited24 S1,33 S2 and45 S3, plus36 refund/batch API and10 new browser groups. It also runs ordinary-HTTP-origin and accessibility probes. Zero skipped tests is required. API groups include50-way concurrency, all10 idempotent paths, exact temporal boundaries, atomic failures and immutable snapshots/receipts.

The upgrade runner with `--migrations-only` performs actual source-destroyed1/2/3→4 migrations, preserving populated state, old responses and snapshots, then exercising new refunds/batches. Without that option it additionally repeats36 API +3 import +10 browser groups and the historical-import regression probe. Repeated executions are not new distinct coverage. The browser-upgrade runner uses the actual API-only Stage1 source under a Stage4 browser, loses a committed payment response, removes the source before import, and retries in the same nonsecure-HTTP browser without reload or relogin.

For a running **disposable** HTTP service:

```sh
python /absolute/result/verification/stage4_api.py \
  --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/s4-api-new
python /absolute/result/verification/stage4_browser.py \
  --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/s4-browser-new --insecure-host
```

The latter requires Playwright/Chromium locally; container orchestrators supply it. `--insecure-host` uses a real non-loopback HTTP origin and routes it to the disposable service; it does not enable outbound runtime networking. Browser reports include screenshots, layout, keyboard-focus and contrast metrics.

`stage4_upgrade.py` is an internal two-phase orchestration helper. Its prepare payload contains synthetic session/export state and is captured only in memory/stdin; do not invoke or publish its internal phase output. Raw exports, tokens, host repairs and execution logs are not packaged. Reports/screenshots must remain outside the repository.

Run the official isolated harness separately from the official kickoff checkout:

```sh
python -m harness run --track pocketful --repo /absolute/result \
  --stage 4 --mode isolated --out /absolute/checks/s4-official-new
```

The shipped suite observed193 passes (147+35+6+5), not a full organizer verdict. [STAGE4_ACCEPTANCE.md](STAGE4_ACCEPTANCE.md) gives accepted revision, measured evidence, limits and interpretations; [STAGE4_MATRIX.md](STAGE4_MATRIX.md) maps30 requirements. Evidence paths there are historical private workspace references relative to `checks/verifier`, not bundled files. Current Stage4 acceptance had no reproduced product failure; inherited Stage2 HTTP-key and Stage3 invalid-history-import defects, their failed evidence and repairs remain described in the earlier acceptance documents. Packaging does not change any accepted production file.
