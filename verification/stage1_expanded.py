"""Additional independently specified boundaries; never imports production code."""
import concurrent.futures
import copy
import json
import unittest
import uuid
import stage1_acceptance as base


class Expanded(unittest.TestCase):
    setUp = base.Stage1.setUp
    call = base.Stage1.call
    good = base.Stage1.good
    error = base.Stage1.error
    balances = base.Stage1.balances
    payment = base.Stage1.payment
    request = base.Stage1.request

    def test_auth_every_financial_path_and_unknown_resources(self):
        for path, body in [("/payments", {"to_handle": "bob", "amount": 1}),
                           ("/requests", {"payer_handle": "bob", "amount": 1}),
                           ("/splits", {"participant_handles": ["bob"], "amount": 1}),
                           ("/settlements", {"transfers": []}),
                           ("/requests/missing/pay", {}), ("/requests/missing/decline", {}),
                           ("/requests/missing/cancel", {})]:
            self.error(401, "unauthenticated", "POST", path, body, who=None, key="auth")
        for action in ("pay", "decline", "cancel"):
            self.error(404, "not_found", "POST", "/requests/missing/" + action, {}, key="unknown")
        self.error(422, "self_request", "POST", "/requests", {"payer_handle": "ada", "amount": 1}, key="self")
        self.error(404, "not_found", "POST", "/requests", {"payer_handle": "missing", "amount": 1}, key="unknown")

    def test_signup_exact_boundaries_and_recipient_readiness(self):
        local = "New.Person+LongerThanTwentyChars"
        signed = self.good("POST", "/auth/signup", {"email": local + "@example.test", "password": "12345678", "display_name": "New", "handle": "ignored", "balance": 100}, who=None)
        status, me = base.http("GET", "/me", token=signed["token"])
        self.assertEqual((status, me["handle"], len(me["handle"]), me["balance"]), (200, "new_person_longertha", 20, 0))
        self.payment(1, target=me["handle"])
        self.request(1000000000, payer=me["handle"])
        self.assertEqual(base.http("GET", "/me", token=signed["token"])[1]["balance"], 1)
        self.assertEqual(len(base.http("GET", "/requests", token=signed["token"])[1]["requests"]), 1)

    def test_every_amount_note_visibility_rule_and_no_failure_trace(self):
        before = self.good("GET", "/_test/export", who=None, status=200)
        for path, template in [("/requests", {"payer_handle": "bob"}), ("/splits", {"participant_handles": ["bob", "cy"]})]:
            for amount in [None, True, "2", 0, -1, 1.1, 1000000001]:
                self.error(422, "validation_failed", "POST", path, {**template, "amount": amount}, key="fail")
            for note in [None, 0, False, "😀" * 201]:
                self.error(422, "validation_failed", "POST", path, {**template, "amount": 1, "note": note}, key="fail")
        for handles in [None, "ada", {}, [1], [None]]:
            self.error(400, "malformed_request", "POST", "/splits", {"amount": 1, "participant_handles": handles}, key="fail")
        self.assertEqual(self.good("GET", "/_test/export", who=None, status=200), before)
        request = self.request(1)
        for public in [None, False, {}, "invalid"]:
            self.error(422, "validation_failed", "POST", "/requests/" + request["request_id"] + "/pay", {"visibility": public}, key="same")
        self.good("POST", "/requests/" + request["request_id"] + "/pay", {}, key="same")
        note = "😀" * 200
        self.assertEqual(self.payment(1, note=note)["note"], note)

    def test_all_idempotent_writes_under_concurrency(self):
        rid = self.request(1)["request_id"]
        cases = [("/requests", {"payer_handle": "bob", "amount": 1}, "ada"),
                 ("/splits", {"participant_handles": ["bob", "cy"], "amount": 1}, "ada"),
                 ("/requests/" + rid + "/pay", {}, "ada"),
                 ("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, "op")]
        for path, body, who in cases:
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
                results = list(pool.map(lambda _: self.call("POST", path, body, who=who, key="race"), range(50)))
            self.assertEqual(sum(s == 201 for s, _ in results), 1, path)
            self.assertEqual(sum(s == 200 for s, _ in results), 49, path)
            self.assertTrue(all(v == results[0][1] for _, v in results), path)
        self.balances()

    def test_export_snapshot_under_net_settlements(self):
        body = {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 5}, {"from_handle": "cy", "to_handle": "ada", "amount": 5}]}
        def work(n):
            if n % 2:
                return "write", self.call("POST", "/settlements", body, who="op", key="snap" + str(n))
            return "read", self.call("GET", "/_test/export", who=None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(work, range(100)))
        for kind, (status, snapshot) in results:
            self.assertEqual(status, 201 if kind == "write" else 200)
            if kind == "read":
                state = snapshot["state"]
                balances = [u["balance"] for u in state["users"].values()]
                self.assertEqual(sum(balances), 300)
                self.assertTrue(all(value >= 0 for value in balances))
                self.assertEqual(len(state["payments"]), 2 * len(state["settlements"]))
                for settlement in state["settlements"].values():
                    self.assertEqual(len(settlement["payments"]), 2)
                    for p in settlement["payments"]:
                        self.assertEqual(state["payments"][p["payment_id"]], p)

    def test_settlement_input_order_atomicity_and_failed_key_recovery(self):
        insufficient = {"from_handle": "ada", "to_handle": "bob", "amount": 101}
        unknown = {"from_handle": "missing", "to_handle": "bob", "amount": 1}
        selfpay = {"from_handle": "ada", "to_handle": "ada", "amount": 1}
        before = self.good("GET", "/_test/export", who=None, status=200)
        for entries, status, code in [([unknown, selfpay], 404, "not_found"), ([selfpay, unknown], 422, "self_payment"), ([insufficient, unknown], 404, "not_found")]:
            self.error(status, code, "POST", "/settlements", {"transfers": entries}, who="op", key="same")
            self.assertEqual(self.good("GET", "/_test/export", who=None, status=200), before)
        self.good("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, who="op", key="same")

    def test_full_populated_import_and_removal_of_destination_accounts(self):
        requests = [self.request(1) for _ in range(4)]
        self.good("POST", "/requests/" + requests[0]["request_id"] + "/pay", {}, key="paid")
        self.good("POST", "/requests/" + requests[1]["request_id"] + "/decline", {}, status=200)
        self.good("POST", "/requests/" + requests[2]["request_id"] + "/cancel", {}, who="bob", status=200)
        split_body = {"participant_handles": ["ada", "bob", "cy"], "amount": 2}
        split = self.good("POST", "/splits", split_body, key="split")
        settlement_body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1, "visibility": "private"}, {"from_handle": "bob", "to_handle": "cy", "amount": 1}]}
        settlement = self.good("POST", "/settlements", settlement_body, who="op", key="settlement")
        snapshot = self.good("GET", "/_test/export", who=None, status=200)
        frozen = copy.deepcopy(snapshot)
        # Inspection stays in memory: only presence/algorithm assertions, no hash/token output.
        for user in snapshot["state"]["users"].values():
            self.assertNotIn("password", user)
            self.assertTrue(user["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertEqual(len(set(u["password_hash"] for u in snapshot["state"]["users"].values())), 4)
        self.payment(1)
        other = self.good("POST", "/auth/signup", {"email": "destination-only@example.test", "password": base.PASSWORD, "display_name": "Other"}, who=None)
        self.assertEqual(base.http("POST", "/_test/import", snapshot)[0], 204)
        self.assertEqual(snapshot, frozen)
        self.assertEqual(base.http("GET", "/me", token=other["token"])[0], 401)
        self.error(401, "unauthenticated", "POST", "/auth/login", {"email": "destination-only@example.test", "password": base.PASSWORD}, who=None)
        self.assertEqual(self.good("POST", "/splits", split_body, key="split", status=200), split)
        self.assertEqual(self.good("POST", "/settlements", settlement_body, who="op", key="settlement", status=200), settlement)
        self.assertEqual({r["status"] for r in self.good("GET", "/requests", status=200)["requests"]}, {"pending", "paid", "declined", "cancelled"})
        self.good("POST", "/requests/" + requests[3]["request_id"] + "/pay", {}, key="pending-restored")
        self.assertEqual(base.http("POST", "/_test/reset", base.fixture())[0], 204)
        self.assertEqual(self.call("GET", "/me")[0], 401)

    def test_default_paging_huge_offset_exact_precision_and_empty_actions(self):
        receipts = [self.payment(1) for _ in range(51)]
        page = self.good("GET", "/activity", status=200)
        self.assertEqual(len(page["payments"]), 50)
        self.assertTrue(page["has_more"])
        self.assertEqual([p["payment_id"] for p in page["payments"]], [p["payment_id"] for p in reversed(receipts[1:])])
        last = self.good("GET", "/activity?offset=50", status=200)
        self.assertEqual(len(last["payments"]), 1)
        self.assertFalse(last["has_more"])
        self.assertEqual(self.good("GET", "/activity?offset=" + "9" * 5000, status=200)["payments"], [])
        self.error(422, "validation_failed", "POST", "/payments", key="precise", raw=b'{"to_handle":"bob","amount":1.000000000000000000001}')
        for action, who in (("decline", "ada"), ("cancel", "bob")):
            rid = self.request(1)["request_id"]
            self.assertEqual(self.call("POST", "/requests/" + rid + "/" + action, who=who, raw=b"")[0], 200)


if __name__ == "__main__":
    base.Stage1 = Expanded
    base.main()
