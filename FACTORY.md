# Pocketful software factory

The factory uses four BAND-owned Codex app-server runtimes with `gpt-6-astra`. Each seat has its own identity, mandate, inbox and Git authorship. The operator prepared the environment and supplied one initial dispatch covering all four Pocketful stages. Stage implementation belongs to the BAND seats. This operator documentation was prepared with Codex from the configuration and recorded evidence.

## Responsibilities

| Seat | Responsibility | Review boundary |
| --- | --- | --- |
| `factory-coordinator` | Decompose the specification, assign files, coordinate integration and accept milestones | Require exact commits and independent evidence before advancing |
| `factory-core` | State, storage and HTTP service behavior | Return implementation changes to review |
| `factory-interface` | Browser flows, integration, packaging and startup | Exercise real browser interactions and respond to reproduced defects |
| `factory-verifier` | Independent specification review, tests and acceptance | Write verification tools; send production fixes to the responsible builder |

The four mandate files describe this process without naming Pocketful endpoints or fields. The track-specific task belongs in the dispatch and subsequent handoffs. Another team can retain the mandates and supply a different specification.

Separate builders make ownership visible. The verifier can reject a revision without becoming its implementer. The coordinator serializes changes to shared files and the Git index; this prevents one seat from accidentally committing another seat's unfinished work. Every completed change keeps its original commit and author.

## Environment

The observed setup used BAND Desktop and CLI 0.4.12, Codex subscription authentication, Git and Colima on macOS. Each agent ran on the host through BAND's owned runtime. The agent tools were not inside Docker Sandboxes. Docker isolation applied to the submitted services and their test environments.

The event harness used Python 3.13.12 in a dedicated virtual environment, with the official harness requirements and Chromium installed. The kickoff repository was pinned at `803560d2a678ace1414465c098eb0ab5380ffade`. Colima provided 4 CPUs and 8 GiB of memory. The acceptance runs constrained each tested service to 2 CPUs, 2 GiB and no outbound runtime network.

Keep three locations separate: the unchanged kickoff package, a fresh result repository and private test output. Use absolute paths in assignments. Give each seat access to the result checkout, Git, Docker and the browser tools before dispatch. Keep provider credentials outside the result repository and room messages.

To reproduce the configuration, create four distinct local Codex seats in BAND Desktop and assign the corresponding files under `mandates/`. Select the recorded model when available and authenticate using your own provider account. Put all four seats in a fresh room. Confirm that each receives a directed mention and can reply before sending the graded task. Configure distinct Git author identities for the seats.

Create an empty result repository. Supply the complete task and specification, the absolute input/output paths, the required standalone outputs and the acceptance rules in the initial dispatch. The coordinator must include the complete applicable requirements in each implementation and review handoff. The room record, rather than a shortened task summary, is the evidence of what each seat received.

## Acceptance process

The builders commit bounded work before requesting review. The verifier inspects the committed source and derives additional cases from the specification. Public tests provide one check; they do not determine independent acceptance.

The review includes error handling, retries, concurrent writes, state replacement, cross-feature interactions and real browser behavior. Upgrade tests preserve an existing browser session while replacing the source service with the next-stage service. The source container is removed before import so a successful upgrade cannot depend on the old process still being available.

If a check fails, the band first distinguishes a product defect, a faulty test expectation and an environment failure. It preserves the original failure. A production fix goes back to the assigned builder, followed by a committed revision and independent regression. Test expectations change only when the full requirement justifies the correction; an assertion is not removed to make a product pass.

After accepting a stage, the coordinator freezes its tree and copies it forward. Earlier outputs remain independently buildable. A later improvement must not rewrite the earlier stage. Git history and immutable build inputs connect the reports to the code that was tested.

## A defect caught beyond the public tests

Stage 2 revision `473b515f4dd3d40bcafc18571767e223e7133811` passed all 147 stage-1 and 35 stage-2 public checks. The coordinator then raised a browser capability issue for independent reproduction. At an ordinary non-loopback HTTP origin, the application called `crypto.randomUUID()` even though that function was unavailable. Clicking Pay threw an error, sent zero payment requests and left the test balance at EUR 100.

The verifier and interface builder reproduced the failure against the actual application. The interface builder changed one file in `dc2b5f7c2983bde10f83df4ce0d2446328d80f5b`: the request key now comes from 16 cryptographically random bytes encoded as hexadecimal. Existing request-body and retry-key caching stayed intact.

The verifier built the committed revision from `git archive` and repeated the HTTP-origin check. One EUR 1 payment was sent, the balance became EUR 99 and no browser error occurred. Independent browser, cross-feature and upgrade checks followed. The original failed report remains available. This was an internal BAND review and repair sequence.

## Environment recovery

