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


def test_customer_transfer_created_notes_row_without_ledger():
    ref = "https://api-sandbox.dwolla.com/transfers/9b77f03d-4dbc-f111-acd5-02ab38c54207"
    _pending_ach(ref, "DW-CREATED-ONLY")
    ok = _post_dwolla(
        {
            "id": "evt-created",
            "topic": "customer_transfer_created",
            "resourceId": "9b77f03d-4dbc-f111-acd5-02ab38c54207",
            "_links": {"resource": {"href": ref}},
        }
    )
    assert ok.status_code == 200
    assert _payment_count("DW-CREATED-ONLY") == 0
    row = _ach_row(ref)
    assert row["status"] == "pending"
    assert row["provider_event"] == "customer_transfer_created"
    assert not row["payment_id"]


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


def test_sandbox_simulate_posts_empty_body_and_refuses_production():
    os.environ["DWOLLA_ENV"] = "sandbox"
    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    seen = {}

    def fake_api(method, path, body=None, timeout=30):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        return {"total": 2}, None, None

    try:
        with patch("dwolla_client.api", fake_api):
            total, err = dwolla_client.simulate_sandbox_bank_transfers()
        assert err is None, err
        assert total == 2
        assert seen["method"] == "POST"
        assert seen["path"] == "/sandbox-simulations"
        assert seen["body"] == {}
        os.environ["DWOLLA_ENV"] = "production"
        with patch("urllib.request.urlopen") as urlopen:
            total, err = dwolla_client.simulate_sandbox_bank_transfers()
        urlopen.assert_not_called()
        assert total is None
        assert "sandbox" in (err or "").lower()
    finally:
        os.environ["DWOLLA_ENV"] = "sandbox"
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)


def test_tools_sandbox_simulate_button():
    os.environ["DWOLLA_ENV"] = "sandbox"
    os.environ["PLATFORM_MODE"] = "brittco_existing"
    os.environ.pop("DWOLLA_KEY", None)
    os.environ.pop("DWOLLA_SECRET", None)
    client = brittco.app.test_client()
    client.post(
        "/login",
        data={"email": "admin@brittcocapital.com", "password": "brittco"},
        follow_redirects=True,
    )
    tools = client.get("/tools")
    page = tools.get_data(as_text=True)
    assert "Sandbox bank transfers" in page
    assert "sandbox-simulations" in page
    assert "Process sandbox bank transfers" not in page
    os.environ["DWOLLA_KEY"] = "key"
    os.environ["DWOLLA_SECRET"] = "secret"
    try:
        ready = client.get("/tools")
        assert "Process sandbox bank transfers" in ready.get_data(as_text=True)
        with patch("dwolla_client.simulate_sandbox_bank_transfers", return_value=(4, None)) as sim:
            done = client.post("/tools/dwolla-sandbox-simulate", follow_redirects=True)
        sim.assert_called_once()
        assert "Dwolla reported 4" in done.get_data(as_text=True)
        os.environ["DWOLLA_ENV"] = "production"
        with patch("urllib.request.urlopen") as urlopen:
            blocked = client.post("/tools/dwolla-sandbox-simulate", follow_redirects=True)
        urlopen.assert_not_called()
        assert "only available when DWOLLA_ENV=sandbox" in blocked.get_data(as_text=True)
        assert "Process sandbox bank transfers" not in blocked.get_data(as_text=True)
    finally:
        os.environ["DWOLLA_ENV"] = "sandbox"
        os.environ.pop("DWOLLA_KEY", None)
        os.environ.pop("DWOLLA_SECRET", None)


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
        page = detail.get_data(as_text=True)
        assert "Collect ACH payment" in page
        assert 'id="collect-ach"' in page
        tag_at = page.find('id="collect-ach"')
        tag = page[page.rfind("<details", 0, tag_at): page.find(">", tag_at)]
        assert "open" not in tag
    created = client.post("/tools/test-investors", follow_redirects=True)
    body = created.get_data(as_text=True)
    assert created.status_code == 200
    assert "sandbox.investor.a@example.com" in body
    assert "sandbox.investor.b@example.com" in body
    assert "One-time password" in body
    again = client.get("/tools")
    assert "sandbox.investor.a@example.com" not in again.get_data(as_text=True)


