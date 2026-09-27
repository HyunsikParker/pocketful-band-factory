# Independent verification

These tests were derived from the written stage-1 specification by the independent verifier, before reading the submitted implementation. They do not import the service or organizer tests. The optional exported-state integrity assertions inspect this team's documented implementation-defined state after the black-box operations.

Run from any directory with Python3.12+ and a working Docker context:

```sh
python /absolute/result/verification/run_stage1.py --repo /absolute/result --out /absolute/checks/independent-new-run
```

Choose a fresh output directory outside the result repository. The runner builds stage1, checks health under2CPU/2GiB with no network, runs16 basic and8 expanded groups over HTTP, then destroys a populated source container before importing into a separate destination. It also exercises50 concurrent logins. It removes its own containers/image, does not alter Docker configuration, and inherits `DOCKER_CONTEXT` when supplied. New reports contain exact revision, timings, resource configuration and failures; do not commit them.

To run only the HTTP groups against an already-running **disposable** instance:

```sh
python verification/stage1_acceptance.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/basic-new-run
python verification/stage1_expanded.py --url http://127.0.0.1:8080 --revision FULL_SHA --out /absolute/checks/expanded-new-run
```

The tests repeatedly reset the target. All accounts/passwords are synthetic fixtures. Tokens and exported state stay private in memory; they are not printed or saved in these sources. Reports do not substitute for the official isolated harness or independent source/specification review. Later-stage populated upgrades must additionally be checked when those stages are implemented.
