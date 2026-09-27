# Stage4 independent requirement matrix — locally accepted

Official specification `803560d2a678ace1414465c098eb0ab5380ffade`; copy baseline `f9ec13a35675e48f9bc1fb7b6a0b9ea4a0a24c1f`. Full stages1–3 apply. Tests are derived before review of Stage4 code or shipped tests. Exact candidate `42400632c8fef65555b936b987ecf1970df5f540` was independently executed after the full handoff and exclusive Docker lease. All 30 rows below are locally accepted; this is not an organizer score.

| Row | Required behavior | Independent evidence | Status |
|---|---|---|---|
| 01 | Refund receiver-only, unknown404, invalid amount422, required key | API refund validation/key groups | Pass |
| 02 | Opposite payment, original note/privacy, links/nulls, exact money | API receipt/feed group | Pass |
| 03 | Cumulative refund cap follows latest corrected amount | API cap/reduction/race groups | Pass |
| 04 | Refund available funds, holds not spent, atomic failure and reusable key | API available/rollback | Pass |
| 05 | Request/capture/settlement targets; no reopen or restored hold/membership | API three target groups | Pass |
| 06 | Refund-of-refund invalid; capture/refund correction immutability | API linked groups | Pass |
| 07 | Single and batch corrections cannot go below refunded amount | API lowerbound | Pass |
| 08 | Refund original replay after later changes; numeric JSON equality/path scope | API replay/keys | Pass |
| 09 | Refund recorded/effective history, ordinary visibility, frozen old statements | API known-time/snapshot | Pass |
| 10 | Concurrent same-key once, distinct refunds capped, refund/correction race | API three races | Pass |
| 11 | Operator batch auth; no extra private revision access | API operator/privacy | Pass |
| 12 | Batch1/32/33 limits, distinct IDs, malformed shape | API shape/32 group | Pass |
| 13 | Every ordinary correction field, zero, Unicode reason, unknown ignored | API validation/order | Pass |
| 14 | Item errors input-order before incomplete settlement/funds | API precedence | Pass |
| 15 | Complete settlement membership, identical effective instants across offsets | API completeness/offset groups | Pass |
| 16 | Current collective affordability rather than gross; available holds | API cycle/net-held groups | Pass |
| 17 | Current availability before grouped historical total/available | API historical/funding/void-held | Pass |
| 18 | Batch ordered revisions/shared strictly increasing recorded time/batch link | API order/strict time | Pass |
| 19 | Original payment/settlement receipts/replays immutable; new selected statements | API all10, original/snapshot | Pass |
| 20 | Batch same-key concurrency, overlapping CAS and single-vs-batch | API three CAS groups | Pass |
| 21 | Mixed refund/hold/capture/void/spend/net settlement/read serial invariants | API50way mixed | Pass |
| 22 | All10 keyed response bodies and old/new snapshots preserve on import | API all10 roundtrip | Pass |
| 23 | Actual source-destroyed1/2/3→4, credentials/roles/pending/links/corrections/snapshots | stage4_upgrade.py three scenarios | Pass |
| 24 | Full inherited API/time/expiry/invalid-history import semantics | Existing102 independent groups + final source audit | Pass |
| 25 | Refund UI eligibility/corrected cap/errors/preserved inputs/lost samebody/key | New browser contract tests | Pass |
| 26 | Batch UI row controls/exact decimals/zero/offsets/duplicate/error/stale/uncertain | New browser contract tests | Pass |
| 27 | Operator manual IDs without private-read bypass; response ordered/sharedtime | New browser permission/batch tests | Pass |
| 28 | Inherited UI/openbrowser source-destroyed retry and new functional states | Existing browser suites + upgrade/new browser | Pass |
| 29 | 375/1280 layout, visible labels/focus, contrast, actual insecure HTTP/offline | Browser metrics/screenshots + direct inspection | Pass |
| 30 | Exact committed code, predecessor trees, standaloneDocker/RUN/2CPU2GiB/time constraints | Immutable archive audit, official isolated--stage4 and finalchain | Pass |

## Interpretation baseline

- Refund amount inherits ordinary positive integral minor-unit range; zero is a correction amount, not a refund amount.
- Item input-order validation precedes settlement completeness and monetary feasibility. Same-instant history uses combined net movements; ID-ordered statement intermediates may be signed.
- Preserve exact original imported response JSON, even when older receipts omit newly introduced nullable fields.
- All Stage3 disclosed time/TTL/legacy-browser interpretations and atomic malformed-history import repair remain inherited.
- UI additions are coordinator-requested product scope; no role flag, new private read privilege or undocumented API is assumed.

Evidence index: `s4-isolated-4240063-01/report.json` (193 shipped checks), `s4-independent-4240063-01/` (148 distinct independent groups, all zero failures/errors/skips), `s4-upgrades-4240063-01/s1-to-s4/report.json`, `s2-to-s4/report.json`, `s3-to-s4/report.json`, and `s4-browser-upgrade-4240063-01/browser/report.json`. Rows 01–22: s4-api; 23: three migration reports; 24: inherited API/import groups; 25–27: s4-browser; 28: inherited browsers and open-browser upgrade; 29: s4-browser/layout.json, contrast.json and four directly inspected screenshots; 30: constraints.json, source-hashes.json, official report and final source audit. Coordinator fresh-clone/all-stage chain audit remains separate.

Whole-container shape interpretation: establish 1–32 objects and distinct valid payment IDs before semantic item errors in input order; then settlement completeness/effective-instant consistency, combined current available funds, and grouped historical total/available funds.