def _staff_client():
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
    return client


def _loan_ready_for_ach(number):
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT id FROM borrowers ORDER BY id LIMIT 1").fetchone()
        brittco.db().execute(
            """UPDATE borrowers
               SET ach_authorized=1, bank_name=?, bank_routing=?, bank_account=?
               WHERE id=?""",
            ("Test Bank", "110000000", "000123456789", borrower["id"]),
        )
        cur = brittco.db().execute(
            """INSERT INTO loans
               (borrower_id, loan_number, current_balance, original_principal, rate, status, maturity_date, payment_amount)
               VALUES (?,?,?,?,?,?,?,?)""",
            (borrower["id"], number, 10000, 10000, 12, "Active", "2027-06-01", 125),
        )
        brittco.db().commit()
        return cur.lastrowid


def test_schedule_count_and_stop_stay_in_sync():
    assert "customer_transfer_completed" in brittco.DWOLLA_SUCCESS_TOPICS
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-03-01", "", 1, 6, "count")
    assert err is None
    assert day == 1
    assert count == 6
    assert end == "2026-08-01"
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-04-01", "2026-08-01", 1, 6, "count")
    assert end == "2026-09-01"
    assert count == 6
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-03-01", "", 15, 3, "count")
    assert end == "2026-05-15"
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-01-15", "2026-04-01", 1, "", "end")
    assert err is None
    assert count == 3
    assert end == "2026-04-01"
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-02-01", "2026-04-01", 1, "9", "end")
    assert count == 3
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-03-01", "2026-04-01", 1, "9", "end")
    assert count == 2
    start, end, day, count, err = brittco.resolve_ach_schedule("2026-01-20", "2026-01-31", 15, "", "end")
    assert err
    assert brittco.ach_count_through("2026-01-01", "2026-01-20", 15) == 1


