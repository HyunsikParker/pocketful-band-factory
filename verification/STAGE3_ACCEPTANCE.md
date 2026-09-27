# Stage3 independent acceptance — ACCEPTED LOCALLY

Production revision: `5cf06c136945e935a1c90c9bd23bbb946055480b`.
Stage3 tree: `77306c65e74ce44cc3eabbfabaed4d4c4ae70693`.
Official specification: `803560d2a678ace1414465c098eb0ab5380ffade`.
Reviewer: `skaiea13/factory-verifier`. No production edits by reviewer.

## Decision and demonstrated repair

All 28 requirement-matrix rows close with the interpretations below. No unresolved demonstrated specification defect remains. This is local independent acceptance, not organizer acceptance, a score, rank or award.

The rejected predecessor `d19d695411560e0677527233fa898d6de404a854` accepted a malformed imported history whose ending balances were consistent but whose past total/available were negative. Original failure remains in `s3-invalid-history-d19d695-01/report.json`, and the rejection report remains in `stage3-review-d19d695.md`.

The correction changes only `stage-3/engine.py`, 10 added lines. It validates a separate replacement Engine's historical grouped money/hold boundaries before publishing state, mapping historical insolvency to 422. Both author and committer are `factory-core <factory-core@local.invalid>`. Final worktree is clean and `git diff --check` passes.

Independent original repro on final revision now returns `422 validation_failed`, leaves destination exactly unchanged, and retains historical total/available 0. Additional malformed closed-hold history (current hold zero, past available negative) is also rejected atomically. A valid corrected export retains old/new snapshots and its original correction response.

## Final-revision executed evidence

Paths below are relative to `checks/verifier/` in the factory workspace.

| Evidence | Observed result |
|---|---|
| `s3-isolated-5cf06c1-01/report.json` | Official isolated: S1 147/147, S2 35/35, S3 6/6; 188 total, no skips/deselections; highest contiguous3, claimed3. Stage4 probe fails as required. 06:24:42.563–06:25:31.409UTC. |
| `s3-upgrades-5cf06c1-01/api/report.json` | 34/34 independent S3 API groups; 932 requests; 6.7487s; maximum request0.057706s. Includes temporal oracle, exact fractions/offsets, hold lifecycle, current/historical precedence, 50-way CAS/replay and snapshot races. |
| `s3-upgrades-5cf06c1-01/import-invariants/report.json` | 3/3 independent import groups: past total, past available after void, unchanged corrected export with snapshots/retry. |
| `s3-upgrades-5cf06c1-01/original-import-probe/report.json` | Original failure reproduced against repair:422, correct code, unchanged destination. Not counted as another unique group. |
| `s3-upgrades-5cf06c1-01/browser/report.json` | 8/8 new browser groups in10.6555s at actual insecure HTTP hostname; zero page errors/external requests. Correction zero/validation/stale/uncertain exact retry, frozen statement paging, historical filters, signed net-settlement entry. |
| `s3-upgrades-5cf06c1-01/s1-to-s3/report.json` | Actual S1 source destroyed before import; two replacement imports;6 original keyed responses, tokens/password login/roles, pending payment, failed-key reuse and new ledger/snapshot/correction retry preserved. Maxrequest0.054865s. |
| `s3-upgrades-5cf06c1-01/s2-to-s3/report.json` | Actual S2 source destroyed; two imports;12 original keyed responses and inherited state plus open/partial/final/void hold history and subsequent lifecycle actions preserved. Maxrequest0.054996s. |
| `s3-browser-upgrade-5cf06c1-01/browser/report.json` | Real S1 source removed; already-open client recovers committed lost payment with same body/key and 200, keeps session/form and pays pending request after import without reload/re-login/storage edit. Origin `http://pocketful-service:8080`, secure=false, randomUUID undefined. |

The upgrade runners archive exact committed source before building; command/image/build/cleanup evidence is in each run directory. Services run network none, 2CPU and2GiB; no runtime outbound dependencies. The official isolated run separately supplies its constrained runtime evidence. All temporary review containers were removed; final `docker ps` is empty.

## Inherited evidence and counts

The sole final patch touches import validation, not API writes, time selection, rendering or assets. Broader unchanged inherited behavior was independently executed on d19 in `s3-independent-d19d695-01`: S1 24 groups, S2 API17, cross-feature3, S2 browser13, insecure-origin probe and accessibility. These 57 groups are carried with source-diff audit, not falsely labeled final-revision reruns. Final official suites and both predecessor migrations exercise the changed import path on5cf06c1.

There are 102 distinct independent functional groups overall:57 inherited +34 temporal API +3 import +8 new browser. Reruns and the duplicate original repro are not added. Probes/accessibility/migrations are reported separately. The initial d19 API run's erroneous post-void correction expectation was a test-oracle defect; original failed evidence is retained and the corrected test demands historical rejection before a valid smaller correction.

## Visual and source review

Final `browser/layout.json`:375/1280 CSS-pixel routes have scrollWidth equal viewport, no unlabeled controls, visible3px focus. `browser/new-screen-contrast.json`:38 sampled text/background pairs, minimum5.4433. Directly inspected final375 history,1280 history and375 statement screenshots after complete receipt render: readable original/selected revision distinction, ordered signed entries, responsive controls and coherent navigation. The visible skip link/focus ring is intentional keyboard evidence. Inherited UI accessibility evidence remains unchanged (minimum4.755).

Source audit verifies immutable originals separate from revisions; immutable openings; RLock serialization; exact temporal selection; frozen owner-bound deep-copied snapshots; current availability before historical solvency; grouped simultaneous boundaries; safe text rendering; BigInt money formatting; getRandomValues keys and cached unchanged-body retry. Docker/RUN/runtime assets were reviewed. No later-stage refunds/batch surface was added.

Frozen predecessor trees remain S1 `f0157dfb0ebfa07efa03f01601e138d8cd243dd7`, S2 `c56f4ad06c0cf9da50cf33c8fadff9aff9db8470`.

## Explicit limitations and interpretations

- Same-instant solvency uses aggregate boundary movements. An ID-ordered statement intermediate can be negative without a negative wallet at that instant; signed display is tested.
- RFC3339 leap handling is the agreed offline structural interpretation (UTC month-end insertion position, equivalent offsets, distinct fractional leap ordering), not a live leap-announcement table claim.
- S1 has no UI: upgrade uses the newer static client with API initially routed to the actual S1 service, preserving the open client while switching API after import.
- Original imported idempotent JSON responses remain exact even when newer responses add nullable fields. TTLs use exact representable addition or atomic422, never saturation.
- Future-created seeded open holds are an inconsistent/unspecified fixture case, not claimed as validated delayed reservations. Past/default creation and future expiry are covered; no unsupported future-created fixture guarantee is made.
- Tests are finite local evidence; partial official checks alone do not establish full specification coverage. Full matrix closure combines independent tests, source audit and disclosed interpretations.

## Subsequent reusable-source packaging smoke

After acceptance, packaging-only CLI changes were verified from `result/verification` without changing production. `s3-packaged-independent-5cf06c1-01/summary.json` has no failures: all102 functional groups now also have a final5cf06c1 packaged-path execution, plus HTTP/accessibility probes. Constraints: networknone,2CPU,2147483648bytes,UID65532,health0.236489s. `s3-packaged-upgrades-5cf06c1-01/summary.json` has no failures:34API+3import+8browser, original repro, both source-destroyed migrations. These are repeat executions, not additional unique groups. All8 new Python scripts passed syntax and CLI-help checks. The unchanged browser-upgrade runner retains the earlier final-revision passed scenario above.
