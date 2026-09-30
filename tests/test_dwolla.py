"""Dwolla onboarding gate, webhook accounting, and customer payload checks."""

import hashlib
import hmac
import json
import os
import tempfile
from unittest.mock import patch

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="brittco-dwolla-")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["DWOLLA_WEBHOOK_SECRET"] = "whsec-test"
os.environ["PLATFORM_MODE"] = "brittco_existing"
os.environ.pop("DWOLLA_KEY", None)
os.environ.pop("DWOLLA_SECRET", None)
os.environ.pop("DWOLLA_MASTER_CUSTOMER_URL", None)
os.environ.pop("PAYMENTS_PROVIDER", None)
os.environ.pop("STRIPE_SECRET_KEY", None)

import dwolla_client
import app as brittco


def _sample_company(**overrides):
    fields = {
        "business_name": "Northwind Lending LLC",
        "email": "ops@northwind.example",
        "business_type": "llc",
        "business_classification": "9ed3f670-7d6f-11e3-b1ce-5404a6144203",
        "address1": "100 Main St",
        "city": "Kansas City",
        "state": "mo",
        "postal_code": "64105",
        "controller_first": "Ada",
        "controller_last": "Lovelace",
        "controller_title": "Owner",
        "controller_dob": "1980-01-31",
        "controller_ssn": "123-45-6789",
        "ein": "12-3456789",
        "ownership_pct": "100",
        "controller_same_address": "1",
    }
    fields.update(overrides)
    return fields


def test_business_payload_minimum_fields():
    body, owner = dwolla_client.business_customer_body(_sample_company())
    assert body["type"] == "business"
    assert body["businessType"] == "llc"
    assert body["ein"] == "12-3456789"
    assert body["controller"]["ssn"] == "123-45-6789"
    assert body["state"] == "MO"
    assert owner["firstName"] == "Ada"


def test_business_payload_requires_ein_for_llc():
    try:
        dwolla_client.business_customer_body(_sample_company(ein=""))
    except ValueError as exc:
        assert "EIN" in str(exc)
    else:
        raise AssertionError("expected EIN to be required")


class _TokenResponse:
    def __init__(self, payload, location=None):
        self._payload = payload
        self.headers = {"Location": location} if location else {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reset_token_cache():
    dwolla_client._token_cache["token"] = ""
    dwolla_client._token_cache["exp"] = 0.0


def test_token_request_accept_is_json_not_hal():
    os.environ["DWOLLA_KEY"] = "test-key"
    os.environ["DWOLLA_SECRET"] = "test-secret"
    os.environ["DWOLLA_ENV"] = "sandbox"
    _reset_token_cache()
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["accept"] = req.get_header("Accept")
        seen["authorization"] = req.get_header("Authorization") or ""
        seen["body"] = req.data
        return _TokenResponse(b'{"access_token":"sandbox-token","expires_in":3600}')

    try:
        with patch("urllib.request.urlopen", fake_urlopen):
            token, err = dwolla_client.access_token(force=True)
        assert err is None, err
        assert token == "sandbox-token"
        assert seen["method"] == "POST"
        assert seen["url"] == "https://api-sandbox.dwolla.com/token"
        assert seen["accept"] == "application/json"
        assert seen["accept"] != dwolla_client.ACCEPT
        assert "grant_type=client_credentials" in seen["body"].decode("utf-8")
        assert seen["authorization"].startswith("Basic ")
    finally:
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)
        _reset_token_cache()


def test_api_requests_keep_hal_accept():
    os.environ["DWOLLA_ENV"] = "sandbox"
    dwolla_client._token_cache["token"] = "cached-token"
    dwolla_client._token_cache["exp"] = 10**12
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["url"] = req.full_url
        seen["accept"] = req.get_header("Accept")
        seen["authorization"] = req.get_header("Authorization") or ""
        return _TokenResponse(b'{"id":"cust"}')

    try:
        with patch("urllib.request.urlopen", fake_urlopen):
            parsed, _loc, err = dwolla_client.api("GET", "/customers/abc")
        assert err is None, err
        assert parsed["id"] == "cust"
        assert seen["url"] == "https://api-sandbox.dwolla.com/customers/abc"
        assert seen["accept"] == dwolla_client.ACCEPT
        assert seen["authorization"] == "Bearer cached-token"
    finally:
        _reset_token_cache()


def test_signature_round_trip():
    raw = b'{"topic":"transfer_completed"}'
    secret = "whsec-test"
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert dwolla_client.signature_ok(secret, raw, sig)
    assert not dwolla_client.signature_ok(secret, raw, "nope")
    assert not dwolla_client.signature_ok("", raw, sig)


