"""Authentication primitives; callers hold the engine lock during state access.

Only password digests and random bearer tokens are stored. No global indexes or
process-local authentication state are needed, so an export is portable.
"""

import hashlib
import hmac
import re
import secrets
import uuid


PASSWORD_ITERATIONS = 120_000


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8", errors="surrogatepass"), salt,
                                 PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 1 <= iterations <= 1_000_000:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8", errors="surrogatepass"),
                                     salt_bytes, iterations)
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError, UnicodeError):
        return False


def authenticate(state, authorization_header):
    from engine import ApiError

    if not isinstance(authorization_header, str):
        raise ApiError(401, "unauthenticated", "A bearer token is required.")
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ApiError(401, "unauthenticated", "Invalid bearer token.")
    user_id = state["tokens"].get(parts[1])
    if user_id not in state["users"]:
        raise ApiError(401, "unauthenticated", "Invalid bearer token.")
    return user_id


def _string(body, field):
    from engine import ApiError

    if field not in body:
        raise ApiError(422, "validation_failed", f"{field} is required.")
    if not isinstance(body[field], str):
        raise ApiError(400, "malformed_request", f"{field} must be a string.")
    return body[field]


def handle_auth(state, path, body):
    from engine import ApiError

    if not isinstance(body, dict):
        raise ApiError(400, "malformed_request", "A JSON object is required.")
    email = _string(body, "email")
    password = _string(body, "password")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
        raise ApiError(422, "validation_failed", "Use an email in local@domain form.")
    existing = next((u for u in state["users"].values() if u["email"] == email), None)
    if path == "/auth/signup":
        display_name = _string(body, "display_name")
        if len(password) < 8:
            raise ApiError(422, "validation_failed", "Password must have at least 8 characters.")
        if existing is not None:
            raise ApiError(409, "email_taken", "That email is already registered.")
        handle = re.sub(r"[^a-z0-9_]", "_", email.split("@", 1)[0].lower())[:20]
        if any(u["handle"] == handle for u in state["users"].values()):
            raise ApiError(409, "handle_taken", "That handle is already registered.")
        user_id = "u_" + uuid.uuid4().hex
        user = {"id": user_id, "email": email, "display_name": display_name,
                "handle": handle, "password_hash": hash_password(password), "balance": 0}
        state["users"][user_id] = user
        status = 201
    elif path == "/auth/login":
        if existing is None or not verify_password(password, existing["password_hash"]):
            raise ApiError(401, "unauthenticated", "Email or password is incorrect.")
        user, status = existing, 200
    else:
        raise ApiError(404, "not_found", "Unknown authentication endpoint.")
    token = secrets.token_urlsafe(32)
    state["tokens"][token] = user["id"]
    return status, {"user_id": user["id"], "display_name": user["display_name"], "token": token}
