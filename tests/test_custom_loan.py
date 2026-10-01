"""Custom Loan is selectable and does not copy deal or product defaults."""

import os
import re
import tempfile
import uuid

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="brittco-custom-loan-"))
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
        email = f"custom-{uuid.uuid4().hex}@example.com"
        brittco.db().execute(
            """INSERT INTO borrowers
               (name, entity_type, entity_name, email, phone, credit_score, password, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Custom Tester", "LLC", "Custom Tester LLC", email, "555", 700, "borrower", ""),
        )
        brittco.db().commit()
        return brittco.db().execute(
            "SELECT id FROM borrowers WHERE email=?", (email,)
        ).fetchone()["id"]


def _deal(bid, loan_type, **overrides):
    fields = {
        "borrower_id": bid,
        "loan_type": loan_type,
        "address": "99 Deal Lane",
        "purchase_price": 200000,
        "loan_amount": 180000,
        "rate": 15,
        "points": 3,
        "term_months": 12,
        "status": "Approved",
        "exit_strategy": "Sale",
        "notes": "",
        "created_at": "2026-01-01",
        "acked": 1,
    }
    fields.update(overrides)
    with brittco.app.app_context():
        brittco.db().execute(
            """INSERT INTO deals
               (borrower_id, loan_type, address, purchase_price, loan_amount, rate, points,
                term_months, status, exit_strategy, notes, created_at, acked)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fields["borrower_id"],
                fields["loan_type"],
                fields["address"],
                fields["purchase_price"],
                fields["loan_amount"],
                fields["rate"],
                fields["points"],
                fields["term_months"],
                fields["status"],
                fields["exit_strategy"],
                fields["notes"],
                fields["created_at"],
                fields["acked"],
            ),
        )
        brittco.db().commit()
        return brittco.db().execute(
            "SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC LIMIT 1", (bid,)
        ).fetchone()["id"]


def _input_value(page, element_id):
    match = re.search(
        rf'id="{element_id}"[^>]*value="([^"]*)"',
        page,
    )
    if not match:
        match = re.search(
            rf'name="{element_id}"[^>]*value="([^"]*)"',
            page,
        )
    assert match, element_id
    return match.group(1)


def test_custom_loan_is_selectable_on_staff_forms():
    client = _client()
    bid = _borrower()
    new_loan = client.get("/loans/new")
    assert new_loan.status_code == 200
    page = new_loan.get_data(as_text=True)
    assert "Custom Loan" in page
    assert "Fix and Flip" in page
    assert "Transactional Loan" in page
    assert "Gap Loan" in page
    assert ">Other<" in page
    assert 'rate.value = "15"' in page
    assert page.index("Gap Loan") < page.index("Custom Loan") < page.index(">Other<")

    deal_form = client.get("/deals/new")
    assert deal_form.status_code == 200
    deal_page = deal_form.get_data(as_text=True)
    assert 'value="Custom Loan"' in deal_page
    assert 'rate.value = "15"' in deal_page
    assert "blankCustom" in deal_page

    borrower = client.get(f"/borrowers/{bid}")
    assert borrower.status_code == 200
    assert f"/loans/new?borrower_id={bid}" in borrower.get_data(as_text=True)


