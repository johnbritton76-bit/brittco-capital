"""Missouri closing application: link access, spouse signature, PDF smoke."""

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="brittco-closing-")
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("SMTP_HOST", None)

import closing_packet
import app as brittco


def _borrower(name, email, marital="Single", spouse=""):
    with brittco.app.app_context():
        return _borrower_in_context(name, email, marital, spouse)


def _borrower_in_context(name, email, marital="Single", spouse=""):
    cur = brittco.db().execute(
        """INSERT INTO borrowers
           (name, entity_type, entity_name, email, marital_status, spouse_name, address, city, state, zip)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, "LLC", name + " LLC", email, marital, spouse, "1 Main St", "Kansas City", "MO", "64105"),
    )
    brittco.db().commit()
    return cur.lastrowid


def _staff():
    client = brittco.app.test_client()
    with client.session_transaction() as sess:
        sess["staff_id"] = 1
    return client


def _send(bid):
    client = _staff()
    response = client.post(
        f"/borrowers/{bid}/closing-application",
        data={
            "loan_type": "Fix and Flip",
            "interest_rate": "10",
            "term_months": "4",
            "term_days": "0",
            "points": "1",
            "extension_count": "2",
            "extension_rate": "2",
            "loan_amount": "150000",
            "property": "10 Oak St, Kansas City, MO",
            "next": f"/borrowers/{bid}",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE borrower_id=? ORDER BY id DESC LIMIT 1",
            (bid,),
        ).fetchone()
    assert row and row["token"]
    assert row["status"] == "sent"
    return row


def _application(signatory, spouse=None):
    fields = {
        "borrower_legal_name": signatory + " LLC",
        "borrower_entity_type": "LLC",
        "borrower_formation_state": "Missouri",
        "borrower_notice_address": "1 Main St, Kansas City, MO 64105",
        "signatory_name": signatory,
        "signatory_title": "Authorized signatory",
        "property": "10 Oak St, Kansas City, MO",
        "county": "Jackson",
        "state": "Missouri",
        "legal_description": "Lot 1, Block 2, Kansas City, Jackson County, Missouri.",
        "effective_date": "2026-10-01",
        "guarantor_name": signatory,
        "guarantor_address": "1 Main St, Kansas City, MO 64105",
        "marital_status": "Married" if spouse else "Single",
        "spouse_name": spouse or "",
        "spouse_dob": "1990-02-02" if spouse else "",
        "borrower_signature_name": signatory,
        "perjury_ack": "yes",
    }
    if spouse:
        fields["spouse_signature_name"] = spouse
        fields["spouse_perjury_ack"] = "yes"
    return fields


def test_retired_templates_are_not_seeded():
    with brittco.app.app_context():
        brittco.db().execute("DELETE FROM form_templates")
        brittco.db().commit()
        brittco.seed_form_templates()
        keys = {row["form_key"] for row in brittco.db().execute("SELECT form_key FROM form_templates")}
    assert "deed_of_trust" not in keys
    assert "promissory_note_guaranty" not in keys


def test_historical_form_link_still_opens():
    bid = _borrower("Historical Hale", "historical.hale@example.com")
    with brittco.app.app_context():
        brittco.db().execute(
        """INSERT INTO form_packets (token, form_key, borrower_id, status, payload, created_at)
           VALUES (?,?,?,?,?,?)""",
            ("hist-deed-token", "deed_of_trust", bid, "Completed", "{}", "2026-01-01"),
        )
        brittco.db().commit()
    response = brittco.app.test_client().get("/forms/hist-deed-token")
    assert response.status_code == 200
    assert b"Deed of Trust" in response.data


def test_bad_closing_token_is_rejected():
    response = brittco.app.test_client().get("/closing/not-a-real-token")
    assert response.status_code == 404


def test_prefilled_link_and_spouse_signature_required():
    bid = _borrower("Ada Lender", "ada.lender@example.com", marital="Married", spouse="Ben Lender")
    row = _send(bid)
    client = brittco.app.test_client()
    opened = client.get(f"/closing/{row['token']}")
    assert opened.status_code == 200
    assert b"Ada Lender" in opened.data
    assert b"penalties of perjury" in opened.data
    assert b"canvas" not in opened.data.lower()

    missing = dict(_application("Ada Lender", "Ben Lender"))
    missing.pop("spouse_signature_name")
    missing.pop("spouse_perjury_ack")
    denied = client.post(f"/closing/{row['token']}", data=missing)
    assert denied.status_code == 200
    assert b"Spouse" in denied.data
    with brittco.app.app_context():
        still = brittco.db().execute("SELECT status FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert still["status"] == "sent"

    signed = client.post(f"/closing/{row['token']}", data=_application("Ada Lender", "Ben Lender"))
    assert signed.status_code == 200
    assert b"Submitted" in signed.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT * FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert saved["status"] == "submitted"
    assert saved["dot_filename"] and saved["note_filename"]
    deed_path = os.path.join(brittco.UPLOAD_DIR, saved["dot_filename"])
    note_path = os.path.join(brittco.UPLOAD_DIR, saved["note_filename"])
    deed = open(deed_path, "rb").read()
    note = open(note_path, "rb").read()
    assert deed.startswith(b"%PDF")
    assert note.startswith(b"%PDF")
    assert b"Missouri" in deed and b"Deed of Trust" in deed
    assert b"Missouri" in note and b"Ben Lender" in note

    staff = _staff()
    approved = staff.post(f"/admin/closings/{row['id']}", data={"action": "approve"})
    assert approved.status_code in (302, 303)
    package = staff.get(f"/admin/closings/{row['id']}/package.zip")
    assert package.status_code == 200
    assert package.data[:2] == b"PK"


def test_single_borrower_does_not_need_a_spouse_signature():
    bid = _borrower("Cara Solo", "cara.solo@example.com")
    row = _send(bid)
    response = brittco.app.test_client().post(f"/closing/{row['token']}", data=_application("Cara Solo"))
    assert response.status_code == 200
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT status FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert saved["status"] == "submitted"
    note = closing_packet.build_note_pdf(
        {"signatory_name": "Cara Solo", "guarantor_name": "Cara Solo", "marital_status": "Single",
         "note_principal": "1000", "loan_type": "Fix and Flip", "interest_rate": "10",
         "term_months": "4", "borrower_legal_name": "Cara Solo LLC", "property": "1 Main",
         "county": "Jackson", "state": "Missouri"},
        {"borrower": {"typed_name": "Cara Solo", "signed_at": "2026-10-01T12:00:00", "ip": "127.0.0.1"}},
    )
    assert b"joins this Guaranty" not in note
