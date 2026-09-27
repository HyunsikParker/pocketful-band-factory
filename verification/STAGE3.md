# Stage3 reusable independent verification

These specification-derived tests are destructive only to disposable service instances: they repeatedly reset and import synthetic state. They do not import product code or organizer tests. Earlier verification sources remain unchanged.

Requirements: Python3.12+ on the host, Docker, and the official prepared `df-harness-runner` image (Python and Playwright/Chromium). The standalone product has no dependency on this verification image. Set `DOCKER_CONTEXT` when your installation requires it. Run containers sequentially; each service is limited to2CPU/2GiB with network none. Output directories must be fresh and outside the result repository. Runners archive committed HEAD before building, so commit production before testing it.

```sh
python /absolute/result/verification/run_stage3_independent.py \
  --repo /absolute/result --out /absolute/checks/s3-independent-new
python /absolute/result/verification/run_stage3_upgrade_checks.py \
  --repo /absolute/result --out /absolute/checks/s3-upgrades-new
python /absolute/result/verification/run_stage3_browser_upgrade.py \
  --repo /absolute/result --out /absolute/checks/s3-browser-upgrade-new
```

The independent runner executes102 distinct functional groups:24 inherited S1,17 S2 API,3 cross-feature,13 S2 browser,34 S3 API,3 temporal-import and8 new browser groups. It also runs HTTP-origin and accessibility probes. The upgrade runner repeats34+3+8 groups, the original import-defect probe, and two actual source-destroyed S1/S2→S3 migrations. Repeated executions and duplicate repros are not unique coverage. The browser upgrade runner removes its actual S1 source, imports into S3 and preserves the already-open insecure-HTTP client's session, pending request and unchanged lost-response retry body/key.

For an already-running disposable HTTP service:

```sh
python verification/stage3_api.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/api-new
python verification/stage3_import_invariants.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/import-new
python verification/stage3_import_probe.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/repro-new
python verification/stage3_browser.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/browser-new --insecure-host
```

Browser checks need Playwright/Chromium in their execution environment; the container runners provide this. `--insecure-host` routes a real non-loopback HTTP browser origin to the local service, covering the secure-context key-generation regression. It does not grant runtime outbound networking.

`stage3_upgrade.py` is an orchestrated two-phase helper. Its private preparation payload includes synthetic export/session state and is captured in process memory/stdin only. Invoke it through the upgrade runner; do not run or publish its internal preparation output. Reports and screenshots stay in private check directories, never in the repository. No runtime credentials, raw exports, host repairs or measured logs are packaged here.

See [STAGE3_ACCEPTANCE.md](STAGE3_ACCEPTANCE.md) and [STAGE3_MATRIX.md](STAGE3_MATRIX.md) for exact accepted production revision, measured results, preserved failures, evidence provenance and interpretations. Referenced private evidence paths are historical workspace-relative references, not files bundled with these sources. Run the official isolated harness separately: independent tests and partial shipped tests complement source/spec review, neither establishes an organizer score.
