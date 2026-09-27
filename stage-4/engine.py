"""Pocketful stage-four domain engine.

One lock is the transaction boundary for every read and write, including control
operations and authentication. Receipts and idempotent responses are independent
copies; changing a request never changes an earlier response.
"""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import math
import re
from threading import RLock
from uuid import uuid4
from calendar import monthrange


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


def time_key(value):
    """Exact RFC3339 ordering, retaining sub-microsecond input precision.

    UTC seconds are calculated arithmetically, not with local time or floating
    timestamps. Fractional seconds compare as Decimal values without rounding.
    """
    if not isinstance(value, str):
        fail()
    match = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?([Zz]|[+-][0-9]{2}:[0-9]{2})", value)
    if match is None:
        fail()
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    try:
        date = datetime(year, month, day, hour, minute, min(second, 59))
    except ValueError:
        fail()
    zone = match[8]
    offset = 0
    if zone.upper() != "Z":
        zh, zm = int(zone[1:3]), int(zone[4:6])
        if zh > 23 or zm > 59:
            fail()
        offset = (zh * 60 + zm) * 60 * (1 if zone[0] == "+" else -1)
    if second > 60:
        fail()
    seconds = date.toordinal() * 86400 + hour * 3600 + minute * 60 + min(second, 59) - offset
    if second == 60:
        # RFC3339's insertion position is the final UTC minute of a month.
        # Keep that extra second distinct: :59.f < :60.f < next minute :00.
        try:
            utc_date = datetime.fromordinal(seconds // 86400)
        except ValueError:
            fail()
        if seconds % 86400 != 86399 or utc_date.day != monthrange(utc_date.year, utc_date.month)[1]:
            fail()
        return seconds, Decimal("1." + (match[7] or "0"))
    return seconds, Decimal("0." + (match[7] or "0"))


def blank_state():
    return {"schema": 4, "currency": "EUR", "minor_units": 2,
            "users": {}, "tokens": {}, "payments": {}, "requests": {},
            "splits": {}, "settlements": {}, "idempotency": {},
            "settlement_operator_ids": [], "seeded_total": 0,
            "authorization_ttl_seconds": 600, "authorizations": {},
            "authorization_lifecycle": {}, "opening_balances": {},
            "revisions": {}, "snapshots": {}, "correction_batches": {}}


class Engine:
    def __init__(self):
        self.lock = RLock()
        self.state = blank_state()
        self._now = instant()

    def handle(self, method, path, query, body, headers):
        with self.lock:
            self._now = instant()
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
                result = auth.handle_auth(self.state, path, body)
                self.state["opening_balances"].setdefault(result[1]["user_id"], 0)
                return result
            user_id = auth.authenticate(self.state, headers.get("authorization"))
            if method == "POST" and path in ("/settlements", "/correction-batches"):
                if user_id not in self.state["settlement_operator_ids"]:
                    fail("forbidden", 403)
            request_action = re.fullmatch(r"/requests/([^/]+)/(pay|decline|cancel)", path)
            authorization_action = re.fullmatch(r"/authorizations/([^/]+)/(capture|void)", path)
            payment_action = re.fullmatch(r"/payments/([^/]+)/(corrections|revisions|refunds)", path)
            idempotent = method == "POST" and (
                path in ("/payments", "/requests", "/splits", "/settlements", "/authorizations", "/correction-batches")
                or (request_action and request_action[2] == "pay")
                or (authorization_action and authorization_action[2] == "capture")
                or (payment_action and payment_action[2] in ("corrections", "refunds")))
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
                response = self._me(user_id, query)
            elif method == "GET" and path == "/statement":
                response = self._statement(user_id, query)
            elif method == "GET" and payment_action and payment_action[2] == "revisions":
                payment = self.state["payments"].get(payment_action[1])
                if payment is None or user_id not in (payment["from_user_id"], payment["to_user_id"]):
                    fail("not_found", 404)
                response = {"revisions": self.state["revisions"][payment_action[1]]}
            elif method == "POST" and payment_action and payment_action[2] == "corrections":
                response = self._correct(user_id, payment_action[1], body)
            elif method == "POST" and payment_action and payment_action[2] == "refunds":
                response = self._refund(user_id, payment_action[1], body)
            elif method == "POST" and path == "/correction-batches":
                response = self._correction_batch(body)
            elif method == "GET" and path == "/activity":
                items = [dict(p, authorization_id=p.get("authorization_id"), refund_of=p.get("refund_of")) for p in self.state["payments"].values()
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
            elif method == "GET" and path == "/authorizations":
                direction = self._query(query, "direction")
                status = self._query(query, "status")
                if direction is not None and direction not in ("incoming", "outgoing"):
                    fail()
                if status is not None and status not in ("open", "captured", "voided", "expired"):
                    fail()
                items = [self._authorization_view(a) for a in self.state["authorizations"].values()
                         if user_id in (a["from_user_id"], a["to_user_id"])
                         and (direction is None or a["to_user_id" if direction == "incoming" else "from_user_id"] == user_id)]
                if status is not None:
                    items = [a for a in items if a["status"] == status]
                response = self._page(items, query, "authorizations")
            elif method == "POST" and path == "/authorizations":
                response = self._authorize(user_id, body)
            elif method == "POST" and authorization_action:
                response = self._authorization_action(user_id, authorization_action[1], authorization_action[2], body)
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

    def _pagination(self, query):
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
        return values

    def _page(self, items, query, field):
        limit, offset = self._pagination(query)
        ordered = sorted(items, key=lambda item: time_key(item["created_at"]), reverse=True)
        return {field: ordered[offset:offset + limit], "has_more": offset + limit < len(ordered)}

    def _user_by_handle(self, handle):
        for uid, user in self.state["users"].items():
            if user["handle"] == handle:
                return uid
        fail("not_found", 404)

    def _afford(self, deltas):
        for uid, delta in deltas.items():
            balance = self.state["users"][uid]["balance"] + delta
            if balance - self._held(uid) < 0:
                fail("insufficient_funds", 409)
            if balance > 2**53:
                fail()

    def _payment(self, sender, receiver, amount, memo, public, request_id=None, settlement_id=None, at=None, pid=None, authorization_id=None, refund_of=None):
        users = self.state["users"]
        return {"payment_id": pid or new_id("p_"), "from_user_id": sender,
                "from_handle": users[sender]["handle"], "to_user_id": receiver,
                "to_handle": users[receiver]["handle"], "amount": amount,
                "currency": self.state["currency"], "note": memo, "visibility": public,
                "request_id": request_id, "settlement_id": settlement_id,
                "authorization_id": authorization_id, "refund_of": refund_of, "created_at": at or self._now}

    def _authorization_view(self, authorization):
        result = deepcopy(authorization)
        if result["status"] == "open" and time_key(result["expires_at"]) <= time_key(self._now):
            result["status"] = "expired"
        result["remaining_amount"] = (result["amount"] - result["captured_amount"]
                                      if result["status"] == "open" else 0)
        result["closed_at"] = None
        if result["status"] == "expired":
            result["closed_at"] = result["expires_at"]
        elif result["status"] != "open":
            events = self.state["authorization_lifecycle"].get(result["authorization_id"], [])
            result["closed_at"] = events[-1]["at"] if events else result["created_at"]
        return result

    def _held(self, user_id):
        return sum(self._authorization_view(a)["remaining_amount"]
                   for a in self.state["authorizations"].values() if a["from_user_id"] == user_id)

    @staticmethod
    def _deadline(created_at, ttl):
        """Reject an unrepresentable RFC3339 deadline, never falsify the TTL."""
        try:
            return (datetime.fromisoformat(created_at) + timedelta(seconds=ttl)).isoformat(timespec="microseconds")
        except OverflowError:
            fail()

    def _authorize(self, user_id, body):
        receiver = self._user_by_handle(string(body, "to_handle"))
        amount = integer(body.get("amount"), 1, 10**9)
        memo, public = note(body), visibility(body)
        if receiver == user_id:
            fail("self_payment")
        self._afford({user_id: -amount})
        expires = self._deadline(self._now, self.state["authorization_ttl_seconds"])
        aid = new_id("a_")
        authorization = {"authorization_id": aid, "from_user_id": user_id,
                         "from_handle": self.state["users"][user_id]["handle"],
                         "to_user_id": receiver, "to_handle": self.state["users"][receiver]["handle"],
                         "amount": amount, "captured_amount": 0, "remaining_amount": amount,
                         "currency": self.state["currency"], "note": memo, "visibility": public,
                         "status": "open", "expires_at": expires,
                         "created_at": self._now, "payment_id": None, "payment_ids": []}
        self.state["authorizations"][aid] = authorization
        self.state["authorization_lifecycle"][aid] = [
            {"kind": "created", "at": self._now, "remaining_amount": amount}]
        return self._authorization_view(authorization)

    def _authorization_action(self, user_id, aid, action, body):
        authorization = self.state["authorizations"].get(aid)
        if authorization is None:
            fail("not_found", 404)
        permitted = "to_user_id" if action == "capture" else "from_user_id"
        if authorization[permitted] != user_id:
            fail("forbidden", 403)
        current = self._authorization_view(authorization)
        if action == "void":
            if current["status"] == "voided":
                return current
            if current["status"] != "open":
                fail("authorization_not_open", 409)
            authorization["status"], authorization["remaining_amount"] = "voided", 0
            self.state["authorization_lifecycle"][aid].append(
                {"kind": "voided", "at": self._now, "remaining_amount": 0})
            return self._authorization_view(authorization)
        if current["status"] == "expired":
            fail("authorization_expired", 409)
        if current["status"] != "open":
            fail("authorization_not_open", 409)
        amount = integer(body.get("amount", current["remaining_amount"]), 1)
        if amount > current["remaining_amount"]:
            fail("capture_exceeds_authorization")
        final = body.get("final", True)
        if not isinstance(final, bool):
            fail("malformed_request", 400)
        sender, receiver = authorization["from_user_id"], authorization["to_user_id"]
        if self.state["users"][receiver]["balance"] + amount > 2**53:
            fail()
        payment = self._payment(sender, receiver, amount, authorization["note"], authorization["visibility"], authorization_id=aid)
        self._commit_payments([payment])
        authorization["captured_amount"] += amount
        authorization["payment_id"] = payment["payment_id"]
        authorization["payment_ids"].append(payment["payment_id"])
        remaining = authorization["amount"] - authorization["captured_amount"]
        if final or remaining == 0:
            authorization["status"] = "captured"
            remaining = 0
        authorization["remaining_amount"] = remaining
        self.state["authorization_lifecycle"][aid].append(
            {"kind": "captured" if authorization["status"] == "captured" else "partial_capture",
             "at": self._now, "remaining_amount": remaining, "payment_id": payment["payment_id"]})
        return payment

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
            self.state["revisions"][payment["payment_id"]] = [self._original_revision(payment)]

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
    def _original_revision(payment):
        return {"payment_id": payment["payment_id"], "revision": 1,
                "amount": payment["amount"], "effective_at": payment["created_at"],
                "recorded_at": payment["created_at"], "reason": ""}

    @staticmethod
    def _initialize_ledger(state):
        openings = {uid: user["balance"] for uid, user in state["users"].items()}
        revisions = {}
        for pid, payment in state["payments"].items():
            openings[payment["from_user_id"]] += payment["amount"]
            openings[payment["to_user_id"]] -= payment["amount"]
            revisions[pid] = [Engine._original_revision(payment)]
        state.update(schema=4, opening_balances=openings, revisions=revisions, snapshots={}, correction_batches={})

    def _selected(self, known=None, overrides=None):
        selected = {}
        for pid, revisions in self.state["revisions"].items():
            if overrides and pid in overrides:
                selected[pid] = overrides[pid]
            elif known is None:
                selected[pid] = revisions[-1]
            else:
                revision = next((r for r in reversed(revisions) if time_key(r["recorded_at"]) <= known), None)
                if revision is not None:
                    selected[pid] = revision
        return selected

    def _hold_at(self, aid, as_of, known=None):
        authorization = self.state["authorizations"][aid]
        events = self.state["authorization_lifecycle"][aid]
        creation = time_key(authorization["created_at"])
        if creation > as_of or (known is not None and creation > known):
            return 0
        # Once creation is known, the expiry deadline is already known, even in
        # a future as_of view with an earlier known_at cutoff.
        if time_key(authorization["expires_at"]) <= as_of:
            return 0
        remaining = 0
        for event in events:
            event_time = time_key(event["at"])
            if event_time <= as_of and (known is None or event_time <= known):
                remaining = event["remaining_amount"]
        return remaining

    def _me(self, user_id, query):
        supplied_as_of = self._query(query, "as_of")
        supplied_known = self._query(query, "known_at")
        as_of = time_key(self._now if supplied_as_of is None else supplied_as_of)
        known = time_key(supplied_known) if supplied_known is not None else None
        total = self.state["opening_balances"][user_id]
        for pid, revision in self._selected(known).items():
            payment = self.state["payments"][pid]
            if time_key(revision["effective_at"]) <= as_of:
                if payment["from_user_id"] == user_id:
                    total -= revision["amount"]
                elif payment["to_user_id"] == user_id:
                    total += revision["amount"]
        held = sum(self._hold_at(aid, as_of, known) for aid, authorization in self.state["authorizations"].items()
                   if authorization["from_user_id"] == user_id)
        user = self.state["users"][user_id]
        result = {"user_id": user_id, "display_name": user["display_name"], "handle": user["handle"],
                  "balance": total, "total": total, "held": held, "available": total - held,
                  "currency": self.state["currency"], "minor_units": self.state["minor_units"]}
        if supplied_as_of is not None:
            result["as_of"] = supplied_as_of
        if supplied_known is not None:
            result["known_at"] = supplied_known
        return result

    def _statement(self, user_id, query):
        limit, offset = self._pagination(query)
        token = self._query(query, "snapshot")
        if token is not None:
            if any(name in query for name in ("from", "to", "known_at")):
                fail()
            frozen = self.state["snapshots"].get(token)
            if frozen is None or frozen["user_id"] != user_id:
                fail("not_found", 404)
        else:
            supplied_from, supplied_to = self._query(query, "from"), self._query(query, "to")
            supplied_known = self._query(query, "known_at")
            start = time_key(supplied_from) if supplied_from is not None else None
            end = time_key(self._now if supplied_to is None else supplied_to)
            known = time_key(supplied_known) if supplied_known is not None else None
            if start is not None and start > end:
                fail()
            selected = [(pid, revision) for pid, revision in self._selected(known).items()
                        if user_id in (self.state["payments"][pid]["from_user_id"], self.state["payments"][pid]["to_user_id"])]
            selected.sort(key=lambda pair: (time_key(pair[1]["effective_at"]), pair[0]))
            running = self.state["opening_balances"][user_id]
            opening = running
            entries = []
            for pid, revision in selected:
                effective = time_key(revision["effective_at"])
                if effective >= end:
                    break
                payment = self.state["payments"][pid]
                delta = revision["amount"] * (-1 if payment["from_user_id"] == user_id else 1)
                running += delta
                if start is not None and effective < start:
                    opening = running
                    continue
                statement_payment = dict(payment, amount=revision["amount"], authorization_id=payment.get("authorization_id"), refund_of=payment.get("refund_of"))
                entries.append({"payment": statement_payment, "delta": delta, "balance_after": running,
                                "revision": revision["revision"], "effective_at": revision["effective_at"],
                                "recorded_at": revision["recorded_at"]})
            token = new_id("ss_")
            data = {"opening_balance": opening, "closing_balance": running, "entries": entries}
            if supplied_known is not None:
                data["known_at"] = supplied_known
            frozen = {"user_id": user_id, "data": data,
                      "window": {"from": supplied_from, "to": supplied_to or self._now,
                                 "known_at": supplied_known}}
            self.state["snapshots"][token] = frozen
        data = deepcopy(frozen["data"])
        entries = data["entries"]
        data.update(snapshot=token, entries=entries[offset:offset + limit], has_more=offset + limit < len(entries))
        return data

    def _correction_fields(self, body):
        expected = integer(body.get("expected_revision"), 1)
        amount = integer(body.get("amount"), 0, 10**9)
        effective = body.get("effective_at")
        if time_key(effective) > time_key(self._now):
            fail()
        reason = body.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 200:
            fail()
        return expected, amount, effective, reason

    def _correct(self, user_id, pid, body):
        payment = self.state["payments"].get(pid)
        if payment is None:
            fail("not_found", 404)
        if payment["from_user_id"] != user_id:
            fail("forbidden", 403)
        if any(payment.get(link) is not None for link in ("settlement_id", "authorization_id", "refund_of")):
            fail("linked_payment_immutable")
        expected, amount, effective, reason = self._correction_fields(body)
        previous = self.state["revisions"][pid][-1]
        if expected != previous["revision"]:
            fail("stale_revision", 409)
        if amount < self._refunded(pid):
            fail("refund_exceeds_payment")
        change = amount - previous["amount"]
        deltas = {payment["from_user_id"]: -change, payment["to_user_id"]: change}
        self._afford(deltas)
        recorded = self._recorded_after([previous])
        revision = {"payment_id": pid, "revision": expected + 1, "amount": amount,
                    "effective_at": effective, "recorded_at": recorded, "reason": reason}
        self._assert_history({pid: revision})
        for uid, delta in deltas.items():
            self.state["users"][uid]["balance"] += delta
        self.state["revisions"][pid].append(revision)
        return revision

    def _recorded_after(self, previous):
        """One timestamp strictly later than every replaced revision."""
        latest = max(time_key(revision["recorded_at"]) for revision in previous)
        recorded = self._now
        if time_key(recorded) <= latest:
            try:
                seconds, fraction = latest
                # Round strictly upward to our server's microsecond precision;
                # client fractions, offsets, lowercase forms and leap notation
                # never pass through datetime.fromisoformat truncation.
                whole, fractional = format(fraction, "f").split(".")
                micros = int(whole) * 10**6 + int((fractional + "000000")[:6]) + 1
                recorded = (datetime.fromordinal(seconds // 86400).replace(tzinfo=timezone.utc)
                            + timedelta(seconds=seconds % 86400, microseconds=micros)).isoformat(timespec="microseconds")
            except (ValueError, OverflowError):
                fail()
        return recorded

    def _refunded(self, pid):
        return sum(payment["amount"] for payment in self.state["payments"].values()
                   if payment.get("refund_of") == pid)

    def _refund(self, user_id, pid, body):
        payment = self.state["payments"].get(pid)
        if payment is None:
            fail("not_found", 404)
        if payment["to_user_id"] != user_id:
            fail("forbidden", 403)
        if payment.get("refund_of") is not None:
            fail("invalid_refund_target")
        amount = integer(body.get("amount"), 1, 10**9)
        if self._refunded(pid) + amount > self.state["revisions"][pid][-1]["amount"]:
            fail("refund_exceeds_payment")
        self._afford({user_id: -amount, payment["from_user_id"]: amount})
        refund = self._payment(user_id, payment["from_user_id"], amount,
                               payment["note"], payment["visibility"], refund_of=pid)
        self._commit_payments([refund])
        return refund

    def _correction_batch(self, body):
        items = body.get("corrections")
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            fail()
        # Establish the specified whole-container shape first; only then scan
        # complete ordinary item validation in input order, never by field.
        identifiers = set()
        for item in items:
            if not isinstance(item, dict):
                fail()
            pid = item.get("payment_id")
            if not isinstance(pid, str) or not 1 <= len(pid) <= 64 or pid in identifiers:
                fail()
            identifiers.add(pid)
        proposals, previous, deltas, settlements = {}, [], {}, {}
        for item in items:
            pid = item["payment_id"]
            payment = self.state["payments"].get(pid)
            if payment is None:
                fail("not_found", 404)
            if payment.get("authorization_id") is not None or payment.get("refund_of") is not None:
                fail("linked_payment_immutable")
            expected, amount, effective, reason = self._correction_fields(item)
            latest = self.state["revisions"][pid][-1]
            if expected != latest["revision"]:
                fail("stale_revision", 409)
            if amount < self._refunded(pid):
                fail("refund_exceeds_payment")
            proposals[pid] = {"payment_id": pid, "revision": expected + 1,
                              "amount": amount, "effective_at": effective, "reason": reason}
            previous.append(latest)
            change = amount - latest["amount"]
            for uid, delta in ((payment["from_user_id"], -change), (payment["to_user_id"], change)):
                deltas[uid] = deltas.get(uid, 0) + delta
            if payment.get("settlement_id") is not None:
                settlements.setdefault(payment["settlement_id"], []).append(time_key(effective))
        # Item errors have all been resolved before group constraints or funds.
        for sid in settlements:
            members = {pid for pid, p in self.state["payments"].items() if p.get("settlement_id") == sid}
            if not members.issubset(proposals):
                fail("incomplete_settlement")
        if any(len(set(instants)) != 1 for instants in settlements.values()):
            fail()
        self._afford(deltas)
        recorded, bid = self._recorded_after(previous), new_id("cb_")
        for revision in proposals.values():
            revision.update(recorded_at=recorded, correction_batch_id=bid)
        self._assert_history(proposals)
        for uid, delta in deltas.items():
            self.state["users"][uid]["balance"] += delta
        for pid, revision in proposals.items():
            self.state["revisions"][pid].append(revision)
        response = {"correction_batch_id": bid, "recorded_at": recorded, "revisions": list(proposals.values())}
        self.state["correction_batches"][bid] = deepcopy(response)
        return response

    def _assert_history(self, overrides):
        """Aggregate simultaneous money/hold events before testing a boundary."""
        boundaries = {}
        now = time_key(self._now)

        def event(at, uid, money=0, hold=0):
            if at > now:
                return
            changes = boundaries.setdefault(at, {})
            prior = changes.setdefault(uid, [0, 0])
            prior[0] += money
            prior[1] += hold

        for pid, revision in self._selected(overrides=overrides).items():
            payment = self.state["payments"][pid]
            at = time_key(revision["effective_at"])
            event(at, payment["from_user_id"], money=-revision["amount"])
            event(at, payment["to_user_id"], money=revision["amount"])
        for aid, authorization in self.state["authorizations"].items():
            uid = authorization["from_user_id"]
            expiry = time_key(authorization["expires_at"])
            remaining = 0
            for lifecycle in self.state["authorization_lifecycle"][aid]:
                at = time_key(lifecycle["at"])
                if at >= expiry:
                    break
                value = lifecycle["remaining_amount"]
                event(at, uid, hold=value - remaining)
                remaining = value
            if remaining:
                event(expiry, uid, hold=-remaining)
        balances = self.state["opening_balances"].copy()
        held = {uid: 0 for uid in balances}
        if any(balance < 0 for balance in balances.values()):
            fail("historical_overdraft", 409)
        for at in sorted(boundaries):
            for uid, (money, hold) in boundaries[at].items():
                balances[uid] += money
                held[uid] += hold
                if balances[uid] < 0 or balances[uid] - held[uid] < 0:
                    fail("historical_overdraft", 409)

    @staticmethod
    def _timestamp(value):
        time_key(value)
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
        replacement._now = at
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
                    if time_key(created) > time_key(at):
                        fail()
                    sender, receiver = item.get("from_user_id"), item.get("to_user_id")
                    if not isinstance(sender, str) or not isinstance(receiver, str) or sender not in state["users"] or receiver not in state["users"] or sender == receiver:
                        fail()
                    record = replacement._payment(sender, receiver, amount, memo, visibility(item),
                                                  request_id=item.get("request_id"), settlement_id=item.get("settlement_id"), at=created, pid=record_id,
                                                  authorization_id=item.get("authorization_id"))
                else:
                    requester, payer = item.get("requester_id"), item.get("payer_id")
                    if not isinstance(requester, str) or not isinstance(payer, str) or requester not in state["users"] or payer not in state["users"] or requester == payer:
                        fail()
                    status = item.get("status", "pending")
                    if not isinstance(status, str) or status not in ("pending", "paid", "declined", "cancelled"):
                        fail()
                    record = replacement._request(requester, payer, amount, memo, at=created, rid=record_id, status=status, payment_id=item.get("payment_id"))
                state[field][record_id] = record
        ttl = body.get("authorization_ttl_seconds", 600)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float, Decimal)):
            fail("malformed_request", 400)
        state["authorization_ttl_seconds"] = integer(ttl, 1)
        self._deadline(at, state["authorization_ttl_seconds"])
        authorizations = body.get("authorizations", [])
        if not isinstance(authorizations, list):
            fail()
        for item in authorizations:
            if not isinstance(item, dict):
                fail()
            aid = item.get("id")
            if not isinstance(aid, str) or not 1 <= len(aid) <= 64 or aid in state["authorizations"]:
                fail()
            sender, receiver = item.get("from_user_id"), item.get("to_user_id")
            if not isinstance(sender, str) or not isinstance(receiver, str) or sender not in state["users"] or receiver not in state["users"] or sender == receiver:
                fail()
            amount = integer(item.get("amount"), 1, 10**9)
            status = item.get("status", "open")
            if not isinstance(status, str) or status not in ("open", "captured", "voided", "expired"):
                fail()
            captured = integer(item.get("captured_amount", amount if status == "captured" else 0), 0, amount)
            if status == "open" and captured == amount:
                fail()
            created = self._timestamp(item.get("created_at", at))
            expires = self._timestamp(item.get("expires_at"))
            payment_ids = item.get("payment_ids", [item["payment_id"]] if item.get("payment_id") else [])
            if not isinstance(payment_ids, list) or any(not isinstance(pid, str) or pid not in state["payments"] for pid in payment_ids):
                fail()
            if len(set(payment_ids)) != len(payment_ids):
                fail()
            authorization = {"authorization_id": aid, "from_user_id": sender,
                             "from_handle": state["users"][sender]["handle"], "to_user_id": receiver,
                             "to_handle": state["users"][receiver]["handle"], "amount": amount,
                             "captured_amount": captured, "remaining_amount": amount - captured if status == "open" else 0,
                             "currency": state["currency"], "note": note(item), "visibility": visibility(item),
                             "status": status, "expires_at": expires, "created_at": created,
                             "payment_id": payment_ids[-1] if payment_ids else None, "payment_ids": list(payment_ids)}
            state["authorizations"][aid] = authorization
            # A seeded closed hold has no recoverable earlier lifecycle; seeded
            # open holds start at their supplied creation time (otherwise reset).
            state["authorization_lifecycle"][aid] = [
                {"kind": "created" if status == "open" else "seeded_closed", "at": created,
                 "remaining_amount": authorization["remaining_amount"]}]
        if any(user["balance"] - replacement._held(uid) < 0 for uid, user in state["users"].items()):
            fail()
        self._initialize_ledger(state)
        return state

    def _import(self, body):
        if body.get("track") != "pocketful" or isinstance(body.get("format_version"), bool) or body.get("format_version") != 1:
            fail()
        state = deepcopy(body.get("state"))
        try:
            self._validate_state(state)
            # Validate the replacement, never temporarily publish it on self.
            # Ending balances alone cannot prove past total/available solvency.
            candidate = Engine()
            candidate.state = state
            candidate._now = self._now
            candidate._assert_history({})
        except ApiError as error:
            if error.code == "historical_overdraft":
                fail()
            raise
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, RecursionError):
            fail()
        return state

    def _validate_state(self, state):
        if not isinstance(state, dict) or isinstance(state.get("schema"), bool) or state.get("schema") not in (1, 2, 3, 4):
            fail()
        source_schema = state["schema"]
        legacy = state["schema"] == 1
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
        if legacy:
            # Do not augment any historical receipt or idempotent response: an
            # old payment replay must retain the old response as a JSON value.
            state.update(schema=2, authorization_ttl_seconds=600,
                         authorizations={}, authorization_lifecycle={})
        else:
            self._validate_authorizations(state)
        if source_schema < 3:
            self._initialize_ledger(state)
        else:
            self._validate_ledger(state)
        if source_schema < 4:
            state.update(schema=4, correction_batches={})
        self._validate_stage4(state)

    def _validate_stage4(self, state):
        """Validate portable refund links and immutable batch membership."""
        payments, histories = state["payments"], state["revisions"]
        refunded = {}
        for pid, payment in payments.items():
            target_id = payment.get("refund_of")
            if target_id is not None:
                if not isinstance(target_id, str) or target_id not in payments:
                    fail()
                target = payments[target_id]
                if (target.get("refund_of") is not None
                        or payment["from_user_id"] != target["to_user_id"]
                        or payment["to_user_id"] != target["from_user_id"]
                        or payment["note"] != target["note"] or payment["visibility"] != target["visibility"]
                        or any(payment.get(link) is not None for link in ("request_id", "authorization_id", "settlement_id"))
                        or payment["amount"] < 1):
                    fail()
                refunded[target_id] = refunded.get(target_id, 0) + payment["amount"]
            if (target_id is not None or payment.get("authorization_id") is not None) and len(histories[pid]) != 1:
                fail()
        if any(amount > histories[pid][-1]["amount"] for pid, amount in refunded.items()):
            fail()
        batches = state.get("correction_batches")
        if not isinstance(batches, dict):
            fail()
        linked = set()
        for bid, batch in batches.items():
            if (not isinstance(bid, str) or not 1 <= len(bid) <= 64 or not isinstance(batch, dict)
                    or batch.get("correction_batch_id") != bid):
                fail()
            recorded = time_key(batch.get("recorded_at"))
            revisions = batch.get("revisions")
            if not isinstance(revisions, list) or not 1 <= len(revisions) <= 32:
                fail()
            seen, settlements = set(), {}
            for revision in revisions:
                if not isinstance(revision, dict):
                    fail()
                pid = revision.get("payment_id")
                if not isinstance(pid, str) or pid not in histories or pid in seen:
                    fail()
                seen.add(pid)
                number = integer(revision.get("revision"), 2, len(histories[pid]))
                if (revision != histories[pid][number - 1] or revision.get("correction_batch_id") != bid
                        or time_key(revision["recorded_at"]) != recorded):
                    fail()
                linked.add((pid, number))
                sid = payments[pid].get("settlement_id")
                if sid is not None:
                    settlements.setdefault(sid, set()).add(time_key(revision["effective_at"]))
            for sid, times in settlements.items():
                members = {pid for pid, payment in payments.items() if payment.get("settlement_id") == sid}
                if not members.issubset(seen) or len(times) != 1:
                    fail()
        for pid, history in histories.items():
            for revision in history[1:]:
                if ((revision.get("correction_batch_id") is not None or payments[pid].get("settlement_id") is not None)
                        and (pid, revision["revision"]) not in linked):
                    fail()

    def _validate_ledger(self, state):
        openings, revisions, snapshots = state.get("opening_balances"), state.get("revisions"), state.get("snapshots")
        if not isinstance(openings, dict) or set(openings) != set(state["users"]):
            fail()
        if not isinstance(revisions, dict) or set(revisions) != set(state["payments"]) or not isinstance(snapshots, dict):
            fail()
        balances = {uid: integer(value, -(2**53)) for uid, value in openings.items()}
        for pid, history in revisions.items():
            payment = state["payments"][pid]
            if not isinstance(history, list) or not history or history[0] != self._original_revision(payment):
                fail()
            previous = None
            for number, revision in enumerate(history, 1):
                if not isinstance(revision, dict) or revision.get("payment_id") != pid or integer(revision.get("revision"), 1) != number:
                    fail()
                integer(revision.get("amount"), 0, 10**9)
                time_key(revision.get("effective_at"))
                recorded = time_key(revision.get("recorded_at"))
                if previous is not None and recorded <= previous:
                    fail()
                previous = recorded
                reason = revision.get("reason")
                if not isinstance(reason, str) or len(reason) > 200 or (number > 1 and not reason):
                    fail()
            balances[payment["from_user_id"]] -= history[-1]["amount"]
            balances[payment["to_user_id"]] += history[-1]["amount"]
        if any(value != state["users"][uid]["balance"] for uid, value in balances.items()):
            fail()
        if sum(openings.values()) != state["seeded_total"]:
            fail()
        for token, frozen in snapshots.items():
            if not isinstance(token, str) or not 1 <= len(token) <= 64 or not isinstance(frozen, dict):
                fail()
            uid, data, window = frozen.get("user_id"), frozen.get("data"), frozen.get("window")
            if uid not in state["users"] or not isinstance(data, dict) or not isinstance(window, dict):
                fail()
            for name in ("from", "to", "known_at"):
                if name not in window:
                    fail()
                if window[name] is not None:
                    time_key(window[name])
            opening = integer(data.get("opening_balance"), -(2**53))
            closing = integer(data.get("closing_balance"), -(2**53))
            entries = data.get("entries")
            if not isinstance(entries, list):
                fail()
            running, prior = opening, None
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("payment"), dict):
                    fail()
                payment = entry["payment"]
                pid = payment.get("payment_id")
                if pid not in state["payments"] or uid not in (payment.get("from_user_id"), payment.get("to_user_id")):
                    fail()
                number = integer(entry.get("revision"), 1, len(revisions[pid]))
                revision = revisions[pid][number - 1]
                if (entry.get("effective_at") != revision["effective_at"] or entry.get("recorded_at") != revision["recorded_at"]
                        or payment.get("amount") != revision["amount"]):
                    fail()
                order = time_key(entry["effective_at"]), pid
                if prior is not None and order <= prior:
                    fail()
                prior = order
                delta = integer(entry.get("delta"), -(10**9), 10**9)
                if delta != revision["amount"] * (-1 if payment["from_user_id"] == uid else 1):
                    fail()
                running += delta
                if entry.get("balance_after") != running:
                    fail()
            if running != closing:
                fail()

    def _validate_authorizations(self, state):
        state["authorization_ttl_seconds"] = integer(state.get("authorization_ttl_seconds"), 1)
        authorizations, lifecycles = state.get("authorizations"), state.get("authorization_lifecycle")
        if not isinstance(authorizations, dict) or not isinstance(lifecycles, dict) or set(authorizations) != set(lifecycles):
            fail()
        users, payments = state["users"], state["payments"]
        held = {}
        for aid, authorization in authorizations.items():
            if not isinstance(authorization, dict) or authorization.get("authorization_id") != aid or not 1 <= len(aid) <= 64:
                fail()
            sender, receiver = authorization.get("from_user_id"), authorization.get("to_user_id")
            if sender not in users or receiver not in users or sender == receiver:
                fail()
            if authorization.get("from_handle") != users[sender]["handle"] or authorization.get("to_handle") != users[receiver]["handle"]:
                fail()
            amount = integer(authorization.get("amount"), 1, 10**9)
            captured = integer(authorization.get("captured_amount"), 0, amount)
            status = authorization.get("status")
            if status not in ("open", "captured", "voided", "expired") or (status == "open" and captured == amount):
                fail()
            remaining = integer(authorization.get("remaining_amount"), 0, amount)
            if remaining != (amount - captured if status == "open" else 0):
                fail()
            if authorization.get("currency") != state["currency"]:
                fail()
            note(authorization)
            visibility(authorization)
            self._timestamp(authorization.get("created_at"))
            self._timestamp(authorization.get("expires_at"))
            payment_ids = authorization.get("payment_ids")
            if not isinstance(payment_ids, list) or len(set(payment_ids)) != len(payment_ids):
                fail()
            if authorization.get("payment_id") != (payment_ids[-1] if payment_ids else None):
                fail()
            for pid in payment_ids:
                payment = payments.get(pid)
                if (not isinstance(payment, dict) or payment.get("authorization_id") != aid
                        or payment.get("from_user_id") != sender or payment.get("to_user_id") != receiver
                        or payment.get("request_id") is not None):
                    fail()
            # Seeded closed records can have a captured amount without a supplied
            # prior capture; actual supplied captures still cannot exceed it.
            if sum(payments[pid]["amount"] for pid in payment_ids) > captured:
                fail()
            events = lifecycles[aid]
            if not isinstance(events, list) or not events:
                fail()
            previous_at = None
            for event in events:
                if not isinstance(event, dict) or event.get("kind") not in ("created", "seeded_closed", "partial_capture", "captured", "voided"):
                    fail()
                when = time_key(event.get("at"))
                if previous_at is not None and when < previous_at:
                    fail()
                previous_at = when
                integer(event.get("remaining_amount"), 0, amount)
                if "payment_id" in event and event["payment_id"] not in payment_ids:
                    fail()
            held[sender] = held.get(sender, 0) + self._authorization_view(authorization)["remaining_amount"]
        if any(users[uid]["balance"] < amount for uid, amount in held.items()):
            fail()
