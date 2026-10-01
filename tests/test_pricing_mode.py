"""Loan pricing: interest rate versus a flat fee."""

import os
import tempfile
import uuid

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="brittco-pricing-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PLATFORM_MODE", "brittco_existing")

import app as brittco


def _client():
    client = brittco.app.test_client()
    resp = client.post(
        "/login",
        data={"email": "admin@brittcocapital.com", "password": "brittco"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    return client


def _borrower():
    with brittco.app.app_context():
        email = f"pricing-{uuid.uuid4().hex}@example.com"
        brittco.db().execute(
            """INSERT INTO borrowers
               (name, entity_type, entity_name, email, phone, credit_score, password, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Fee Tester", "LLC", "Fee Tester LLC", email, "555", 700, "borrower", ""),
        )
        brittco.db().commit()
        return brittco.db().execute(
            "SELECT id FROM borrowers WHERE email=?", (email,)
        ).fetchone()["id"]


def _loan_form(bid, **extra):
    data = {
        "borrower_id": str(bid),
        "deal_id": "",
        "loan_number": extra.pop("loan_number"),
        "loan_type": "Fix and Flip",
        "term_kind": "Fixed",
        "property_address": "10 Fee Street",
        "purchase_price": "100000",
        "loan_amount": "100000",
        "total_loan_amount": "100000",
        "rehab_cost": "0",
        "points": "",
        "pricing_mode": "rate",
        "rate": "10",
        "flat_fee": "",
        "base_term": "3",
        "start_date": "2026-01-15",
        "payment_type": "Balloon",
        "payment_amount": "",
        "payment_frequency": "Interest due at maturity",
        "status": "Current",
        "notes": "",
    }
    data.update(extra)
    return data


def _loan_row(number):
    with brittco.app.app_context():
        return brittco.db().execute(
            "SELECT * FROM loans WHERE loan_number=?", (number,)
        ).fetchone()


def test_new_loan_form_offers_rate_or_flat_fee():
    client = _client()
    resp = client.get("/loans/new")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert 'name="pricing_mode"' in page
    assert "Interest rate %" in page
    assert "Flat fee" in page
    assert 'name="flat_fee"' in page
    assert 'name="rate"' in page


def test_rate_path_unchanged():
    client = _client()
    bid = _borrower()
    resp = client.post(
        "/loans/new",
        data=_loan_form(bid, loan_number="BC-RATE-1", pricing_mode="rate", rate="10", flat_fee="9999"),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    row = _loan_row("BC-RATE-1")
    assert row is not None
    assert row["pricing_mode"] == "rate"
    assert row["rate"] == 10
    assert row["flat_fee"] in (None, 0)
    assert row["original_principal"] == 100000
    with brittco.app.app_context():
        assert brittco.payoff_amount(row) == 110000
        defaults = brittco.payoff_defaults(row)
    assert defaults["pricing_mode"] == "rate"
    assert defaults["rate"] == 10
    assert "Base rate" in page
    assert "10" in page


def test_flat_fee_saves_fee_and_clears_rate():
    client = _client()
    bid = _borrower()
    resp = client.post(
        "/loans/new",
        data=_loan_form(
            bid,
            loan_number="BC-FEE-1",
            pricing_mode="flat_fee",
            rate="12",
            flat_fee="2500",
        ),
        follow_redirects=False,
    )
    assert resp.status_code == 302
    row = _loan_row("BC-FEE-1")
    assert row["pricing_mode"] == "flat_fee"
    assert row["rate"] in (None, 0, "")
    assert row["flat_fee"] == 2500
    with brittco.app.app_context():
        assert brittco.payoff_amount(row) == 102500
        defaults = brittco.payoff_defaults(row)
    assert defaults["pricing_mode"] == "flat_fee"
    assert defaults["rate"] == 0
    assert defaults["per_diem"] == 0
    assert defaults["interest_fee"] == 2500
    assert defaults["flat_fee"] == 2500
    detail = client.get(f"/loans/{row['id']}")
    page = detail.get_data(as_text=True)
    assert "Flat fee" in page
    assert "2,500.00" in page
    assert "Fee-based" in page
    assert "Base rate" not in page
    assert "12%" not in page


def test_flat_fee_requires_amount():
    client = _client()
    bid = _borrower()
    before = _loan_row("BC-FEE-BLANK")
    resp = client.post(
        "/loans/new",
        data=_loan_form(bid, loan_number="BC-FEE-BLANK", pricing_mode="flat_fee", rate="9", flat_fee=""),
    )
    assert resp.status_code == 200
    assert "Enter a flat fee amount." in resp.get_data(as_text=True)
    assert before is None
    assert _loan_row("BC-FEE-BLANK") is None


def test_edit_switches_between_rate_and_flat_fee():
    client = _client()
    bid = _borrower()
    client.post(
        "/loans/new",
        data=_loan_form(bid, loan_number="BC-SWITCH-1", pricing_mode="rate", rate="11"),
        follow_redirects=True,
    )
    row = _loan_row("BC-SWITCH-1")
    resp = client.post(
        f"/loans/{row['id']}/edit",
        data=_loan_form(
            bid,
            loan_number="BC-SWITCH-1",
            pricing_mode="flat_fee",
            rate="11",
            flat_fee="1800",
        ),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    row = _loan_row("BC-SWITCH-1")
    assert row["pricing_mode"] == "flat_fee"
    assert row["flat_fee"] == 1800
    assert row["rate"] in (None, 0, "")
    page = resp.get_data(as_text=True)
    assert "Flat fee" in page
    assert "1,800.00" in page
    client.post(
        f"/loans/{row['id']}/edit",
        data=_loan_form(bid, loan_number="BC-SWITCH-1", pricing_mode="rate", rate="11", flat_fee=""),
        follow_redirects=True,
    )
    row = _loan_row("BC-SWITCH-1")
    assert row["pricing_mode"] == "rate"
    assert row["rate"] == 11
    assert row["flat_fee"] in (None, 0)


def test_flat_fee_flows_into_application_and_closing_defaults():
    client = _client()
    bid = _borrower()
    with brittco.app.app_context():
        brittco.db().execute(
            """INSERT INTO deals
               (borrower_id, loan_type, address, purchase_price, loan_amount, rate, points,
                term_months, status, exit_strategy, notes, created_at, acked)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                bid,
                "Fix and Flip",
                "10 Fee Street",
                100000,
                100000,
                10,
                2,
                4,
                "Application",
                "Sale",
                "{}",
                "2026-01-01",
                0,
            ),
        )
        brittco.db().commit()
        deal = brittco.db().execute(
            "SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC LIMIT 1", (bid,)
        ).fetchone()
        did = deal["id"]
    client.post(
        "/loans/new",
        data=_loan_form(
            bid,
            loan_number="BC-FEE-APP",
            deal_id=str(did),
            pricing_mode="flat_fee",
            rate="10",
            flat_fee="4000",
        ),
        follow_redirects=True,
    )
    with brittco.app.app_context():
        deal = brittco.db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
        borrower = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
        assert deal["rate"] in (None, 0, "")
        prefill = brittco.form_prefill(borrower, deal)
        assert prefill["pricing_mode"] == "flat_fee"
        assert prefill["profit_fee"] == "4,000.00"
        packet = brittco.apply_packet_data(
            borrower,
            deal,
            {"loan_amount": 100000, "points": 2, "term_months": 4, "start_date": "2026-02-01"},
        )
        assert packet["profit_fee"] == "4,000.00"
        assert packet["pricing_mode"] == "flat_fee"
    detail = client.get(f"/deals/{did}")
    page = detail.get_data(as_text=True)
    assert "Flat fee" in page
    assert "4,000.00" in page
    assert "No interest rate" in page
