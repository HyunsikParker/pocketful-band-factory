# Stage4 independent acceptance

Locally accepted production revision: `42400632c8fef65555b936b987ecf1970df5f540`.
Official requirements: `803560d2a678ace1414465c098eb0ab5380ffade`. Reviewer: `@skaiea13/factory-verifier`.

## Observed results

- Official isolated: **193/193** (147 + 35 + 6 + 5), zero failures/errors/skips/deselections, claimed stage **4** on shipped checks. 51.453 seconds. `s4-isolated-4240063-01/report.json`.
- Independent immutable Git archive: **148 distinct groups**, all pass: S1 basic16/expanded8, S2 API17/cross-feature3/browser13, S3 API34/import-invariants3/browser8, S4 API36/browser10. Additional ordinary-HTTP-origin and accessibility probes pass. `s4-independent-4240063-01/summary.json` and per-suite reports/results logs. New S4 API: 910 requests, 3.916 seconds, maximum request0.058018 seconds; new browser10 groups15.915 seconds.
- Stage4 requirement matrix: **30/30 rows closed** against full inherited and new requirements, not only shipped checks. `STAGE4_MATRIX.md`.
- Actual populated **1→4, 2→4, 3→4**, source container removed before import; repeat imports twice; preserve 6/12/13 original successful responses, passwords, live tokens, roles, pending requests, failed-key reuse, settlements, holds/captures where present. Stage3 migration preserves two pre/post-correction snapshots. New refund/batch retries and snapshots survive further roundtrip. `s4-upgrades-4240063-01/s{1,2,3}-to-s4/report.json`. These runs used `--migrations-only` to avoid repeating unchanged full regression suites.
- Same open browser **1→4**: actual source destruction, no reload or login/storage replacement, original payment response committed then lost, unchanged body/key replays200 after import; balance refreshes and pending request pays. Actual non-secure origin (`randomUUID` unavailable). `s4-browser-upgrade-4240063-01/browser/report.json`, 1.286 seconds.
- Container constraints observed: network **none**, **2 CPUs**, **2 GiB**, UID65532, health0.244423 seconds. Maximum measured ordinary/control API latency across independent API suites0.061100 seconds; upgrades0.055190 seconds. No outbound runtime dependency. `s4-independent-4240063-01/constraints.json`.
- Refund and Operations layouts375/1280: scroll width equals viewport, no unlabeled controls, visible3px keyboard focus;66 color pairs minimum5.443288 contrast. Four screenshots directly inspected for coherent original/current/refund distinctions and manual operator flow. Inherited wallet contrast minimum4.755115. `s4-independent-4240063-01/s4-browser/{layout,contrast}.json` and `visual-*.png`; inherited accessibility report.

## Independent review scope

Reviewed exact Stage4 delta plus inherited transaction/idempotency/affordability/history/import and browser write paths. One lock serializes all state operations. Refunds use cumulative latest-corrected cap and available funds, opposite immutable linked payments, preserve original note/privacy, and never reopen linked resources. Batches validate container shape before ordered semantic errors, include complete settlement groups at equivalent effective instants, check combined current availability then combined historical boundaries, commit shared strictly increasing recorded time atomically, and preserve originals/retries/snapshots. UI consumes only specified APIs; operators can enter private IDs manually but cannot read other parties’ histories.

50-way cases cover same-key refunds and batches, distinct refund cap, overlapping batch CAS, single-vs-batch CAS, refund-vs-correction, and mixed payments/holds/captures/void/settlements/reads. Tests also cover zero corrections,32/33 limits, multiple error precedence, submicrosecond distinct/equivalent instants, refund eligibility for all allowed targets, all ten keyed paths and frozen statements after export/import.

## Provenance and limits

`stage4-final-source-audit.json` records verifier source hashes and exact production archive equality for all9 files. Working tree remained clean, HEAD unchanged, and frozen Stage1/2/3 trees unchanged. No production edits or staged/committed verifier sources in this review. Builder67/67 and UI reports were not substituted for independent execution. No reproduced Stage4 product failures remain; current independent invocations have no failed assertion or operational failure. Earlier stages’ failures and repairs remain preserved in their own evidence.

Inherited documented interpretations remain: offline structural month-end leap-second syntax/order without announcement-table claim; exact fractional/offset comparison; simultaneous history solvency is net while ordered statement entries may be signed; original imported JSON receipts remain byte-value-equivalent even if newer nullable fields were absent; API-only Stage1 supplies the backend for the open-browser upgrade; future-created already-open seeded holds are outside the consistent-fixture interpretation. Whole batch shape validation precedes per-item semantic errors. These are local interpretations, not organizer clarifications.

This accepts the reviewed Stage4 service locally, not an organizer score, rank or prize. Coordinator fresh-clone/all-stage chain audit and participant-owned final submission artifacts are separate. No public push, submission or external contact occurred.

## Verification-source packaging

Nine new verification files were packaged after service acceptance. Six Python sources are byte-identical to the executed independent sources. All six portable CLI/import help checks passed from `result/verification`; a new offline-container packaging smoke executed all36 Stage4 API and10 Stage4 browser groups from that packaged directory, all pass with no skips. Evidence: `checks/verifier/s4-packaging-4240063-01/{cli,commands,summary}.json`, `api/report.json`, `browser/report.json`. This46-group smoke verifies relocated imports and runner dependencies; it is not counted as additional unique coverage. Accepted production and earlier verification files remain unchanged.
