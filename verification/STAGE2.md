# Independent Stage-2 verification

Tests derive from the complete Pocketful requirements, not the shipped test implementation. They reset disposable targets and use synthetic accounts. No production module is imported. Prior `stage1_acceptance.py` and `stage1_expanded.py` are shared HTTP helpers/regression suites, unchanged.

## Docker orchestration

Requires Docker, Python3.12+, and an existing `df-harness-runner` image with Python Playwright/Chromium (the official prepared runner). Does not change Docker contexts or install/configure a daemon. Output must be a new directory outside this repository. Builds immutable `git archive HEAD` snapshots, records commands and source hashes, limits services to2CPU/2GiB/networknone, and cleans up only owned containers/images.

```sh
DOCKER_CONTEXT=colima python verification/run_stage2_independent.py --repo "$PWD" --out ../checks/s2-independent-new
DOCKER_CONTEXT=colima python verification/run_stage2_independent.py --repo "$PWD" --out ../checks/s2-regression-new --regression
DOCKER_CONTEXT=colima python verification/run_stage2_upgrade.py --repo "$PWD" --out ../checks/s2-upgrade-new
```

The first command runs inherited16+8API groups,17newAPI groups,13browser groups and an HTTP-origin probe. `--regression` runs the HTTP-origin probe,13browser groups on an actual insecure HTTP hostname,3cross-feature groups and the keyboard/contrast audit. `--extra-only` selects the separately defined3cross-feature groups and audit; it does not deselect individual tests. Services have no network interface beyond loopback; test runner containers share that namespace. The insecure-host browser option appends a synthetic hostname to `/etc/hosts` **inside the disposable root-owned runner only**; do not use that option on a host.

Upgrade orchestration runs actual frozenS1 andS2 image processes. The browser obtains real legacy credentials through login, loses a committed payment response, exports only to memory, signals the host orchestrator to removeS1, and imports after absence is verified. It then retries the unchanged open form/body/key and pays an existing request. No reload, localStorage editing or re-login occurs at migration. Stage1 source revision21a65c9 is identified by its frozen tree in this repository. Check tree identity before reusing this claim with a different repository.

## Direct target runners

`stage2_api.py`, `stage2_browser.py`, `stage2_http_origin.py`, `stage2_cross_feature.py` and `stage2_accessibility.py` each accept `--url`, `--revision` and `--out`. Targets are destructively reset. Playwright is needed only for browser programs. `stage2_upgrade_browser.py` is intended for the orchestration handshake, not an uncoordinated direct invocation. See each `--help`.

Reports contain counts, timing, source metadata and synthetic UI screenshots, not raw exported state or bearer tokens. Failed reports remain untouched. Tests log human-readable assertions; run only against disposable synthetic fixtures.

## Evidence boundaries

API17 and inherited24 groups plus cross-feature3 and browser13 are57 distinct groups. Do not count reruns or duplicated inherited tests as new coverage. The HTTP-origin probe, accessibility audit and populated migration are additional scenarios, not unittest groups. One cross-feature test has a historical name mentioning expiry; its assertions cover partial-capture import and receipt replay. No-traffic expiry is covered by the17-group API suite.

The official isolated harness remains separate and must run against the exact committed source. Green shipped tests are not complete acceptance. These tools found a genuine insecureHTTP `crypto.randomUUID` failure despite public green; the product fix uses `getRandomValues` while preserving cached attempt identity. Browser coverage is Chromium, not all engines/assistive technologies. Finite timing samples do not guarantee arbitrary workload performance.
