"""Pocketful stage-one domain engine.

One lock is the transaction boundary for every read and write, including control
operations and authentication. Receipts and idempotent responses are independent
copies; changing a request never changes an earlier response.
"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import re
from threading import RLock
from uuid import uuid4


class ApiError(Exception):
    def __init__(self, status, code, message=None):
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")
        super().__init__(self.message)


def fail(code="validation_failed", status=422):
    raise ApiError(status, code)


def instant():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix):
    return prefix + uuid4().hex


def integer(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        fail()
    if isinstance(value, float) and not math.isfinite(value):
        fail()
    if isinstance(value, Decimal) and not value.is_finite():
        fail()
    if value < minimum or (maximum is not None and value > maximum):
        fail()
    if value != int(value):
        fail()
    return int(value)


def string(body, name, default=None, required=True):
    if name not in body:
        if required:
            fail()
        return default
    value = body[name]
    if not isinstance(value, str):
        fail("malformed_request", 400)
    return value


def note(body):
    value = body.get("note", "")
    if not isinstance(value, str) or len(value) > 200:
        fail()
    return value


def visibility(body):
    value = body.get("visibility", "public")
    if not isinstance(value, str) or value not in ("public", "private"):
        fail()
    return value


def canonical(value):
    """Tagged JSON equality: numbers compare numerically, never as booleans.

    Decimal.normalize uses the active precision and may round; normalizing the
    coefficient tuple instead preserves arbitrary parsed JSON precision.
    """
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            fail("malformed_request", 400)
        sign, digits, exponent = number.as_tuple()
        digits = list(digits)
        if not any(digits):
            return ["number", 0, [0], 0]
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return ["number", sign, digits, exponent]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, list):
        return ["array", [canonical(item) for item in value]]
    if isinstance(value, dict):
        return ["object", [[key, canonical(value[key])] for key in sorted(value)]]
    fail("malformed_request", 400)


def fingerprint(body):
    return json.dumps(canonical(body), ensure_ascii=True, separators=(",", ":"))


def blank_state():
    return {"schema": 1, "currency": "EUR", "minor_units": 2,
            "users": {}, "tokens": {}, "payments": {}, "requests": {},
            "splits": {}, "settlements": {}, "idempotency": {},
            "settlement_operator_ids": [], "seeded_total": 0}


class Engine:
    def __init__(self):
        self.lock = RLock()
        self.state = blank_state()

    def handle(self, method, path, query, body, headers):
        with self.lock:
            if method == "GET" and path == "/health":
                return 200, {"status": "ok"}
            if method == "POST" and not isinstance(body, dict):
                fail("malformed_request", 400)
            if method == "POST" and path == "/_test/reset":
                replacement = self._fixture(body)
                self.state = replacement
                return 204, None
            if method == "GET" and path == "/_test/export":
                return 200, {"track": "pocketful", "format_version": 1,
                             "state": deepcopy(self.state)}
            if method == "POST" and path == "/_test/import":
                replacement = self._import(body)
                self.state = replacement
                return 204, None
            import auth
            if method == "POST" and path in ("/auth/signup", "/auth/login"):
                return auth.handle_auth(self.state, path, body)
            user_id = auth.authenticate(self.state, headers.get("authorization"))
            if method == "POST" and path == "/settlements":
                if user_id not in self.state["settlement_operator_ids"]:
                    fail("forbidden", 403)
            request_action = re.fullmatch(r"/requests/([^/]+)/(pay|decline|cancel)", path)
            idempotent = method == "POST" and (
                path in ("/payments", "/requests", "/splits", "/settlements")
                or (request_action and request_action[2] == "pay"))
            claim = None
            if idempotent:
                key = headers.get("idempotency-key")
                if not key:
                    fail("missing_idempotency_key", 400)
                if not isinstance(key, str) or len(key) > 255:
                    fail()
                claim = json.dumps([user_id, method, path, key], separators=(",", ":"))
                body_key = fingerprint(body)
                previous = self.state["idempotency"].get(claim)
                if previous is not None:
                    if body_key != previous["body"]:
                        fail("idempotency_key_reuse", 409)
                    return 200, deepcopy(previous["response"])
            if method == "GET" and path == "/me":
                user = self.state["users"][user_id]
                response = {"user_id": user_id, "display_name": user["display_name"],
                            "handle": user["handle"], "balance": user["balance"],
                            "currency": self.state["currency"], "minor_units": self.state["minor_units"]}
            elif method == "GET" and path == "/activity":
                items = [p for p in self.state["payments"].values()
                         if p["visibility"] == "public" or user_id in (p["from_user_id"], p["to_user_id"])]
                response = self._page(items, query, "payments")
            elif method == "GET" and path == "/requests":
                direction = self._query(query, "direction")
                status = self._query(query, "status")
                if direction is not None and direction not in ("incoming", "outgoing"):
                    fail()
                if status is not None and status not in ("pending", "paid", "declined", "cancelled"):
                    fail()
                items = [r for r in self.state["requests"].values()
                         if user_id in (r["requester_id"], r["payer_id"])
                         and (direction is None or r["payer_id" if direction == "incoming" else "requester_id"] == user_id)
                         and (status is None or r["status"] == status)]
                response = self._page(items, query, "requests")
            elif method == "POST" and path == "/payments":
                recipient = self._user_by_handle(string(body, "to_handle"))
                amount = integer(body.get("amount"), 1, 10**9)
                memo, public = note(body), visibility(body)
                if recipient == user_id:
                    fail("self_payment")
                self._afford({user_id: -amount, recipient: amount})
                response = self._payment(user_id, recipient, amount, memo, public)
                self._commit_payments([response])
            elif method == "POST" and path == "/requests":
                payer = self._user_by_handle(string(body, "payer_handle"))
                amount, memo = integer(body.get("amount"), 1, 10**9), note(body)
                if payer == user_id:
                    fail("self_request")
                response = self._request(user_id, payer, amount, memo)
                self.state["requests"][response["request_id"]] = response
            elif method == "POST" and request_action:
                response = self._request_action(user_id, request_action[1], request_action[2], body)
            elif method == "POST" and path == "/splits":
                response = self._split(user_id, body)
            elif method == "POST" and path == "/settlements":
                response = self._settlement(body)
            else:
                fail("not_found", 404)
            if claim is not None:
                self.state["idempotency"][claim] = {"body": body_key, "response": deepcopy(response)}
            return (201 if idempotent else 200), deepcopy(response)

    @staticmethod
    def _query(query, key, default=None):
        values = query.get(key)
        return values[0] if values else default

    def _page(self, items, query, field):
        values = []
        for name, default, minimum, maximum in (("limit", "50", 1, 200), ("offset", "0", 0, None)):
            raw = self._query(query, name, default)
            if not isinstance(raw, str) or re.fullmatch(r"[0-9]+", raw) is None:
                fail()
            # Huge offsets are valid and simply lie beyond the end; avoid Python's
            # decimal conversion limit without rejecting a valid query value.
            trimmed = raw.lstrip("0") or "0"
            number = int(trimmed) if len(trimmed) < 20 else 10**20
            if number < minimum or (maximum is not None and number > maximum):
                fail()
            values.append(number)
        limit, offset = values
        ordered = sorted(items, key=lambda item: datetime.fromisoformat(item["created_at"]), reverse=True)
        return {field: ordered[offset:offset + limit], "has_more": offset + limit < len(ordered)}

    def _user_by_handle(self, handle):
        for uid, user in self.state["users"].items():
            if user["handle"] == handle:
                return uid
        fail("not_found", 404)

    def _afford(self, deltas):
        for uid, delta in deltas.items():
            balance = self.state["users"][uid]["balance"] + delta
            if balance < 0:
                fail("insufficient_funds", 409)
            if balance > 2**53:
                fail()

    def _payment(self, sender, receiver, amount, memo, public, request_id=None, settlement_id=None, at=None, pid=None):
        users = self.state["users"]
        return {"payment_id": pid or new_id("p_"), "from_user_id": sender,
                "from_handle": users[sender]["handle"], "to_user_id": receiver,
                "to_handle": users[receiver]["handle"], "amount": amount,
                "currency": self.state["currency"], "note": memo, "visibility": public,
                "request_id": request_id, "settlement_id": settlement_id,
                "created_at": at or instant()}

    def _commit_payments(self, payments):
        deltas = {}
        for payment in payments:
            sender, receiver, amount = payment["from_user_id"], payment["to_user_id"], payment["amount"]
            deltas[sender] = deltas.get(sender, 0) - amount
            deltas[receiver] = deltas.get(receiver, 0) + amount
        # Apply the net result, so settlement cycles never transiently overdraw.
        for uid, delta in deltas.items():
            self.state["users"][uid]["balance"] += delta
        for payment in payments:
            self.state["payments"][payment["payment_id"]] = payment

    def _request(self, requester, payer, amount, memo, at=None, rid=None, status="pending", payment_id=None):
        users = self.state["users"]
        return {"request_id": rid or new_id("rq_"), "requester_id": requester,
                "requester_handle": users[requester]["handle"], "payer_id": payer,
                "payer_handle": users[payer]["handle"], "amount": amount,
                "currency": self.state["currency"], "note": memo, "status": status,
                "payment_id": payment_id, "created_at": at or instant()}

    def _request_action(self, user_id, rid, action, body):
        request = self.state["requests"].get(rid)
        if request is None:
            fail("not_found", 404)
        party = "requester_id" if action == "cancel" else "payer_id"
        if request[party] != user_id:
            fail("forbidden", 403)
        if action == "pay":
            public = visibility(body)
            if request["status"] != "pending":
                fail("request_not_pending", 409)
            amount, receiver = request["amount"], request["requester_id"]
            self._afford({user_id: -amount, receiver: amount})
            response = self._payment(user_id, receiver, amount, request["note"], public, request_id=rid)
            self._commit_payments([response])
            request["status"], request["payment_id"] = "paid", response["payment_id"]
            return response
        target = "declined" if action == "decline" else "cancelled"
        if request["status"] not in ("pending", target):
            fail("request_not_pending", 409)
        request["status"] = target
        return request

    def _split(self, user_id, body):
        amount, memo = integer(body.get("amount"), 1, 10**9), note(body)
        if "participant_handles" not in body:
            fail()
        handles = body["participant_handles"]
        if not isinstance(handles, list):
            fail("malformed_request", 400)
        if not handles:
            fail()
        if any(not isinstance(handle, str) for handle in handles):
            fail("malformed_request", 400)
        if len(set(handles)) != len(handles):
            fail()
        users = [self._user_by_handle(handle) for handle in handles]
        quotient, remainder = divmod(amount, len(handles))
        at = instant()
        shares = [{"handle": handle, "amount": quotient + (i < remainder)} for i, handle in enumerate(handles)]
        requests = [self._request(user_id, uid, share["amount"], memo, at=at)
                    for uid, share in zip(users, shares) if uid != user_id]
        response = {"split_id": new_id("sp_"), "amount": amount, "currency": self.state["currency"],
                    "note": memo, "shares": shares, "requests": requests, "created_at": at}
        for request in requests:
            self.state["requests"][request["request_id"]] = request
        self.state["splits"][response["split_id"]] = deepcopy(response)
        return response

    def _settlement(self, body):
        transfers = body.get("transfers")
        if not isinstance(transfers, list) or not 1 <= len(transfers) <= 32:
            fail()
        prepared, deltas = [], {}
        for item in transfers:
            if not isinstance(item, dict):
                fail()
            sender = self._user_by_handle(string(item, "from_handle"))
            receiver = self._user_by_handle(string(item, "to_handle"))
            amount = integer(item.get("amount"), 1, 10**9)
            memo, public = note(item), visibility(item)
            if sender == receiver:
                fail("self_payment")
            prepared.append((sender, receiver, amount, memo, public))
            deltas[sender] = deltas.get(sender, 0) - amount
            deltas[receiver] = deltas.get(receiver, 0) + amount
        self._afford(deltas)
        sid, at = new_id("st_"), instant()
        payments = [self._payment(*item, settlement_id=sid, at=at) for item in prepared]
        response = {"settlement_id": sid, "committed_at": at, "payments": payments}
        self._commit_payments(payments)
        self.state["settlements"][sid] = deepcopy(response)
        return response

    @staticmethod
    def _timestamp(value):
        if not isinstance(value, str):
            fail()
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                fail()
        except (ValueError, OverflowError):
            fail()
        return value

    def _fixture(self, body):
        """Construct a separate engine so reset failure cannot alter live state."""
        from auth import hash_password
        replacement = Engine()
        state = replacement.state
        currency = body.get("currency")
        if not isinstance(currency, str) or not currency:
            fail()
        minor = integer(body.get("minor_units"), 0, 3)
        if minor not in (0, 2, 3):
            fail()
        state["currency"], state["minor_units"] = currency, minor
        users = body.get("users")
        if not isinstance(users, list):
            fail()
        seen_emails, seen_handles = set(), set()
        for user in users:
            if not isinstance(user, dict):
                fail()
            uid, email, handle = user.get("id"), user.get("email"), user.get("handle")
            display, password = user.get("display_name"), user.get("password")
            if not isinstance(uid, str) or not 1 <= len(uid) <= 64 or uid in state["users"]:
                fail()
            if not isinstance(email, str) or email in seen_emails:
                fail()
            if not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9_]{1,20}", handle) or handle in seen_handles:
                fail()
            if not isinstance(display, str) or not isinstance(password, str):
                fail()
            balance = integer(user.get("balance"), 0, 2**53)
            state["users"][uid] = {"id": uid, "email": email, "handle": handle,
                                  "display_name": display, "password_hash": hash_password(password), "balance": balance}
            seen_emails.add(email)
            seen_handles.add(handle)
        state["seeded_total"] = sum(user["balance"] for user in state["users"].values())
        operators = body.get("settlement_operator_ids", [])
        if not isinstance(operators, list) or any(not isinstance(uid, str) or uid not in state["users"] for uid in operators):
            fail()
        state["settlement_operator_ids"] = list(dict.fromkeys(operators))
        at = instant()
        for field in ("payments", "requests"):
            items = body.get(field, [])
            if not isinstance(items, list):
                fail()
            for item in items:
                if not isinstance(item, dict):
                    fail()
                record_id = item.get("id")
                if not isinstance(record_id, str) or not 1 <= len(record_id) <= 64 or record_id in state[field]:
                    fail()
                amount = integer(item.get("amount"), 0, 10**9)
                memo = note(item)
                created = self._timestamp(item.get("created_at", at))
                if field == "payments":
                    sender, receiver = item.get("from_user_id"), item.get("to_user_id")
                    if not isinstance(sender, str) or not isinstance(receiver, str) or sender not in state["users"] or receiver not in state["users"] or sender == receiver:
                        fail()
                    record = replacement._payment(sender, receiver, amount, memo, visibility(item),
                                                  request_id=item.get("request_id"), settlement_id=item.get("settlement_id"), at=created, pid=record_id)
                else:
                    requester, payer = item.get("requester_id"), item.get("payer_id")
                    if not isinstance(requester, str) or not isinstance(payer, str) or requester not in state["users"] or payer not in state["users"] or requester == payer:
                        fail()
                    status = item.get("status", "pending")
                    if not isinstance(status, str) or status not in ("pending", "paid", "declined", "cancelled"):
                        fail()
                    record = replacement._request(requester, payer, amount, memo, at=created, rid=record_id, status=status, payment_id=item.get("payment_id"))
                state[field][record_id] = record
        return state

    def _import(self, body):
        if body.get("track") != "pocketful" or isinstance(body.get("format_version"), bool) or body.get("format_version") != 1:
            fail()
        state = deepcopy(body.get("state"))
        try:
            self._validate_state(state)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, RecursionError):
            fail()
        return state

    def _validate_state(self, state):
        if not isinstance(state, dict) or isinstance(state.get("schema"), bool) or state.get("schema") != 1:
            fail()
        for field in ("users", "tokens", "payments", "requests", "splits", "settlements", "idempotency"):
            if not isinstance(state.get(field), dict):
                fail()
        if not isinstance(state.get("currency"), str) or not state["currency"]:
            fail()
        state["minor_units"] = integer(state.get("minor_units"), 0, 3)
        if state["minor_units"] not in (0, 2, 3):
            fail()
        users, handles, emails = state["users"], set(), set()
        for uid, user in users.items():
            if not isinstance(user, dict) or user.get("id") != uid or not 1 <= len(uid) <= 64:
                fail()
            for field in ("email", "handle", "password_hash", "display_name"):
                if not isinstance(user.get(field), str):
                    fail()
            if not user["password_hash"] or user["handle"] in handles or user["email"] in emails or not re.fullmatch(r"[a-z0-9_]{1,20}", user["handle"]):
                fail()
            # The portable encoding is owned by auth.py; reject corrupt encodings
            # before replacement rather than silently destroying password login.
            encoded = user["password_hash"].split("$")
            if (len(encoded) != 4 or encoded[0] != "pbkdf2_sha256"
                    or not encoded[1].isdigit() or not 1 <= int(encoded[1]) <= 1_000_000
                    or re.fullmatch(r"[0-9a-f]{32}", encoded[2]) is None
                    or re.fullmatch(r"[0-9a-f]{64}", encoded[3]) is None):
                fail()
            handles.add(user["handle"])
            emails.add(user["email"])
            user["balance"] = integer(user.get("balance"), 0, 2**53)
        total = integer(state.get("seeded_total"), 0)
        if total != sum(user["balance"] for user in users.values()):
            fail()
        state["seeded_total"] = total
        for token, uid in state["tokens"].items():
            if not isinstance(token, str) or not token or uid not in users:
                fail()
        operators = state.get("settlement_operator_ids")
        if not isinstance(operators, list) or any(uid not in users for uid in operators):
            fail()
        for field, id_field, parties in (("payments", "payment_id", ("from_user_id", "to_user_id")),
                                         ("requests", "request_id", ("requester_id", "payer_id"))):
            for rid, record in state[field].items():
                if not isinstance(record, dict) or record.get(id_field) != rid or not 1 <= len(rid) <= 64:
                    fail()
                if any(record.get(party) not in users for party in parties) or record[parties[0]] == record[parties[1]]:
                    fail()
                record["amount"] = integer(record.get("amount"), 0, 10**9)
                note(record)
                self._timestamp(record.get("created_at"))
                if record.get("currency") != state["currency"]:
                    fail()
                aliases = ("from_handle", "to_handle") if field == "payments" else ("requester_handle", "payer_handle")
                if any(record.get(alias) != users[record[party]]["handle"] for alias, party in zip(aliases, parties)):
                    fail()
                if field == "payments":
                    visibility(record)
                    if "request_id" not in record or "settlement_id" not in record:
                        fail()
                    if any(record[link] is not None and not isinstance(record[link], str) for link in ("request_id", "settlement_id")):
                        fail()
                elif record.get("status") not in ("pending", "paid", "declined", "cancelled") or "payment_id" not in record:
                    fail()
                elif record["payment_id"] is not None and record["payment_id"] not in state["payments"]:
                    fail()
        for sid, settlement in state["settlements"].items():
            if not isinstance(settlement, dict) or settlement.get("settlement_id") != sid:
                fail()
            self._timestamp(settlement.get("committed_at"))
            payments = settlement.get("payments")
            if not isinstance(payments, list) or not 1 <= len(payments) <= 32:
                fail()
            seen = set()
            for payment in payments:
                if not isinstance(payment, dict) or payment.get("payment_id") in seen:
                    fail()
                pid = payment.get("payment_id")
                seen.add(pid)
                if (payment != state["payments"].get(pid) or payment.get("settlement_id") != sid
                        or payment.get("created_at") != settlement["committed_at"] or payment.get("request_id") is not None):
                    fail()
        for sid, split in state["splits"].items():
            if not isinstance(split, dict) or split.get("split_id") != sid:
                fail()
            amount = integer(split.get("amount"), 1, 10**9)
            note(split)
            self._timestamp(split.get("created_at"))
            shares, requests = split.get("shares"), split.get("requests")
            if split.get("currency") != state["currency"] or not isinstance(shares, list) or not shares or not isinstance(requests, list):
                fail()
            seen, amounts = set(), []
            for share in shares:
                if not isinstance(share, dict) or share.get("handle") not in handles or share["handle"] in seen:
                    fail()
                seen.add(share["handle"])
                amounts.append(integer(share.get("amount"), 0, 10**9))
            base, extra = divmod(amount, len(shares))
            if amounts != [base + (index < extra) for index in range(len(shares))]:
                fail()
            for request in requests:
                if not isinstance(request, dict) or request.get("request_id") not in state["requests"]:
                    fail()
        for claim, receipt in state["idempotency"].items():
            key = json.loads(claim)
            if (not isinstance(key, list) or len(key) != 4 or any(not isinstance(value, str) for value in key)
                    or key[0] not in users or key[1] != "POST" or not 1 <= len(key[3]) <= 255):
                fail()
            if not isinstance(receipt, dict) or not isinstance(receipt.get("body"), str) or not isinstance(receipt.get("response"), dict):
                fail()
            json.loads(receipt["body"])
        # All stored numbers are integral; this also handles a parser using Decimal
        # for JSON floats during a same-value round trip of an exported state.
        def clean(value):
            if isinstance(value, dict):
                for key in value:
                    value[key] = clean(value[key])
            elif isinstance(value, list):
                for index in range(len(value)):
                    value[index] = clean(value[index])
            elif isinstance(value, (float, Decimal)):
                value = integer(value, -(2**53))
            return value
        clean(state)
        json.dumps(state, allow_nan=False)