def test_provider_prefers_dwolla_when_keys_present():
    os.environ.pop("PAYMENTS_PROVIDER", None)
    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
    assert brittco.payments_provider() == "dwolla"
    os.environ["PAYMENTS_PROVIDER"] = "stripe"
    assert brittco.payments_provider() == "stripe"
    os.environ.pop("PAYMENTS_PROVIDER", None)
    os.environ.pop("DWOLLA_KEY", None)
    os.environ.pop("DWOLLA_SECRET", None)
    assert brittco.payments_provider() == "stripe"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    assert brittco.payments_provider() == "manual"


def test_existing_brittco_skips_onboarding():
    os.environ["PLATFORM_MODE"] = "brittco_existing"
    os.environ.pop("DWOLLA_MASTER_CUSTOMER_URL", None)
    with brittco.app.app_context():
        assert brittco.dwolla_onboarding_required() is False
    os.environ["PLATFORM_MODE"] = "whitelabel"
    with brittco.app.app_context():
        assert brittco.dwolla_onboarding_required() is True
    os.environ["DWOLLA_MASTER_CUSTOMER_URL"] = "https://api-sandbox.dwolla.com/customers/abc"
    with brittco.app.app_context():
        assert brittco.dwolla_onboarding_required() is False
    os.environ.pop("DWOLLA_MASTER_CUSTOMER_URL", None)
    os.environ["PLATFORM_MODE"] = "brittco_existing"


def test_webhook_posts_payment_once():
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT id FROM borrowers LIMIT 1").fetchone()
        cur = brittco.db().execute(
            """INSERT INTO loans
               (borrower_id, loan_number, current_balance, original_principal, rate, status)
               VALUES (?,?,?,?,?,?)""",
            (borrower["id"], "DW-TEST", 50000, 50000, 12, "Active"),
        )
        lid = cur.lastrowid
        brittco.ensure_dwolla_schema()
        brittco.db().execute(
            """INSERT INTO ach_transfers
               (borrower_id, loan_id, direction, amount, status, vendor, provider, provider_ref, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                borrower["id"],
                lid,
                "Debit borrower (loan payment)",
                25,
                "pending",
                "Dwolla",
                "dwolla",
                "https://api-sandbox.dwolla.com/transfers/abc123",
                "2026-01-01T00:00",
            ),
        )
        brittco.db().commit()

    raw = json.dumps(
        {
            "id": "evt-1",
            "topic": "transfer_completed",
            "_links": {"resource": {"href": "https://api-sandbox.dwolla.com/transfers/abc123"}},
        }
    ).encode()
    sig = hmac.new(b"whsec-test", raw, hashlib.sha256).hexdigest()
    client = brittco.app.test_client()
    bad = client.post("/webhooks/dwolla", data=raw, headers={"X-Request-Signature-SHA-256": "nope"})
    assert bad.status_code == 401
    ok = client.post(
        "/webhooks/dwolla",
        data=raw,
        headers={"X-Request-Signature-SHA-256": sig, "Content-Type": "application/json"},
    )
    assert ok.status_code == 200
    again = client.post(
        "/webhooks/dwolla",
        data=raw,
        headers={"X-Request-Signature-SHA-256": sig, "Content-Type": "application/json"},
    )
    assert again.status_code == 200
    with brittco.app.app_context():
        count = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM payments WHERE loan_id=(SELECT id FROM loans WHERE loan_number='DW-TEST')"
        ).fetchone()["c"]
        assert count == 1
        row = brittco.db().execute(
            "SELECT status, payment_id FROM ach_transfers WHERE provider_ref LIKE '%abc123'"
        ).fetchone()
        assert row["status"] == "processed"
        assert row["payment_id"]


def _sign(raw):
    return hmac.new(b"whsec-test", raw, hashlib.sha256).hexdigest()


def _post_dwolla(payload):
    raw = json.dumps(payload).encode()
    client = brittco.app.test_client()
    return client.post(
        "/webhooks/dwolla",
        data=raw,
        headers={"X-Request-Signature-SHA-256": _sign(raw), "Content-Type": "application/json"},
    )


def _pending_ach(provider_ref, loan_number, amount=1):
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT id FROM borrowers LIMIT 1").fetchone()
        cur = brittco.db().execute(
            """INSERT INTO loans
               (borrower_id, loan_number, current_balance, original_principal, rate, status)
               VALUES (?,?,?,?,?,?)""",
            (borrower["id"], loan_number, 50000, 50000, 12, "Active"),
        )
        lid = cur.lastrowid
        brittco.ensure_dwolla_schema()
        brittco.db().execute(
            """INSERT INTO ach_transfers
               (borrower_id, loan_id, direction, amount, status, vendor, provider, provider_ref, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                borrower["id"],
                lid,
                "Debit borrower (loan payment)",
                amount,
                "pending",
                "Dwolla",
                "dwolla",
                provider_ref,
                "2026-09-29T21:32",
            ),
        )
        brittco.db().commit()
        return lid


