# Stage3 independent acceptance matrix — CLOSED — 5cf06c1 local acceptance

Official specification revision: `803560d2a678ace1414465c098eb0ab5380ffade`.
Production copy baseline: `03ccfabfd7ff42e45e060045bb7d8de4ecd037ee`.
Full S1/S2 requirements remain inherited. All28 rows closed against final5cf06c136945e935a1c90c9bd23bbb946055480b and audited unchanged inherited code. See STAGE3_ACCEPTANCE.md for executed evidence, prior rejection and limits.

| Row | Requirement | Independent evidence | Status |
|---|---|---|---|
| 01 | Net seed balances, immutable openings, original times, future-seed atomic rejection | API seeded/opening/future groups | PASS (evidence map below) |
| 02 | Inclusive as-of, exact echo, future instants, default current | API temporal/bitemporal groups | PASS (evidence map below) |
| 03 | RFC3339 syntax, equivalent offsets, fractional precision, lowercase, leap interpretation | API four parser groups; final-source exact-time audit | PASS (evidence map below) |
| 04 | Statement half-open window, effective-time then ID order, full-window balances | API half-open/pagination/oracle | PASS (evidence map below) |
| 05 | Statement parties-only privacy, auth, integer query ranges, unknown fields | API statement privacy/ranges | PASS (evidence map below) |
| 06 | Correction required fields, zero, exact money, reason limits, future rejection | API field/zero/large-money | PASS (evidence map below) |
| 07 | Original-sender write, two-party revisions, 403/404/401 | API correction authorization | PASS (evidence map below) |
| 08 | Difference transfers, immutable openings, original receipts/feed/retries | API difference/direct replay/request replay | PASS (evidence map below) |
| 09 | Current available debit rejection precedes historical total/available | API current funds + historical funding + historical hold | PASS (evidence map below) |
| 10 | Atomic historical rejection, combined same-instant boundary, conservation | API grouped funding/oracle/rollback | PASS (evidence map below) |
| 11 | Latest recorded revision <= known-at; selected effective time, zero entries | API bitemporal oracle/effective move | PASS (evidence map below) |
| 12 | Strict recorded ordering, CAS, successful original replay, failed key reuse | API replay + two 50-way correction races | PASS (evidence map below) |
| 13 | Eighth keyed path: key bounds, JSON numeric equality, ignored-field equality | API correction numeric equality | PASS (evidence map below) |
| 14 | Snapshot frozen revisions/default window/full balances, independent page size | API frozen window and concurrent 50 reads/writes | PASS (evidence map below) |
| 15 | Snapshot ownership, recognized-filter exclusivity, reset, export/import | API snapshot permissions + source-destroyed upgrade | PASS (evidence map below) |
| 16 | Settlement/capture immutable links, original shared timestamps, request payments correctable | API linked + request correction groups | PASS (evidence map below) |
| 17 | Historical held lifecycle, partial/final/void, known-time event selection | API partial/final/void groups | PASS (evidence map below) |
| 18 | Clock expiry known after creation, future view, exact closed_at, no fake payments | API future expiry + real partial expiry | PASS (evidence map below) |
| 19 | Seeded hold supplied/default creation, closed seeds need not invent lifecycle | API supplied creation; final source audit for default/closed | PASS (evidence map below) |
| 20 | Actual S1->S3 and S2->S3 with source destroyed, tokens/hash login/roles/keys/pending/holds | `stage3_upgrade.py` twice; immutable source image hashes | PASS (evidence map below) |
| 21 | Inherited S1 behavior, seven S2 keyed writes, mixed available/capture/settlement races | Existing independent S1 24 + S2 17 API + cross-feature 3 groups | PASS (evidence map below) |
| 22 | Inherited browser flows, lost retry, stale refresh, decimals, hold UI | Existing S2 browser13 at insecure HTTP; source-destroyed open-browser upgrade adapted to S3 | PASS (evidence map below) |
| 23 | New statement/history/correction product, original vs corrected labeling, zero | New browser route/zero/filter groups | PASS (evidence map below) |
| 24 | New correction local validation, stale editable refusal, explicit latest, uncertain same body/key | New browser validation/stale/lost groups | PASS (evidence map below) |
| 25 | Stable browser previous/next, explicit fresh statement | New browser frozen paging group | PASS (evidence map below) |
| 26 | 375/1280 layout, labels/focus/contrast, negative ordered entry display, safe user text | New browser layout + inherited accessibility evaluator + direct screenshot review | PASS (evidence map below) |
| 27 | Standalone Docker/RUN, offline, 2CPU/2GiB, <=60s health, <=5/10s requests, 50 concurrency | Immutable source/image constrained runs; official isolated fresh output | PASS (evidence map below) |
| 28 | Earlier stage frozen, no Stage4 overshoot, exact committed source/identity | Git tree/diff audit; official next-stage probe | PASS (evidence map below) |

## Disclosed interpretations

- Historical solvency is checked after the combined net movements at one instant; statement ID-ordered intermediate `balance_after` may be negative without a negative wallet at a boundary. The UI must format it correctly, with ASCII or typographic minus acceptable.
- RFC3339 leap seconds use the coordinator/core accepted offline structural interpretation: `:60` only in the UTC month-end insertion position, equivalent offsets accepted, fractional leap instants remain distinct from next midnight. This is not a live leap-announcement/table verification claim.
- S1 is API-only. Existing-browser upgrade uses the S2/S3 static client with API initially routed to the actual predecessor, then export/import and route switch without reload, re-login, localStorage modification or attempt identity change; source destroyed first.
- Original imported idempotent responses stay byte-equivalent as parsed JSON even if newer responses introduce nullable fields. New ledger views may add their documented fields.
- Inherited Stage2 TTL representability interpretation remains: exact representable deadline, atomic422 if no RFC3339-representable deadline; no silent saturation/arbitrary cap.

## Preparation artifacts

`stage3_api.py`: 34 independent API groups, no production or shipped-test imports. A deterministic selected-revision oracle computes expected histories.

`stage3_browser.py`: 8 new browser groups based on the interface's published selector contract, not evolving source. Reuses only independently authored S2 browser helpers.

`stage3_upgrade.py`: two source-destroyed container scenarios, source stage selected by CLI. Private synthetic export/session data remains in captured subprocess memory/stdin; output records only measured summaries.

Evidence map: rows01–14,16–19 use s3-upgrades-5cf06c1-01/api/report.json (34groups) plus committed-source audit; row15 also import-invariants (3groups); row20 uses that run’s s1-to-s3 and s2-to-s3 reports; row21 uses unchanged-code inherited s3-independent-d19d695-01 plus final official suites; row22 adds s3-browser-upgrade-5cf06c1-01; rows23–26 use final browser/report.json,layout.json,new-screen-contrast.json and directly inspected final375/1280 screenshots; row27 uses final constrained runner commands plus s3-isolated-5cf06c1-01; row28 uses final Git tree/diff audit and official Stage4 failure. Original invalid-history import failure is preserved and resolved by original-import-probe plus import-invariants on finalrevision. No duplicated run is counted as unique coverage.