def test_custom_create_from_deal_does_not_prefill():
    client = _client()
    bid = _borrower()
    did = _deal(bid, "Custom Loan")
    resp = client.post(f"/deals/{did}/create-loan", follow_redirects=True)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert 'value="Custom Loan" selected' in page
    assert f'value="{bid}" selected' in page
    assert f'value="{did}" selected' in page
    assert _input_value(page, "property_address") == ""
    assert _input_value(page, "loan_amount") == ""
    assert _input_value(page, "rate") == ""
    assert _input_value(page, "points") == ""
    assert _input_value(page, "base_term") == ""
    assert "99 Deal Lane" in page
    with brittco.app.app_context():
        assert brittco.loan_fields_from_deal(
            brittco.db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
        ) is None
        assert (
            brittco.db().execute("SELECT id FROM loans WHERE deal_id=?", (did,)).fetchone()
            is None
        )
        deal = brittco.db().execute("SELECT status FROM deals WHERE id=?", (did,)).fetchone()
        assert deal["status"] == "Approved"

    saved = client.post(
        "/loans/new",
        data={
            "borrower_id": str(bid),
            "deal_id": str(did),
            "loan_number": "BC-CUSTOM-1",
            "loan_type": "Custom Loan",
            "term_kind": "Fixed",
            "property_address": "Staff typed address",
            "purchase_price": "",
            "loan_amount": "50000",
            "total_loan_amount": "50000",
            "rehab_cost": "",
            "points": "",
            "pricing_mode": "rate",
            "rate": "9",
            "flat_fee": "",
            "base_term": "6",
            "start_date": "2026-03-01",
            "payment_type": "Interest only",
            "payment_amount": "375",
            "payment_frequency": "Monthly",
            "status": "Current",
            "notes": "typed by staff",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    detail = saved.get_data(as_text=True)
    assert "Custom Loan" in detail
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM loans WHERE loan_number=?", ("BC-CUSTOM-1",)
        ).fetchone()
    assert row["loan_type"] == "Custom Loan"
    assert row["property_address"] == "Staff typed address"
    assert row["original_principal"] == 50000
    assert row["rate"] == 9
    assert row["points"] in (None, 0, "")
    assert row["base_term_months"] == 6
    assert row["purchase_price"] in (None, "")
    assert row["pricing_mode"] == "rate"
    assert row["notes"] == "typed by staff"
    assert "99 Deal Lane" not in (row["property_address"] or "")
    assert row["deal_id"] == did
    with brittco.app.app_context():
        saved_loan = brittco.db().execute(
            "SELECT * FROM loans WHERE id=?", (row["id"],)
        ).fetchone()
        saved_defaults = brittco.closing_defaults(
            brittco.db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone(),
            saved_loan,
        )
    assert saved_defaults["interest_rate"] == "9"
    assert saved_defaults["term_months"] == 6
    assert saved_defaults["loan_amount"] == "50000"
    assert saved_defaults["property"] == "Staff typed address"
    assert saved_defaults["extension_count"] == ""

    edit = client.get(f"/loans/{row['id']}/edit")
    assert edit.status_code == 200
    assert 'value="Custom Loan" selected' in edit.get_data(as_text=True)


def test_blank_custom_loan_does_not_copy_deal_figures():
    client = _client()
    bid = _borrower()
    did = _deal(bid, "custom loan")
    resp = client.post(
        "/loans/new",
        data={
            "borrower_id": str(bid),
            "deal_id": str(did),
            "loan_number": "BC-CUSTOM-BLANK",
            "loan_type": "custom loan",
            "term_kind": "Fixed",
            "property_address": "Only what staff typed",
            "purchase_price": "",
            "loan_amount": "",
            "total_loan_amount": "",
            "rehab_cost": "",
            "points": "",
            "pricing_mode": "rate",
            "rate": "",
            "flat_fee": "",
            "base_term": "",
            "start_date": "2026-04-01",
            "payment_type": "Interest only",
            "payment_amount": "",
            "payment_frequency": "Monthly",
            "status": "Current",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Custom Loan" in resp.get_data(as_text=True)
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM loans WHERE loan_number=?", ("BC-CUSTOM-BLANK",)
        ).fetchone()
        defaults = brittco.closing_defaults(
            brittco.db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone(),
            row,
        )
    assert row["loan_type"] == "Custom Loan"
    assert row["original_principal"] in (None, "")
    assert row["rate"] in (None, "")
    assert row["points"] in (None, "")
    assert row["base_term_months"] in (None, "")
    assert row["purchase_price"] in (None, "")
    assert row["maturity_date"] in (None, "")
    assert row["property_address"] == "Only what staff typed"
    assert defaults["loan_type"] == "Custom Loan"
    assert defaults["interest_rate"] == ""
    assert defaults["points"] == ""
    assert defaults["term_months"] == ""
    assert defaults["term_days"] == ""
    assert defaults["extension_count"] == ""
    assert defaults["extension_rate"] == ""
    assert defaults["loan_amount"] == ""
    assert defaults["property"] == "Only what staff typed"


def test_fix_and_flip_deal_still_prefills_loan():
    client = _client()
    bid = _borrower()
    did = _deal(bid, "Fix and Flip", rate=11, points=1, loan_amount=80000, term_months=6)
    resp = client.post(f"/deals/{did}/create-loan", follow_redirects=False)
    assert resp.status_code in (302, 303)
    with brittco.app.app_context():
        deal = brittco.db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
        fields = brittco.loan_fields_from_deal(deal)
        row = brittco.db().execute(
            "SELECT * FROM loans WHERE deal_id=?", (did,)
        ).fetchone()
    assert fields["property_address"] == "99 Deal Lane"
    assert fields["original_principal"] == 80000
    assert fields["rate"] == 11
    assert fields["points"] == 1
    assert fields["loan_type"] == "Fix and Flip"
    assert fields["notes"] == "Created from funded deal"
    assert row["loan_type"] == "Fix and Flip"
    assert row["property_address"] == "99 Deal Lane"
    assert row["original_principal"] == 80000
    assert row["rate"] == 11
    assert row["points"] == 1
    assert row["notes"] == "Created from funded deal"
    assert row["pricing_mode"] == "rate"
    assert deal["status"] == "Funded"


def test_transactional_deal_still_applies_flat_fee_default():
    client = _client()
    bid = _borrower()
    did = _deal(
        bid,
        "Transactional Loan",
        rate=None,
        points=None,
        loan_amount=100000,
        term_months=None,
    )
    client.post(f"/deals/{did}/create-loan", follow_redirects=False)
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM loans WHERE deal_id=?", (did,)
        ).fetchone()
        bare = brittco.db().execute(
            """INSERT INTO deals
               (borrower_id, loan_type, address, loan_amount, rate, points, term_months, status, created_at, acked)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (bid, "Fix and Flip", "1 Template St", 80000, None, None, None, "Lead", "2026-01-01", 1),
        )
        brittco.db().commit()
        template_deal = brittco.db().execute(
            "SELECT * FROM deals WHERE id=?", (bare.lastrowid,)
        ).fetchone()
        defaults = brittco.closing_defaults(template_deal, None)
    assert row["loan_type"] == "Transactional Loan"
    assert row["pricing_mode"] == "flat_fee"
    assert row["points"] == 3
    assert row["flat_fee"] == 3000
    assert row["rate"] in (None, "")
    assert "3% flat fee" in (row["notes"] or "")
    assert defaults["loan_type"] == "Fix and Flip"
    assert defaults["interest_rate"] == "10"
    assert defaults["term_months"] == 4
    assert defaults["extension_count"] == 2


def test_custom_loan_still_sends_closing_application():
    client = _client()
    bid = _borrower()
    resp = client.post(
        f"/borrowers/{bid}/closing-application",
        data={
            "loan_type": "Custom Loan",
            "interest_rate": "8",
            "term_months": "5",
            "term_days": "0",
            "points": "0",
            "extension_count": "0",
            "extension_rate": "0",
            "loan_amount": "42000",
            "property": "4 Custom Ct",
            "next": f"/borrowers/{bid}",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE borrower_id=? ORDER BY id DESC LIMIT 1",
            (bid,),
        ).fetchone()
    assert row["loan_type"] == "Custom Loan"
    assert row["status"] == "sent"
    assert row["loan_amount"].replace(",", "") in ("42000", "42000.00")
