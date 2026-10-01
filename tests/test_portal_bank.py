"""Portal bank request follows the payment plan unless staff overrides it."""

import os
import tempfile
import uuid

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="brittco-portal-bank-")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["PLATFORM_MODE"] = "brittco_existing"
os.environ.pop("DWOLLA_KEY", None)
os.environ.pop("DWOLLA_SECRET", None)
os.environ.pop("DWOLLA_MASTER_CUSTOMER_URL", None)

import app as brittco


def _insert_borrower(portal_bank_request=None):
    email = f"bank-{uuid.uuid4().hex}@example.com"
    cur = brittco.db().execute(
        """INSERT INTO borrowers
           (name, email, phone, dob, ssn, address, city, state, zip, own_or_rent,
            employer, occupation, years_at_address, portal_bank_request)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "Pat Borrower",
            email,
            "8135550100",
            "1980-01-02",
            "123-45-6789",
            "1 Main",
            "Tampa",
            "FL",
            "33602",
            "Own",
            "Self",
            "Investor",
            5,
            portal_bank_request,
        ),
    )
    brittco.db().commit()
    return cur.lastrowid


def _loan(bid, payment_type, payment_frequency, status="Current"):
    brittco.db().execute(
        """INSERT INTO loans (borrower_id, loan_number, payment_type, payment_frequency, status)
           VALUES (?,?,?,?,?)""",
        (bid, f"LN-{uuid.uuid4().hex[:8]}", payment_type, payment_frequency, status),
    )
    brittco.db().commit()


def _completeness(bid):
    b = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    ready, missing = brittco.profile_ready(b)
    complete = brittco.application_completeness(b)
    return ready, missing, complete


def test_deferred_plan_is_complete_without_bank():
    with brittco.app.app_context():
        bid = _insert_borrower()
        _loan(bid, "Balloon", "Interest due at maturity")
        ready, missing, complete = _completeness(bid)
        assert ready
        assert "Bank name" not in missing
        assert "Bank name" not in complete["missing"]
        assert complete["profile_ready"] is True
        assert complete["ok"] is True


def test_nondeferred_monthly_requires_bank():
    with brittco.app.app_context():
        bid = _insert_borrower()
        _loan(bid, "Interest only", "Monthly")
        ready, missing, complete = _completeness(bid)
        assert ready is False
        assert "Bank name" in missing
        assert "Bank routing number" in complete["missing"]
        assert "Bank account number" in complete["missing"]
        assert complete["ok"] is False


def test_switch_off_skips_bank_on_monthly_plan():
    with brittco.app.app_context():
        bid = _insert_borrower("off")
        _loan(bid, "Amortizing", "Monthly")
        ready, missing, complete = _completeness(bid)
        assert ready is True
        assert not any(item.startswith("Bank") for item in missing)
        assert complete["profile_ready"] is True
        assert complete["ok"] is True


def test_switch_on_requires_bank_on_deferred_plan():
    with brittco.app.app_context():
        bid = _insert_borrower("on")
        _loan(bid, "Balloon", "Interest due at maturity")
        ready, missing, complete = _completeness(bid)
        assert ready is False
        assert "Bank name" in complete["missing"]
        assert complete["ok"] is False


def test_monthly_plan_completes_once_bank_is_on_file():
    with brittco.app.app_context():
        bid = _insert_borrower()
        brittco.db().execute(
            "UPDATE borrowers SET bank_name=?, bank_routing=?, bank_account=? WHERE id=?",
            ("First Bank", "021000021", "123456789", bid),
        )
        brittco.db().commit()
        _loan(bid, "Interest only", "Monthly")
        ready, _missing, complete = _completeness(bid)
        assert ready is True
        assert complete["ok"] is True