def _payment_count(loan_number):
    with brittco.app.app_context():
        return brittco.db().execute(
            "SELECT COUNT(*) AS c FROM payments WHERE loan_id=(SELECT id FROM loans WHERE loan_number=?)",
            (loan_number,),
        ).fetchone()["c"]


def _ach_row(provider_ref):
    with brittco.app.app_context():
        return brittco.db().execute(
            "SELECT status, provider_event, payment_id FROM ach_transfers WHERE provider_ref=?",
            (provider_ref,),
        ).fetchone()


# UI shows provider_ref[-24:], which for this URL is c-f111-acd5-02ab38c54207.
PARENT_URL = "https://api-sandbox.dwolla.com/transfers/12345678-90ac-f111-acd5-02ab38c54207"
CHILD_URL = "https://api-sandbox.dwolla.com/transfers/1352581d-3b6c-ec11-813c-f6ddd36b41b8"


def test_customer_transfer_completed_posts_ledger_once():
    _pending_ach(PARENT_URL, "DW-CUST-OK")
    payload = {
        "id": "evt-cust-ok",
        "topic": "customer_transfer_completed",
        "resourceId": "12345678-90ac-f111-acd5-02ab38c54207",
        "_links": {"resource": {"href": PARENT_URL}},
    }
    with patch("dwolla_client.get_resource") as get_resource:
        ok = _post_dwolla(payload)
        again = _post_dwolla(payload)
        get_resource.assert_not_called()
    assert ok.status_code == 200
    assert again.status_code == 200
    assert _payment_count("DW-CUST-OK") == 1
    row = _ach_row(PARENT_URL)
    assert row["status"] == "processed"
    assert row["provider_event"] == "customer_transfer_completed"
    assert row["payment_id"]


def test_customer_transfer_failed_marks_row_without_ledger():
    ref = "https://api-sandbox.dwolla.com/transfers/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _pending_ach(ref, "DW-CUST-FAIL")
    ok = _post_dwolla(
        {
            "id": "evt-cust-fail",
            "topic": "customer_transfer_failed",
            "resourceId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "_links": {"resource": {"href": ref}},
        }
    )
    assert ok.status_code == 200
    assert _payment_count("DW-CUST-FAIL") == 0
    row = _ach_row(ref)
    assert row["status"] == "failed"
    assert row["provider_event"] == "customer_transfer_failed"
    assert not row["payment_id"]


def test_bank_leg_completed_follows_funding_transfer():
    parent = "https://api-sandbox.dwolla.com/transfers/cd4a2cb3-3a6c-ec11-813c-f6ddd36b41b8"
    _pending_ach(parent, "DW-BANK-LEG")

    def fake_get(url, timeout=30):
        assert url == CHILD_URL
        return (
            {
                "_links": {
                    "funding-transfer": {"href": parent},
                    "self": {"href": CHILD_URL},
                },
                "status": "processed",
            },
            None,
            None,
        )

    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    try:
        with patch("dwolla_client.get_resource", fake_get):
            ok = _post_dwolla(
                {
                    "id": "evt-bank",
                    "topic": "customer_bank_transfer_completed",
                    "resourceId": "1352581d-3b6c-ec11-813c-f6ddd36b41b8",
                    "_links": {"resource": {"href": CHILD_URL}},
                }
            )
            again = _post_dwolla(
                {
                    "id": "evt-bank-2",
                    "topic": "customer_bank_transfer_completed",
                    "resourceId": "1352581d-3b6c-ec11-813c-f6ddd36b41b8",
                    "_links": {"resource": {"href": CHILD_URL}},
                }
            )
    finally:
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)
    assert ok.status_code == 200
    assert again.status_code == 200
    assert _payment_count("DW-BANK-LEG") == 1
    row = _ach_row(parent)
    assert row["status"] == "processed"
    assert row["provider_event"] == "customer_bank_transfer_completed"
    assert row["payment_id"]