def test_recurring_plan_saves_synced_count_and_keeps_active():
    lid = _loan_ready_for_ach("ACH-RECUR-1")
    client = _staff_client()
    saved = client.post(
        "/loans/%s/ach-plan" % lid,
        data={
            "kind": "recurring",
            "amount": "100.00",
            "day_of_month": "1",
            "start_on": "2026-03-01",
            "end_on": "",
            "payment_count": "6",
            "schedule_driver": "count",
            "note": "six pulls",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    page = saved.get_data(as_text=True)
    assert "Active recurring plan" in page
    assert "6" in page
    with brittco.app.app_context():
        plan = brittco.db().execute(
            "SELECT * FROM loan_ach_plans WHERE loan_id=? AND status='Active'", (lid,)
        ).fetchone()
        assert plan["kind"] == "recurring"
        assert plan["payment_count"] == 6
        assert plan["start_on"] == "2026-03-01"
        assert plan["end_on"] == "2026-08-01"
        assert plan["day_of_month"] == 1
    moved = client.post(
        "/loans/%s/ach-plan" % lid,
        data={
            "kind": "recurring",
            "amount": "100.00",
            "day_of_month": "1",
            "start_on": "2026-01-15",
            "end_on": "2026-04-10",
            "payment_count": "99",
            "schedule_driver": "end",
            "note": "through april",
        },
        follow_redirects=True,
    )
    assert moved.status_code == 200
    with brittco.app.app_context():
        active = brittco.db().execute(
            "SELECT * FROM loan_ach_plans WHERE loan_id=? AND status='Active'", (lid,)
        ).fetchall()
        assert len(active) == 1
        assert active[0]["payment_count"] == 3
        assert active[0]["end_on"] == "2026-04-10"
        assert active[0]["start_on"] == "2026-01-15"
        assert active[0]["kind"] == "recurring"


def test_one_time_collects_and_does_not_leave_active_plan():
    lid = _loan_ready_for_ach("ACH-ONCE-1")
    with brittco.app.app_context():
        brittco.ensure_ach_plans()
        brittco.db().execute(
            """INSERT INTO loan_ach_plans
               (loan_id, amount, day_of_month, start_on, end_on, status, created_at, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (lid, 80, 1, "2026-01-01", "2026-12-01", "Active", "2026-01-01T00:00", "legacy open plan"),
        )
        brittco.db().commit()
        before = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM payments WHERE loan_id=?", (lid,)
        ).fetchone()["c"]
    client = _staff_client()
    detail = client.get("/loans/%s" % lid)
    html = detail.get_data(as_text=True)
    assert "Active recurring plan" in html
    assert "Collect ACH payment" in html
    tag_at = html.find('id="collect-ach"')
    tag = html[html.rfind("<details", 0, tag_at): html.find(">", tag_at)]
    assert "open" not in tag
    assert "One-time payment" in html
    assert "Number of payments" in html
    with patch.object(brittco, "collect_ach_once", return_value=(False, "bank down")):
        blocked = client.post(
            "/loans/%s/ach-plan" % lid,
            data={"kind": "single", "amount": "40.00"},
            follow_redirects=True,
        )
    assert "did not send" in blocked.get_data(as_text=True)
    with brittco.app.app_context():
        still = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM loan_ach_plans WHERE loan_id=? AND status='Active'",
            (lid,),
        ).fetchone()["c"]
        assert still == 1
    collected = client.post(
        "/loans/%s/ach-plan" % lid,
        data={"kind": "single", "amount": "40.00", "note": "single pull"},
        follow_redirects=True,
    )
    body = collected.get_data(as_text=True)
    assert "No open recurring plan" in body or "No recurring plan was left open" in body
    assert "Active recurring plan" not in body
    with brittco.app.app_context():
        active = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM loan_ach_plans WHERE loan_id=? AND status='Active'",
            (lid,),
        ).fetchone()["c"]
        assert active == 0
        latest = brittco.db().execute(
            "SELECT * FROM loan_ach_plans WHERE loan_id=? ORDER BY id DESC LIMIT 1",
            (lid,),
        ).fetchone()
        assert latest["kind"] == "single"
        assert latest["status"] == "Collected"
        assert latest["payment_count"] == 1
        assert latest["start_on"] == latest["end_on"]
        pays = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM payments WHERE loan_id=?", (lid,)
        ).fetchone()["c"]
        assert pays == before + 1


def test_approved_one_time_closes_after_collect():
    lid = _loan_ready_for_ach("ACH-ONCE-2")
    with brittco.app.app_context():
        brittco.ensure_ach_plans()
        brittco.db().execute(
            """INSERT INTO loan_ach_plans
               (loan_id, amount, day_of_month, start_on, end_on, status, created_at, note, kind, payment_count)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (lid, 55, 3, "2026-05-03", "2026-05-03", "Pending", "2026-05-01T00:00", "Signed ACH auth · one-time", "single", 1),
        )
        brittco.db().commit()
    client = _staff_client()
    client.post("/loans/%s/ach-plan/approve" % lid, follow_redirects=True)
    with brittco.app.app_context():
        plan = brittco.db().execute(
            "SELECT status, kind, start_on, end_on, payment_count FROM loan_ach_plans WHERE loan_id=?",
            (lid,),
        ).fetchone()
        assert plan["status"] == "Active"
        assert plan["kind"] == "single"
        assert plan["start_on"] == plan["end_on"]
        assert plan["payment_count"] == 1
    client.post("/loans/%s/ach-collect" % lid, follow_redirects=True)
    with brittco.app.app_context():
        plan = brittco.db().execute(
            "SELECT status FROM loan_ach_plans WHERE loan_id=?", (lid,)
        ).fetchone()
        assert plan["status"] == "Collected"
        active = brittco.db().execute(
            "SELECT COUNT(*) AS c FROM loan_ach_plans WHERE loan_id=? AND status='Active'",
            (lid,),
        ).fetchone()["c"]
        assert active == 0


def test_dwolla_terms_urls_match_current_legal_pages():
    assert dwolla_client.DWOLLA_TOS_URL == "https://www.dwolla.com/legal/dwolla-account-terms-of-service"
    assert dwolla_client.DWOLLA_PRIVACY_URL == "https://www.dwolla.com/legal/privacy"
    assert dwolla_client.DWOLLA_TERMS_VERSION


def test_ach_status_labels():
    assert brittco.ach_status_bucket("completed") == "processed"
    assert brittco.ach_status_bucket("pending") == "pending"
    assert brittco.ach_status_bucket("failed") == "failed"
    assert brittco.ach_status_bucket("cancelled") == "cancelled"
    assert brittco.ach_status_bucket("Recorded — collect at bank") == "Recorded — collect at bank"


def test_ach_auth_requires_and_records_dwolla_terms():
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT * FROM borrowers ORDER BY id LIMIT 1").fetchone()
        cur = brittco.db().execute(
            """INSERT INTO loans (borrower_id, loan_number, property_address, status)
               VALUES (?,?,?,?)""",
            (borrower["id"], "DW-TOS", "1 Main", "Active"),
        )
        lid = cur.lastrowid
        token = "tos-token-ach"
        payload = {
            "kind": "single",
            "amount": "25.00",
            "start_on": "2026-06-01",
            "bank_name": "Test Bank",
            "bank_routing": "110000000",
            "bank_account": "000999",
            "bank_account_type": "Checking",
            "loan_number": "DW-TOS",
        }
        brittco.packet_add_loan_id()
        brittco.db().execute(
            """INSERT INTO form_packets
               (token, form_key, borrower_id, loan_id, status, payload, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (token, "ach_auth", borrower["id"], lid, "Sent", json.dumps(payload), "2026-06-01T00:00"),
        )
        brittco.db().commit()
        bid = borrower["id"]
    client = brittco.app.test_client()
    page = client.get("/ach-auth/" + token)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'name="dwolla_terms"' in html
    assert "Agree and Continue" in html
    assert dwolla_client.DWOLLA_TOS_URL in html
    assert dwolla_client.DWOLLA_PRIVACY_URL in html
    assert "processed by Dwolla" in html
    missing = client.post(
        "/ach-auth/" + token,
        data={
            "kind": "single",
            "amount": "25.00",
            "start_on": "2026-06-01",
            "bank_name": "Test Bank",
            "bank_routing": "110000000",
            "bank_account": "000999",
            "bank_account_type": "Checking",
            "signed_name": "Ada Lovelace",
            "signature": "data:image/png;base64,abc",
        },
    )
    assert "Terms of Service" in missing.get_data(as_text=True)
    done = client.post(
        "/ach-auth/" + token,
        data={
            "kind": "single",
            "amount": "25.00",
            "start_on": "2026-06-01",
            "bank_name": "Test Bank",
            "bank_routing": "110000000",
            "bank_account": "000999",
            "bank_account_type": "Checking",
            "signed_name": "Ada Lovelace",
            "signature": "data:image/png;base64,abc",
            "dwolla_terms": "1",
        },
    )
    assert "on file" in done.get_data(as_text=True).lower()
    with brittco.app.app_context():
        row = brittco.latest_dwolla_terms("borrower", bid)
        assert row["accepted_by"] == "Ada Lovelace"
        assert row["accepted_role"] == "borrower"
        assert row["tos_url"] == dwolla_client.DWOLLA_TOS_URL
        assert row["privacy_url"] == dwolla_client.DWOLLA_PRIVACY_URL
        assert row["terms_version"] == dwolla_client.DWOLLA_TERMS_VERSION
        assert row["context"] == "ach_auth"
        assert row["accepted_at"]


def test_staff_bank_link_records_who_accepted():
    client = _staff_client()
    with brittco.app.app_context():
        borrower = brittco.db().execute(
            "SELECT id, bank_routing FROM borrowers ORDER BY id LIMIT 1"
        ).fetchone()
        bid = borrower["id"]
        brittco.db().execute("DELETE FROM dwolla_terms_acceptances WHERE party_id=?", (bid,))
        brittco.db().commit()
    blocked = client.post(
        "/borrowers/%s/ach" % bid,
        data={
            "bank_name": "Operating",
            "bank_account_type": "Checking",
            "bank_routing": "222222226",
            "bank_account": "999000111",
            "ach_authorized": "1",
        },
        follow_redirects=True,
    )
    assert "Terms of Service" in blocked.get_data(as_text=True)
    saved = client.post(
        "/borrowers/%s/ach" % bid,
        data={
            "bank_name": "Operating",
            "bank_account_type": "Checking",
            "bank_routing": "222222226",
            "bank_account": "999000111",
            "ach_authorized": "1",
            "dwolla_terms": "1",
        },
        follow_redirects=True,
    )
    page = saved.get_data(as_text=True)
    assert "Agree and Continue" in page
    assert dwolla_client.DWOLLA_TOS_URL in page
    with brittco.app.app_context():
        row = brittco.latest_dwolla_terms("borrower", bid)
        assert row["accepted_role"] == "staff"
        assert row["accepted_by"]
        assert row["context"] == "borrower_bank"
        stored = brittco.db().execute("SELECT bank_routing FROM borrowers WHERE id=?", (bid,)).fetchone()
        assert stored["bank_routing"] == "222222226"


def test_dwolla_customer_blocked_until_terms_accepted():
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT * FROM borrowers ORDER BY id LIMIT 1").fetchone()
        brittco.db().execute("DELETE FROM dwolla_terms_acceptances WHERE party_id=?", (borrower["id"],))
        brittco.db().execute(
            """UPDATE borrowers
               SET ach_authorized=1, bank_routing=?, bank_account=?, dwolla_customer_url=NULL
               WHERE id=?""",
            ("110000000", "000123456789", borrower["id"]),
        )
        brittco.db().commit()
        fresh = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (borrower["id"],)).fetchone()
        _fs, err = brittco.ensure_borrower_dwolla_source(fresh)
        assert "Terms of Service" in (err or "")
        brittco.record_dwolla_terms_acceptance("borrower", fresh["id"], "Ada Lovelace", "borrower", "ach_auth")
        brittco.db().commit()
        fresh = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (borrower["id"],)).fetchone()
        _fs, err = brittco.ensure_borrower_dwolla_source(fresh)
        assert "Terms of Service" not in (err or "")
        assert "DWOLLA_KEY" in (err or "")


def test_borrower_and_investor_portals_list_ach_history():
    with brittco.app.app_context():
        borrower = brittco.db().execute("SELECT id FROM borrowers ORDER BY id LIMIT 1").fetchone()
        brittco.db().execute(
            "UPDATE borrowers SET email=?, password=? WHERE id=?",
            ("tos.borrower@example.com", "borrower", borrower["id"]),
        )
        investor = brittco.db().execute("SELECT id FROM investors ORDER BY id LIMIT 1").fetchone()
        brittco.db().execute(
            "UPDATE investors SET email=?, password=? WHERE id=?",
            ("tos.lender@example.com", "investor", investor["id"]),
        )
        cur = brittco.db().execute(
            """INSERT INTO loans (borrower_id, loan_number, status) VALUES (?,?,?)""",
            (borrower["id"], "DW-HIST", "Active"),
        )
        lid = cur.lastrowid
        brittco.db().execute(
            "INSERT INTO participations (investor_id, loan_id, amount) VALUES (?,?,?)",
            (investor["id"], lid, 1000),
        )
        brittco.ensure_dwolla_schema()
        brittco.db().execute(
            """INSERT INTO ach_transfers
               (borrower_id, loan_id, investor_id, direction, amount, status, notes, created_at, vendor)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                borrower["id"],
                lid,
                investor["id"],
                "Debit borrower (loan payment)",
                42.5,
                "pending",
                "June interest",
                "2026-06-02T09:00",
                "Dwolla",
            ),
        )
        brittco.db().commit()
    borrower_client = brittco.app.test_client()
    borrower_client.post(
        "/portal/login",
        data={"email": "tos.borrower@example.com", "password": "borrower"},
        follow_redirects=True,
    )
    home = borrower_client.get("/portal")
    body = home.get_data(as_text=True)
    assert "ACH payment history" in body
    assert "June interest" in body
    assert "pending" in body
    assert "42.50" in body
    lender = brittco.app.test_client()
    lender.post(
        "/investor/login",
        data={"email": "tos.lender@example.com", "password": "investor"},
        follow_redirects=True,
    )
    portal = lender.get("/investor")
    text = portal.get_data(as_text=True)
    assert portal.status_code == 200
    assert "ACH payment history" in text
    assert "June interest" in text
    assert "pending" in text


def test_setup_page_points_at_production_disclosures():
    client = _staff_client()
    page = client.get("/help/dwolla-setup")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Production review" in html
    assert "Terms of Service" in html
    tools = client.get("/tools")
    assert "dwolla-setup" in tools.get_data(as_text=True)
