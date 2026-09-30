"""Dwolla REST client for Brittco ACH.

Sandbox by default. Credentials stay in the environment:
DWOLLA_KEY, DWOLLA_SECRET, DWOLLA_ENV=sandbox|production, DWOLLA_WEBHOOK_SECRET.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ACCEPT = "application/vnd.dwolla.v1.hal+json"
# /token rejects the HAL type above and reports a fake Authorization error.
TOKEN_ACCEPT = "application/json"
BUSINESS_TYPES = ("corporation", "llc", "partnership", "soleProprietorship")
# Customer-facing documents. Checked 2026-09-30 against https://www.dwolla.com/legal/ :
# https://www.dwolla.com/legal/tos/ 301s to the account terms URL below.
# https://www.dwolla.com/legal/privacy/ 301s to the privacy URL (no trailing slash).
DWOLLA_TOS_URL = "https://www.dwolla.com/legal/dwolla-account-terms-of-service"
DWOLLA_PRIVACY_URL = "https://www.dwolla.com/legal/privacy"
DWOLLA_TERMS_VERSION = "dwolla-account-tos-2026-09"

_token_cache = {"token": "", "exp": 0.0}


def env_name():
    raw = (os.environ.get("DWOLLA_ENV") or "sandbox").strip().lower()
    if raw in ("production", "prod", "live"):
        return "production"
    return "sandbox"


def api_base():
    if env_name() == "production":
        return "https://api.dwolla.com"
    return "https://api-sandbox.dwolla.com"


def configured():
    return bool(_key() and _secret())


def _key():
    return (os.environ.get("DWOLLA_KEY") or "").strip()


def _secret():
    return (os.environ.get("DWOLLA_SECRET") or "").strip()


def _digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


def signature_ok(secret, raw_body, header_value):
    """True when X-Request-Signature-SHA-256 matches the raw webhook body."""
    if not secret or not header_value or raw_body is None:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(digest, (header_value or "").strip().lower())
    except Exception:
        return False


def business_customer_body(fields):
    """Minimum Business Verified Customer body, plus an optional beneficial owner.

    Used only for Brittco's platform/master customer, not for borrowers or investors.
    Borrowers are unverified personal customers (see create_unverified_customer) and
    cannot hold a Dwolla balance. End-user beneficial ownership is not collected here.
    SSN and full EIN are returned only in this payload for the API call.
    Callers must not persist them.
    """

    def g(name):
        return (fields.get(name) or "").strip()

    business_name = g("business_name")
    email = g("email").lower()
    business_type = g("business_type")
    classification = g("business_classification")
    address1 = g("address1")
    city = g("city")
    state = g("state").upper()
    postal = _digits(g("postal_code"))[:5]
    first = g("controller_first")
    last = g("controller_last")
    title = g("controller_title") or "Controller"
    dob = g("controller_dob")
    ssn = _digits(g("controller_ssn"))
    ein = _digits(g("ein"))
    missing = []
    if not business_name:
        missing.append("legal name")
    if "@" not in email:
        missing.append("email")
    if business_type not in BUSINESS_TYPES:
        missing.append("business type")
    if not classification:
        missing.append("business classification")
    if not address1 or not city or len(state) != 2 or len(postal) != 5:
        missing.append("business address (street, city, 2-letter state, ZIP)")
    if not first or not last:
        missing.append("controller name")
    if len(dob) < 8:
        missing.append("controller date of birth")
    if len(ssn) != 9:
        missing.append("controller SSN (9 digits)")
    if business_type != "soleProprietorship" and len(ein) != 9:
        missing.append("EIN (9 digits)")
    if missing:
        raise ValueError("Dwolla needs: " + ", ".join(missing) + ".")

    if g("controller_same_address") == "1" or not g("controller_address1"):
        c_addr, c_city, c_state, c_postal = address1, city, state, postal
    else:
        c_addr = g("controller_address1")
        c_city = g("controller_city")
        c_state = g("controller_state").upper()
        c_postal = _digits(g("controller_postal_code"))[:5]
        if not c_addr or not c_city or len(c_state) != 2 or len(c_postal) != 5:
            raise ValueError("Dwolla needs the controller's street, city, state, and ZIP.")

    ssn_fmt = "%s-%s-%s" % (ssn[:3], ssn[3:5], ssn[5:])
    address = {
        "address1": c_addr[:255],
        "city": c_city[:50],
        "stateProvinceRegion": c_state,
        "postalCode": c_postal,
        "country": "US",
    }
    body = {
        "firstName": first[:50],
        "lastName": last[:50],
        "email": email,
        "type": "business",
        "address1": address1[:255],
        "city": city[:50],
        "state": state,
        "postalCode": postal,
        "businessName": business_name[:255],
        "businessType": business_type,
        "businessClassification": classification,
        "controller": {
            "firstName": first[:50],
            "lastName": last[:50],
            "title": title[:50],
            "dateOfBirth": dob,
            "ssn": ssn_fmt,
            "address": address,
        },
    }
    if len(ein) == 9:
        body["ein"] = "%s-%s" % (ein[:2], ein[2:])

    try:
        pct = float(g("ownership_pct") or "100")
    except ValueError:
        pct = 100.0
    owner = None
    if pct >= 25:
        owner = {
            "firstName": first[:50],
            "lastName": last[:50],
            "dateOfBirth": dob,
            "ssn": ssn_fmt,
            "address": address,
        }
    return body, owner


def _request(method, url, body=None, form=None, headers=None, timeout=30):
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", ACCEPT)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") if resp else ""
            location = resp.headers.get("Location")
            parsed = json.loads(raw) if raw else {}
            return parsed, location, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else str(exc)
        return None, None, _format_error(raw, exc.code)
    except Exception as exc:
        return None, None, str(exc)


def _format_error(raw, code):
    try:
        payload = json.loads(raw)
    except Exception:
        return raw or ("Dwolla HTTP %s" % code)
    parts = []
    embedded = (payload.get("_embedded") or {}).get("errors") or []
    for item in embedded:
        msg = item.get("message") or item.get("code") or ""
        path = item.get("path") or ""
        if path and msg:
            parts.append("%s: %s" % (path, msg))
        elif msg:
            parts.append(msg)
    if parts:
        return "; ".join(parts)
    return payload.get("message") or raw or ("Dwolla HTTP %s" % code)


def access_token(force=False):
    now = time.time()
    if not force and _token_cache["token"] and _token_cache["exp"] > now + 30:
        return _token_cache["token"], None
    if not configured():
        return None, "Set DWOLLA_KEY and DWOLLA_SECRET."
    basic = base64.b64encode(("%s:%s" % (_key(), _secret())).encode("utf-8")).decode("ascii")
    parsed, _loc, err = _request(
        "POST",
        api_base() + "/token",
        form={"grant_type": "client_credentials"},
        headers={"Authorization": "Basic " + basic, "Accept": TOKEN_ACCEPT},
    )
    if err:
        return None, err
    token = (parsed or {}).get("access_token")
    if not token:
        return None, "Dwolla did not return an access token."
    expires = int((parsed or {}).get("expires_in") or 3600)
    _token_cache["token"] = token
    _token_cache["exp"] = now + expires
    return token, None


def api(method, path_or_url, body=None, timeout=30):
    token, err = access_token()
    if err:
        return None, None, err
    url = path_or_url if str(path_or_url).startswith("http") else api_base() + path_or_url
    headers = {"Authorization": "Bearer " + token, "Accept": ACCEPT}
    if method != "GET":
        headers["Idempotency-Key"] = str(uuid.uuid4())
    parsed, location, err = _request(
        method,
        url,
        body=body if method != "GET" else None,
        headers=headers,
        timeout=timeout,
    )
    if err and "expired" in (err or "").lower():
        token, err2 = access_token(force=True)
        if err2:
            return None, None, err2
        headers["Authorization"] = "Bearer " + token
        parsed, location, err = _request(
            method,
            url,
            body=body if method != "GET" else None,
            headers=headers,
            timeout=timeout,
        )
    return parsed, location, err


def create_business_customer(payload):
    return api("POST", "/customers", payload)


def update_customer(customer_url, payload):
    return api("POST", customer_url, payload)


def get_resource(url, timeout=30):
    return api("GET", url, timeout=timeout)


def find_customer_by_email(email):
    query = urllib.parse.urlencode({"email": email})
    parsed, _loc, err = api("GET", "/customers?" + query)
    if err:
        return None, err
    rows = ((parsed or {}).get("_embedded") or {}).get("customers") or []
    if not rows:
        return None, "No Dwolla customer for that email."
    links = (rows[0].get("_links") or {}).get("self") or {}
    return links.get("href"), None


def list_business_classifications():
    parsed, _loc, err = api("GET", "/business-classifications")
    if err:
        return [], err
    out = []
    groups = ((parsed or {}).get("_embedded") or {}).get("business-classifications") or []
    for group in groups:
        group_name = group.get("name") or ""
        industries = ((group.get("_embedded") or {}).get("industry-classifications")) or []
        for ind in industries:
            iid = ind.get("id") or ""
            if not iid:
                href = ((ind.get("_links") or {}).get("self") or {}).get("href") or ""
                iid = href.rstrip("/").split("/")[-1]
            name = ind.get("name") or "Industry"
            if iid:
                label = "%s — %s" % (group_name, name) if group_name else name
                out.append({"id": iid, "label": label})
    return out, None


def create_funding_source(customer_url, routing, account, bank_account_type, name):
    kind = (bank_account_type or "checking").lower()
    kind = "savings" if "sav" in kind else "checking"
    body = {
        "routingNumber": _digits(routing),
        "accountNumber": _digits(account),
        "bankAccountType": kind,
        "name": (name or "Bank")[:50],
    }
    return api("POST", customer_url.rstrip("/") + "/funding-sources", body)


def initiate_micro_deposits(fs_url):
    return api("POST", fs_url.rstrip("/") + "/micro-deposits", {})


def verify_micro_deposits(fs_url, amount1="0.01", amount2="0.02"):
    body = {
        "amount1": {"value": "%.2f" % float(amount1), "currency": "USD"},
        "amount2": {"value": "%.2f" % float(amount2), "currency": "USD"},
    }
    return api("POST", fs_url.rstrip("/") + "/micro-deposits", body)


def create_unverified_customer(first, last, email):
    """Personal unverified Dwolla customer for a borrower bank-to-bank debit.

    Unverified customers can send and receive ACH. They cannot hold a Dwolla
    balance, so this app does not show a balance wallet for them.
    """
    body = {
        "firstName": (first or "Borrower")[:50],
        "lastName": (last or "Account")[:50],
        "email": email,
        "type": "unverified",
    }
    return api("POST", "/customers", body)


def simulate_sandbox_bank_transfers():
    """Process or fail the latest sandbox bank transfers.

    Dwolla leaves sandbox bank ACH pending until this runs (or the Sandbox
    Dashboard "Process bank transfers" button). POST /sandbox-simulations with
    an empty body. Bank-to-bank pulls need a second call for the credit leg.
    https://developers.dwolla.com/docs/testing#simulate-bank-transfer-processing
    """
    if env_name() != "sandbox":
        return None, "Sandbox bank processing is only available when DWOLLA_ENV=sandbox."
    if not configured():
        return None, "Set DWOLLA_KEY and DWOLLA_SECRET."
    parsed, _loc, err = api("POST", "/sandbox-simulations", {})
    if err:
        return None, err
    total = (parsed or {}).get("total")
    return total, None


def create_transfer(source_url, destination_url, amount, metadata=None):
    body = {
        "_links": {
            "source": {"href": source_url},
            "destination": {"href": destination_url},
        },
        "amount": {"currency": "USD", "value": "%.2f" % float(amount)},
    }
    if metadata:
        clean = {}
        for key, value in metadata.items():
            if value is None or value == "":
                continue
            clean[str(key)[:40]] = str(value)[:255]
        if clean:
            body["metadata"] = clean
    return api("POST", "/transfers", body)


def add_beneficial_owner(customer_url, owner):
    return api("POST", customer_url.rstrip("/") + "/beneficial-owners", owner)


def certify_beneficial_ownership(customer_url):
    return api("POST", customer_url.rstrip("/") + "/beneficial-ownership", {"status": "certified"})