def test_bank_leg_failure_keeps_posted_payment():
    parent = "https://api-sandbox.dwolla.com/transfers/cccccccc-dddd-eeee-ffff-000000000001"
    _pending_ach(parent, "DW-NO-UNDO")
    ok = _post_dwolla(
        {
            "id": "evt-ok-first",
            "topic": "customer_transfer_completed",
            "resourceId": "cccccccc-dddd-eeee-ffff-000000000001",
            "_links": {"resource": {"href": parent}},
        }
    )
    assert ok.status_code == 200

    def fake_get(url, timeout=30):
        return (
            {"_links": {"funding-transfer": {"href": parent}}, "status": "failed"},
            None,
            None,
        )

    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    try:
        with patch("dwolla_client.get_resource", fake_get):
            failed = _post_dwolla(
                {
                    "id": "evt-bank-fail",
                    "topic": "customer_bank_transfer_failed",
                    "resourceId": "1352581d-3b6c-ec11-813c-f6ddd36b41b8",
                    "_links": {"resource": {"href": CHILD_URL}},
                }
            )
    finally:
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)
    assert failed.status_code == 200
    assert _payment_count("DW-NO-UNDO") == 1
    row = _ach_row(parent)
    assert row["status"] == "processed"
    assert row["provider_event"] == "customer_bank_transfer_failed"
    assert row["payment_id"]


def test_reconcile_posts_processed_transfer_still_pending_locally():
    ref = "https://api-sandbox.dwolla.com/transfers/bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    _pending_ach(ref, "DW-RECON", amount=1)

    def fake_get(url, timeout=30):
        if url == ref:
            return ({"id": url.rsplit("/", 1)[-1], "status": "processed"}, None, None)
        return ({"status": "pending"}, None, None)

    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    try:
        with patch("dwolla_client.get_resource", fake_get):
            with brittco.app.app_context():
                changed = brittco.reconcile_pending_dwolla_transfers(loan_id=None)
                again = brittco.reconcile_pending_dwolla_transfers()
    finally:
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)
    assert changed == 1
    assert again == 0
    assert _payment_count("DW-RECON") == 1
    row = _ach_row(ref)
    assert row["status"] == "processed"
    assert row["provider_event"] == "transfer_completed"
    assert row["payment_id"]


def test_staff_pages_hide_modal_for_existing_platform():
    os.environ["PLATFORM_MODE"] = "brittco_existing"
    client = brittco.app.test_client()
    client.post(
        "/login",
        data={"email": "admin@brittcocapital.com", "password": "brittco"},
        follow_redirects=True,
    )
    tools = client.get("/tools")
    assert tools.status_code == 200
    page = tools.get_data(as_text=True)
    assert "Create two test investors" in page
    assert "Set up this company for ACH" not in page
    blocked = client.get("/dwolla/onboarding", follow_redirects=True)
    assert "Enter company information" not in blocked.get_data(as_text=True) or "Company onboarding is off" in blocked.get_data(as_text=True)

    os.environ["PLATFORM_MODE"] = "whitelabel"
    home = client.get("/")
    html = home.get_data(as_text=True)
    assert home.status_code == 200
    assert "Set up this company for ACH" in html
    setup = client.get("/dwolla/onboarding")
    setup_html = setup.get_data(as_text=True)
    assert setup.status_code == 200
    assert "Company information" in setup_html
    assert "Verify the company bank" in setup_html
    assert "Collect first ACH" in setup_html or "First ACH" in setup_html
    os.environ["PLATFORM_MODE"] = "brittco_existing"


def test_ach_page_and_one_time_investor_passwords():
    os.environ["PLATFORM_MODE"] = "brittco_existing"
    os.environ.pop("DWOLLA_KEY", None)
    os.environ.pop("DWOLLA_SECRET", None)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ.pop("PAYMENTS_PROVIDER", None)
    client = brittco.app.test_client()
    client.post(
        "/login",
        data={"email": "admin@brittcocapital.com", "password": "brittco"},
        follow_redirects=True,
    )
    ach = client.get("/ach")
    assert ach.status_code == 200
    assert "No live ACH keys" in ach.get_data(as_text=True)
    assert "Record borrower collection" in ach.get_data(as_text=True)
    with brittco.app.app_context():
        loan = brittco.db().execute("SELECT id FROM loans ORDER BY id LIMIT 1").fetchone()
    if loan:
        detail = client.get("/loans/%s" % loan["id"])
        assert detail.status_code == 200
        assert "Recurring ACH plan" in detail.get_data(as_text=True)
    created = client.post("/tools/test-investors", follow_redirects=True)
    body = created.get_data(as_text=True)
    assert created.status_code == 200
    assert "sandbox.investor.a@example.com" in body
    assert "sandbox.investor.b@example.com" in body
    assert "One-time password" in body
    again = client.get("/tools")
    assert "sandbox.investor.a@example.com" not in again.get_data(as_text=True)