The first isolated stage-1 run failed because the Colima guest could not see the required host paths. The band recorded this as an environment failure, inspected the mount configuration and repaired the temporary mounts before retrying. It preserved the failed invocation and checked the kickoff source hashes. This failure did not establish that the application was incorrect.

The factory mandates also require seats to read their supported inbox and room history when waiting. The rehearsal exposed delayed active-turn message delivery; reading an already-posted report prevents an agent from requesting the same work again. Rehearsal intervention is separate from the submitted run and must remain distinguishable in the evidence.

## Measurement method

Elapsed time uses the initial dispatch and coordinator terminal-report timestamps in the unchanged full room export. A partial stage report does not end that interval.

For model usage, map each exact BAND room runtime to its `provider_session_id` and read the provider's cumulative token counters. Check the counters for decreases before aggregation. Cached input is included in input tokens; reasoning output is included in output tokens. These subsets must not be added a second time.

The runtimes used subscription authentication. Token counts describe usage, not a provider invoice or a measured USD charge. BAND's empty usage attribution during the run is not evidence of zero usage. The measured counters and billing basis are reported below. The coordinator could not obtain these counters from BAND; the operator subsequently read the exact four provider logs. This does not revise the original room report.

## Measured run

One dispatch entered the room at **2026-09-27 04:12:05.403 UTC**. The coordinator posted its terminal report at **07:19:50.713 UTC**: **3 hours, 7 minutes, 45 seconds**. This interval includes setup recovery, implementation, independent review, portable test packaging and the final local-clone audit. The coordinator's own evidence cutoff was 16 seconds earlier; its report uses that different endpoint.

All four stage directories passed from a new local clone at `bccf663516e9c198b420bf8b03faf43e7ccd9cea`:

| Output | Supplied checks passed | Highest local stage claimed |
| --- | ---: | ---: |
| Stage 1 | 147 / 147 | 1 |
| Stage 2 | 182 / 182 | 2 |
| Stage 3 | 188 / 188 | 3 |
| Stage 4 | 193 / 193 | 4 |

That is 710 test executions across separate outputs, including inherited checks repeated against later stages. The final application also passed 148 distinct independent functional groups, separate HTTP/accessibility probes, populated upgrades from each earlier stage and an open-browser upgrade. Each RUN.md was exercised with a clean build; browser stages passed signup, navigation and logout. The four frozen stage trees remained unchanged.

The final factory history has 20 commits: core 5, interface 6, verifier 4 and coordinator 5. The coordinator's stage copies are integration work, not original service implementation. Later operator documentation and packaging commits are separate from this count.

Provider counters were sampled at **2026-09-27 07:20:43.787 UTC**, after the terminal report and its final closeout. They cover the four exact runtime sessions; they exclude the operator's preparation and submission work.

| Provider counter | Tokens |
| --- | ---: |
| Input | 187,569,635 |
| Cached input, already included above | 184,042,880 |
| Output | 636,569 |
| Reasoning output, already included above | 163,728 |
| Total input + output | 188,206,204 |

These are cumulative processed-token counters, not unique words or newly generated context. Repeated full-specification handoffs and long-running conversations contributed to the input volume; cached input accounts for about 98.1% of input. The four-seat review process produced reproducible repairs and portable tests, but this run does not establish that it is token-efficient. No comparison with a single-agent factory was performed.

All four runtimes used the configured Codex subscription. No per-run invoice or verified USD charge was available, so monetary spend is **unknown**, not zero. No paid API account, billing card or separately purchased compute was provisioned for this run. [resources.json](resources.json) preserves the per-seat counters and measurement basis without provider transcripts or credentials.

## Limits and operator boundary

The terminal report marks completion of the autonomous service build, not submission eligibility. At that point `harness check` correctly failed because the operator-owned README, FACTORY and whole-room export were absent. The operator assembled those materials after termination, without changing stage code, mandates, verification sources or the original commit history.

The submitted run received no operator steering, debugging hint, approval or restart after its initial dispatch. The room export retains the full collaboration and terminal report. Local tests do not establish a hidden-test result, rank or award.

The reviewers recorded finite-test and interpretation limits: Chromium was tested, not every browser or assistive technology; leap-second handling is structural and offline; simultaneous historical solvency uses net effects while intermediate statement rows may be signed; inconsistent future-created already-open seeded holds are not treated as delayed reservations; batch shape and duplicate-ID checks precede ordered semantic errors. These interpretations are not organizer clarifications.

## Sources

- [Official participant guide](https://github.com/band-ai/dark-factory-wearedevs/blob/main/docs/participant-guide.md)
- [Recorded BAND room](https://app.band.ai/sessions/4ba34a55-1571-4f90-9280-5950a985978f)
- The four configured mandate files, immutable Git revisions, independent reports and exact-provider usage receipts retained with this run.
