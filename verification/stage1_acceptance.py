"""Independent black-box tests derived from Pocketful stage-1 requirements.

No organizer tests or implementation are imported. Run against a disposable service:
python stage1_acceptance.py --url http://127.0.0.1:8080 --revision FULL_SHA --out NEW_DIR
The runner records test names, outcomes and timing, never tokens or exported state.
"""
import argparse
import concurrent.futures
import copy
import datetime
import json
import pathlib
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid


BASE = ""
REQUEST_TIMES = []
PASSWORD = "synthetic-test-password"


def fixture(currency="EUR", minor=2):
    return {"currency": currency, "minor_units": minor,
            "users": [{"id": "u_" + h, "email": h + "@example.test",
                       "password": PASSWORD, "display_name": h.title(),
                       "handle": h, "balance": b}
                      for h, b in [("ada", 100), ("bob", 200), ("cy", 0), ("op", 0)]],
            "payments": [], "requests": [], "settlement_operator_ids": ["u_op"]}


def http(method, path, body=None, token=None, key=None, raw=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if key is not None:
        headers["Idempotency-Key"] = key
    data = raw if raw is not None else (None if body is None else json.dumps(body, ensure_ascii=False).encode())
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(req, timeout=10 if path.startswith("/_test/") else 5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        payload = response.read()
        status = response.status
        content_type = response.headers.get("Content-Type", "")
    elapsed = time.monotonic() - started
    REQUEST_TIMES.append({"method": method, "path": path.split("?")[0], "seconds": elapsed, "status": status})
    if payload:
        assert "application/json" in content_type.lower(), "non-JSON response"
        assert "charset=utf-8" in content_type.lower().replace(" ", ""), "missing UTF-8 response charset"
        value = json.loads(payload)
    else:
        value = None
    assert status < 500, "unexpected server error"
    if status >= 400:
        assert isinstance(value.get("error", {}).get("code"), str), "missing error code"
        assert isinstance(value["error"].get("message"), str), "missing error message"
    return status, value


class Stage1(unittest.TestCase):
    def setUp(self):
        self.assertEqual(http("POST", "/_test/reset", fixture())[0], 204)
        self.tokens = {}
        for h in ("ada", "bob", "cy", "op"):
            status, body = http("POST", "/auth/login", {"email": h + "@example.test", "password": PASSWORD})
            self.assertEqual(status, 200)
            self.tokens[h] = body["token"]

    def call(self, method, path, body=None, who="ada", key=None, raw=None):
        return http(method, path, body, self.tokens.get(who), key, raw)

    def good(self, method, path, body=None, who="ada", key=None, status=201):
        result, value = self.call(method, path, body, who, key)
        self.assertEqual(result, status, f"{method} {path} expected {status}; got {result}, code={value.get('error', {}).get('code') if isinstance(value, dict) else None}")
        return value

    def error(self, status, code, method, path, body=None, who="ada", key=None, raw=None):
        result, value = self.call(method, path, body, who, key, raw)
        self.assertEqual((result, value.get("error", {}).get("code")), (status, code))

    def balances(self):
        values = {h: self.good("GET", "/me", who=h, status=200)["balance"] for h in self.tokens}
        self.assertEqual(sum(values.values()), 300)
        self.assertTrue(all(v >= 0 for v in values.values()))
        return values

    def payment(self, amount=10, who="ada", target="bob", key=None, **extra):
        return self.good("POST", "/payments", {"to_handle": target, "amount": amount, **extra}, who, key or uuid.uuid4().hex)

    def request(self, amount=10, who="bob", payer="ada"):
        return self.good("POST", "/requests", {"payer_handle": payer, "amount": amount}, who, uuid.uuid4().hex)

    def test_health_auth_signup_and_sessions(self):
        self.assertEqual(http("GET", "/health"), (200, {"status": "ok"}))
        for path in ("/me", "/activity", "/requests"):
            self.error(401, "unauthenticated", "GET", path, who=None)
        status, value = http("GET", "/me", token="not-a-session")
        self.assertEqual((status, value["error"]["code"]), (401, "unauthenticated"))
        for mail, password in [("ada@example.test", "incorrect"), ("missing@example.test", PASSWORD)]:
            self.error(401, "unauthenticated", "POST", "/auth/login", {"email": mail, "password": password}, who=None)
        signup = {"email": "New.User+ABC@example.test", "password": PASSWORD, "display_name": "새 사용자"}
        response = self.good("POST", "/auth/signup", signup, who=None)
        status, user = http("GET", "/me", token=response["token"])
        self.assertEqual((status, user["handle"], user["balance"]), (200, "new_user_abc", 0))
        self.error(409, "email_taken", "POST", "/auth/signup", signup, who=None)
        self.error(409, "handle_taken", "POST", "/auth/signup", {**signup, "email": "new_user_abc@elsewhere.test"}, who=None)
        self.error(401, "unauthenticated", "POST", "/auth/login", {"email": "new_user_abc@elsewhere.test", "password": PASSWORD}, who=None)
        for bad in [{**signup, "email": "invalid"}, {**signup, "email": "unused@domain", "password": "short"}]:
            self.error(422, "validation_failed", "POST", "/auth/signup", bad, who=None)
        second = self.good("POST", "/auth/login", {"email": "ada@example.test", "password": PASSWORD}, who=None, status=200)
        self.assertEqual(http("GET", "/me", token=second["token"])[0], 200)
        self.assertEqual(self.call("GET", "/me")[0], 200)

    def test_numeric_and_field_validation(self):
        for amount in [0, -1, 1000000001, 1.5, "1", True, False, None]:
            with self.subTest(amount=amount):
                self.error(422, "validation_failed", "POST", "/payments", {"to_handle": "bob", "amount": amount}, key="bad")
        for field, values in {"note": [None, 1, True, "a" * 201], "visibility": [None, 1, True, "PUBLIC"]}.items():
            for value in values:
                self.error(422, "validation_failed", "POST", "/payments", {"to_handle": "bob", "amount": 1, field: value}, key="bad")
        self.error(400, "malformed_request", "POST", "/payments", {"to_handle": 3, "amount": 1}, key="bad")
        self.error(422, "validation_failed", "POST", "/payments", {"amount": 1}, key="bad")
        for raw in [b"{", b"[]", b"null", b'{"amount":NaN}', b'{"amount":Infinity}']:
            self.error(400, "malformed_request", "POST", "/payments", key="bad", raw=raw)
        for i, numeric in enumerate((b"1", b"1.0", b"1e0")):
            status, body = self.call("POST", "/payments", key="numeric" + str(i), raw=b'{"to_handle":"bob","amount":' + numeric + b'}')
            self.assertEqual((status, body["amount"]), (201, 1))
        self.balances()

    def test_notes_receipts_and_visibility(self):
        note = "  한글 e\u0301 😀 <script>&\n "
        private = self.payment(note=note, visibility="private")
        self.assertEqual(private["note"], note)
        self.assertIsNone(private["request_id"])
        self.assertIsNone(private["settlement_id"])
        self.assertLessEqual(len(private["payment_id"]), 64)
        self.assertIsNotNone(datetime.datetime.fromisoformat(private["created_at"].replace("Z", "+00:00")).utcoffset())
        public = self.payment(1, who="bob", target="cy")
        for who in self.tokens:
            feed = self.good("GET", "/activity?unused=yes", who=who, status=200)["payments"]
            ids = [p["payment_id"] for p in feed]
            self.assertIn(public["payment_id"], ids)
            self.assertEqual(private["payment_id"] in ids, who in ("ada", "bob"))
        self.assertEqual(self.balances(), {"ada": 90, "bob": 209, "cy": 1, "op": 0})

    def test_idempotency_json_equality_and_precedence(self):
        body = {"to_handle": "bob", "amount": 1, "ignored": {"x": 1}}
        original = self.good("POST", "/payments", body, key="equal")
        status, replay = self.call("POST", "/payments", key="equal", raw=b'{ "ignored":{"x":1.0}, "amount":1e0,"to_handle":"bob" }')
        self.assertEqual((status, replay), (200, original))
        for changed in [{**body, "amount": -1}, {**body, "ignored": {"x": True}}, {"to_handle": "unknown", "amount": 1}]:
            self.error(409, "idempotency_key_reuse", "POST", "/payments", changed, key="equal")
        self.good("POST", "/requests", {"payer_handle": "bob", "amount": 1}, key="equal")
        self.good("POST", "/payments", {"to_handle": "ada", "amount": 1}, who="bob", key="equal")
        self.balances()

    def test_keys_limits_failed_retry_and_unknown_fields(self):
        body = {"to_handle": "bob", "amount": 101}
        for key in (None, ""):
            self.error(400, "missing_idempotency_key", "POST", "/payments", body, key=key)
        self.error(422, "validation_failed", "POST", "/payments", body, key="a" * 256)
        self.error(409, "insufficient_funds", "POST", "/payments", body, key="retry")
        self.payment(2, who="bob", target="ada")
        self.good("POST", "/payments", body, key="retry")
        self.payment(1, key="a" * 255, ignored=[1, {"valid": True}])
        self.error(422, "self_payment", "POST", "/payments", {"to_handle": "ada", "amount": 1}, key="self")
        self.error(404, "not_found", "POST", "/payments", {"to_handle": "missing", "amount": 1}, key="missing")
        self.balances()

    def test_request_permissions_lifecycle_and_original_retry(self):
        request = self.request(150)
        rid = request["request_id"]
        self.error(409, "insufficient_funds", "POST", "/requests/" + rid + "/pay", {}, key="pay")
        for who in ("bob", "cy", "op"):
            self.error(403, "forbidden", "POST", "/requests/" + rid + "/pay", {}, who=who, key="pay")
            self.error(403, "forbidden", "POST", "/requests/" + rid + "/decline", {}, who=who)
        self.error(403, "forbidden", "POST", "/requests/" + rid + "/cancel", {})
        self.payment(50, who="bob", target="ada")
        original = self.good("POST", "/requests/" + rid + "/pay", {"visibility": "private"}, key="pay")
        replay = self.good("POST", "/requests/" + rid + "/pay", {"visibility": "private"}, key="pay", status=200)
        self.assertEqual(replay, original)
        self.error(409, "idempotency_key_reuse", "POST", "/requests/" + rid + "/pay", {}, key="pay")
        self.error(409, "request_not_pending", "POST", "/requests/" + rid + "/pay", {}, key="new")
        for action, who in (("decline", "ada"), ("cancel", "bob")):
            self.error(409, "request_not_pending", "POST", "/requests/" + rid + "/" + action, {}, who=who)
        for terminal, other, actor in (("decline", "cancel", "ada"), ("cancel", "decline", "bob")):
            rid = self.request(1)["request_id"]
            one = self.good("POST", "/requests/" + rid + "/" + terminal, {}, who=actor, status=200)
            two = self.good("POST", "/requests/" + rid + "/" + terminal, {}, who=actor, status=200)
            self.assertEqual(one, two)
            self.error(409, "request_not_pending", "POST", "/requests/" + rid + "/" + other, {}, who="bob" if actor == "ada" else "ada")
        self.balances()

    def test_request_privacy_filters_and_pagination(self):
        for amount in range(1, 5):
            self.request(amount)
        for who in ("cy", "op"):
            self.assertEqual(self.good("GET", "/requests", who=who, status=200)["requests"], [])
        self.assertEqual(self.good("GET", "/activity", status=200)["payments"], [])
        page = self.good("GET", "/requests?direction=incoming&status=pending&limit=2&offset=0", status=200)
        self.assertEqual(len(page["requests"]), 2)
        self.assertTrue(page["has_more"])
        page2 = self.good("GET", "/requests?direction=incoming&limit=2&offset=2", status=200)
        self.assertFalse(page2["has_more"])
        self.assertFalse(set(r["request_id"] for r in page["requests"]) & set(r["request_id"] for r in page2["requests"]))
        self.assertEqual(self.good("GET", "/requests?direction=outgoing", status=200)["requests"], [])
        for path in ("/requests", "/activity"):
            for query in ("limit=0", "limit=201", "limit=1e2", "limit=4.0", "limit=%2B4", "offset=-1", "offset=", "offset=1.0"):
                self.error(422, "validation_failed", "GET", path + "?" + query)
            self.good("GET", path + "?limit=200&offset=9999&unknown=x", status=200)
        for query in ("status=unknown", "direction=unknown"):
            self.error(422, "validation_failed", "GET", "/requests?" + query)

    def test_split_rounding_zero_requests_order_and_atomic_failure(self):
        for amount, handles in [(1, ["ada", "bob", "cy"]), (10, ["cy", "bob", "ada"]), (7, ["bob", "cy"]), (1, ["ada"])]:
            split = self.good("POST", "/splits", {"amount": amount, "participant_handles": handles}, key=uuid.uuid4().hex)
            q, r = divmod(amount, len(handles))
            self.assertEqual(split["shares"], [{"handle": h, "amount": q + (i < r)} for i, h in enumerate(handles)])
            self.assertEqual([x["payer_handle"] for x in split["requests"]], [h for h in handles if h != "ada"])
            for request in split["requests"]:
                if request["amount"] == 0:
                    payment = self.good("POST", "/requests/" + request["request_id"] + "/pay", {}, who=request["payer_handle"], key=uuid.uuid4().hex)
                    self.assertEqual(payment["amount"], 0)
        before = self.good("GET", "/requests", status=200)
        for handles, code in [([], "validation_failed"), (["bob", "bob"], "validation_failed"), (["bob", "missing"], "not_found")]:
            self.error(404 if code == "not_found" else 422, code, "POST", "/splits", {"amount": 5, "participant_handles": handles}, key="retry-split")
            self.assertEqual(self.good("GET", "/requests", status=200), before)
        self.assertEqual(self.balances(), {"ada": 100, "bob": 200, "cy": 0, "op": 0})

    def test_settlement_netting_privacy_precedence_and_receipt(self):
        body = {"transfers": [{"from_handle": "cy", "to_handle": "ada", "amount": 40, "visibility": "private"}, {"from_handle": "ada", "to_handle": "cy", "amount": 40}]}
        self.error(401, "unauthenticated", "POST", "/settlements", body, who=None, key="net")
        self.error(403, "forbidden", "POST", "/settlements", body, key="net")
        original = self.good("POST", "/settlements", body, who="op", key="net")
        self.assertEqual([p["from_handle"] for p in original["payments"]], ["cy", "ada"])
        for payment in original["payments"]:
            self.assertEqual(payment["settlement_id"], original["settlement_id"])
            self.assertEqual(payment["created_at"], original["committed_at"])
            self.assertIsNone(payment["request_id"])
        self.assertEqual(self.good("POST", "/settlements", body, who="op", key="net", status=200), original)
        self.assertEqual(len(self.good("GET", "/activity", who="op", status=200)["payments"]), 1)
        first = {"from_handle": "ada", "to_handle": "bob", "amount": 101}
        self.error(404, "not_found", "POST", "/settlements", {"transfers": [first, {"from_handle": "missing", "to_handle": "cy", "amount": 1}]}, who="op", key="bad")
        for transfers in ([], [first] * 33, [None], "bad"):
            self.error(422, "validation_failed", "POST", "/settlements", {"transfers": transfers}, who="op", key="bad")
        self.error(409, "insufficient_funds", "POST", "/settlements", {"transfers": [first]}, who="op", key="bad")
        self.assertEqual(self.balances(), {"ada": 100, "bob": 200, "cy": 0, "op": 0})

    def test_concurrent_same_key_exactly_once(self):
        body = {"to_handle": "bob", "amount": 2}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(lambda _: self.call("POST", "/payments", body, key="race"), range(50)))
        self.assertEqual(sum(status == 201 for status, _ in results), 1)
        self.assertEqual(sum(status == 200 for status, _ in results), 49)
        self.assertTrue(all(body == results[0][1] for _, body in results))
        self.assertEqual(self.balances()["ada"], 98)

    def test_all_write_paths_original_replay_and_path_scope(self):
        req_body = {"payer_handle": "bob", "amount": 1}
        request = self.good("POST", "/requests", req_body, key="req")
        self.good("POST", "/requests/" + request["request_id"] + "/cancel", {}, status=200)
        self.assertEqual(self.good("POST", "/requests", req_body, key="req", status=200), request)
        split_body = {"participant_handles": ["bob", "cy"], "amount": 1}
        split = self.good("POST", "/splits", split_body, key="split")
        self.good("POST", "/requests/" + split["requests"][0]["request_id"] + "/decline", {}, who="bob", status=200)
        self.assertEqual(self.good("POST", "/splits", split_body, key="split", status=200), split)
        for _ in range(2):
            request = self.request(1)
            self.good("POST", "/requests/" + request["request_id"] + "/pay", {}, key="same-different-path")
        self.balances()

    def test_max_amount_and_large_exact_balances(self):
        seeded = fixture("BHD", 3)
        seeded["users"][0]["balance"] = 2 ** 53 - 1000
        self.assertEqual(http("POST", "/_test/reset", seeded)[0], 204)
        login = self.good("POST", "/auth/login", {"email": "ada@example.test", "password": PASSWORD}, who=None, status=200)
        self.tokens["ada"] = login["token"]
        payment = self.payment(1000000000)
        self.assertEqual(payment["amount"], 1000000000)
        me = self.good("GET", "/me", status=200)
        self.assertEqual(me["balance"], 2 ** 53 - 1000 - 1000000000)
        self.assertEqual((me["currency"], me["minor_units"]), ("BHD", 3))

    def test_settlement_32_entries_and_competing_batches(self):
        transfers = [{"from_handle": "ada" if i % 2 == 0 else "bob", "to_handle": "bob" if i % 2 == 0 else "ada", "amount": 1} for i in range(32)]
        receipt = self.good("POST", "/settlements", {"transfers": transfers}, who="op", key="maximum")
        self.assertEqual(len(receipt["payments"]), 32)
        body = {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 60}]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda n: self.call("POST", "/settlements", body, who="op", key="batch-race" + str(n)), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [201, 409])
        self.assertEqual(self.balances()["ada"], 40)

    def test_concurrent_spending_and_request_terminal_race(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(lambda i: self.call("POST", "/payments", {"to_handle": "cy", "amount": 7}, key="spend" + str(i)), range(50)))
        self.assertEqual(sum(status == 201 for status, _ in results), 14)
        self.assertTrue(all(status == 201 or (status == 409 and body["error"]["code"] == "insufficient_funds") for status, body in results))
        self.assertEqual(self.balances()["ada"], 2)
        rid = self.request(1)["request_id"]
        actions = [("pay", "ada", "payrace"), ("decline", "ada", None), ("cancel", "bob", None)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            raced = list(pool.map(lambda args: self.call("POST", "/requests/" + rid + "/" + args[0], {}, who=args[1], key=args[2]), actions))
        self.assertEqual(sum(status in (200, 201) for status, _ in raced), 1)
        self.assertTrue(all(status in (200, 201, 409) for status, _ in raced))
        self.balances()

    def test_export_import_replacement_retries_credentials_and_invalid_atomicity(self):
        body = {"to_handle": "bob", "amount": 5}
        original = self.good("POST", "/payments", body, key="persist")
        rid = self.request()["request_id"]
        self.error(409, "insufficient_funds", "POST", "/payments", {"to_handle": "bob", "amount": 999}, key="failed")
        status, exported = http("GET", "/_test/export")
        self.assertEqual(status, 200)
        self.assertEqual((exported["track"], exported["format_version"]), ("pocketful", 1))
        self.assertIsInstance(exported["state"], dict)
        self.payment(1)
        changed = fixture("JPY", 0)
        changed["users"] = [{**u, "password": "replacement-password"} for u in changed["users"]]
        self.assertEqual(http("POST", "/_test/reset", changed)[0], 204)
        self.assertEqual(self.call("GET", "/me")[0], 401)
        for _ in range(2):
            self.assertEqual(http("POST", "/_test/import", exported)[0], 204)
            self.assertEqual(self.balances()["ada"], 95)
            self.assertEqual(self.good("POST", "/payments", body, key="persist", status=200), original)
        for bad in [{}, {**exported, "track": "other"}, {**exported, "format_version": 99}, {**exported, "state": {}}]:
            self.error(422, "validation_failed", "POST", "/_test/import", bad, who=None)
            self.assertEqual(self.balances()["ada"], 95)
        self.good("POST", "/auth/login", {"email": "ada@example.test", "password": PASSWORD}, who=None, status=200)
        self.error(401, "unauthenticated", "POST", "/auth/login", {"email": "ada@example.test", "password": "replacement-password"}, who=None)
        self.good("POST", "/requests/" + rid + "/pay", {}, key="restored-request")
        self.good("POST", "/payments", {"to_handle": "bob", "amount": 1}, key="failed")
        self.good("POST", "/settlements", {"transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 1}]}, who="op", key="operator-restored")

    def test_reset_seed_no_replay_and_invalid_reset_unchanged(self):
        seeded = fixture()
        seeded["payments"] = [{"id": "seed", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 7, "note": "seeded", "visibility": "private"}]
        self.assertEqual(http("POST", "/_test/reset", seeded)[0], 204)
        status, login = http("POST", "/auth/login", {"email": "ada@example.test", "password": PASSWORD})
        self.assertEqual(status, 200)
        self.tokens["ada"] = login["token"]
        self.assertEqual(self.good("GET", "/me", status=200)["balance"], 100)
        bad = copy.deepcopy(seeded)
        bad["users"][0]["balance"] = -1
        self.error(422, "validation_failed", "POST", "/_test/reset", bad, who=None)
        self.assertEqual(self.good("GET", "/me", status=200)["balance"], 100)


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    BASE = args.url.rstrip("/")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Stage1)
    started = time.monotonic()
    with (out / "tests.log").open("w") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"revision": args.revision, "suite": "independent-stage1", "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
              "elapsed_seconds": time.monotonic() - started,
              "request_count": len(REQUEST_TIMES), "max_request_seconds": max((r["seconds"] for r in REQUEST_TIMES), default=0),
              "failure_ids": [test.id() for test, _ in result.failures + result.errors]}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "request-metrics.json").write_text(json.dumps(REQUEST_TIMES, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)


if __name__ == "__main__":
    main()
