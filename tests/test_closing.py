"""Loan application: state templates, portal password, profile sync, e-sign."""

import logging
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
        "portal_password": "Testpass1",
        "portal_password_confirm": "Testpass1",
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
    assert b"Signed and received" not in opened.data
    assert b"Submit and sign" in opened.data

    missing = dict(_application("Ada Lender", "Ben Lender"))
    missing.pop("spouse_signature_name")
    missing.pop("spouse_perjury_ack")
    denied = client.post(f"/closing/{row['token']}", data=missing)
    assert denied.status_code == 200
    assert b"Spouse" in denied.data
    assert b"Your application was not submitted." in denied.data
    assert b"Signed and received" not in denied.data
    with brittco.app.app_context():
        still = brittco.db().execute("SELECT status FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert still["status"] == "sent"

    signed = client.post(f"/closing/{row['token']}", data=_application("Ada Lender", "Ben Lender"))
    assert signed.status_code == 200
    assert b"Submitted" in signed.data
    assert b"Signed and received" in signed.data
    assert b"follow up" in signed.data.lower()
    assert b"Submit and sign" not in signed.data
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


def test_state_templates_are_stored_and_selected():
    with brittco.app.app_context():
        closing_packet.ensure_schema(brittco.db())
        missouri = closing_packet.active_template(brittco.db(), "MO", "deed_of_trust")
        kansas = closing_packet.active_template(brittco.db(), "KS", "mortgage")
        note = closing_packet.active_template(brittco.db(), "KS", "note_guaranty")
    assert missouri and "Deed of Trust" in missouri["body"]
    assert kansas and "Mortgage" in kansas["title"]
    assert note and "Mortgage" in note["body"] and "not a deed of trust" in note["body"].lower()
    assert closing_packet.instruments_for_state("Missouri") == ("deed_of_trust", "note_guaranty")
    assert closing_packet.instruments_for_state("KS") == ("mortgage", "note_guaranty")
    assert closing_packet.instruments_for_state("TX") == ()
    assert closing_packet.state_from_text("10 Oak St, Kansas City, MO") == "MO"
    assert closing_packet.state_from_text("500 Main St, Wichita, KS") == "KS"

    with brittco.app.app_context():
        original = kansas["body"]
        closing_packet.publish_template(
            brittco.db(), "KS", "mortgage", "Kansas Mortgage", "UNIQUE-KS-BODY {{property_address}}"
        )
        rendered = closing_packet.render_closing_pdfs(
            {
                "state": "Kansas",
                "property": "9 Hill St",
                "county": "Sedgwick",
                "borrower_legal_name": "KS LLC",
                "signatory_name": "Kim",
                "legal_description": "Lot 9",
                "note_principal": "1000",
                "loan_type": "Fix and Flip",
                "interest_rate": "10",
                "term_months": "4",
                "guarantor_name": "Kim",
                "marital_status": "Single",
                "effective_date": "2026-10-01",
            },
            {"borrower": {"typed_name": "Kim", "signed_at": "2026-10-01T12:00:00", "ip": "127.0.0.1"}},
            brittco.db(),
        )
        closing_packet.publish_template(brittco.db(), "KS", "mortgage", "Kansas Mortgage", original)
    assert "error" not in rendered
    assert rendered["security_type"] == "mortgage"
    assert b"UNIQUE-KS-BODY" in rendered["security_pdf"]
    assert b"Deed of Trust" not in rendered["security_pdf"]

    staff = _staff()
    page = staff.get("/admin/forms")
    assert page.status_code == 200
    assert b"Loan applications" in page.data
    assert b"deed_of_trust" in page.data
    assert b"mortgage" in page.data
    assert staff.post(
        "/admin/state-templates",
        data={
            "state_code": "OK",
            "instrument_type": "mortgage",
            "title": "Oklahoma Mortgage",
            "body": "OKLAHOMA-MORTGAGE-MARK {{property_address}}",
        },
    ).status_code in (302, 303)
    assert staff.post(
        "/admin/state-templates",
        data={
            "state_code": "OK",
            "instrument_type": "note_guaranty",
            "title": "Oklahoma Note",
            "body": "OKLAHOMA-NOTE-MARK",
        },
    ).status_code in (302, 303)
    with brittco.app.app_context():
        assert closing_packet.instruments_for_state("Oklahoma", brittco.db()) == (
            "mortgage",
            "note_guaranty",
        )
        oklahoma = closing_packet.render_closing_pdfs(
            {
                "state": "Oklahoma",
                "property": "1 Broadway",
                "county": "Oklahoma",
                "borrower_legal_name": "OK LLC",
                "signatory_name": "Olivia",
                "legal_description": "Lot 3",
                "note_principal": "1000",
                "loan_type": "Bridge",
                "interest_rate": "10",
                "term_months": "6",
                "guarantor_name": "Olivia",
                "marital_status": "Single",
                "effective_date": "2026-10-01",
            },
            {"borrower": {"typed_name": "Olivia"}},
            brittco.db(),
        )
    assert oklahoma["security_type"] == "mortgage"
    assert b"OKLAHOMA-MORTGAGE-MARK" in oklahoma["security_pdf"]
    assert b"Deed of Trust" not in oklahoma["security_pdf"]


def test_kansas_property_generates_a_mortgage_not_a_deed_of_trust():
    bid = _borrower("Kara Sun", "kara.sun@example.com")
    client = _staff()
    sent = client.post(
        f"/borrowers/{bid}/closing-application",
        data={
            "loan_type": "Bridge",
            "interest_rate": "11",
            "term_months": "6",
            "term_days": "0",
            "points": "1",
            "extension_count": "0",
            "extension_rate": "0",
            "loan_amount": "200000",
            "property": "500 Main St, Wichita, KS",
            "property_state": "KS",
            "next": f"/borrowers/{bid}",
        },
        follow_redirects=False,
    )
    assert sent.status_code in (302, 303)
    with brittco.app.app_context():
        row = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE borrower_id=? ORDER BY id DESC LIMIT 1",
            (bid,),
        ).fetchone()
    fields = _application("Kara Sun")
    fields["property"] = "500 Main St, Wichita, KS"
    fields["state"] = "Kansas"
    fields["county"] = "Sedgwick"
    fields["legal_description"] = "Lot 5, Wichita, Sedgwick County, Kansas."
    opened = brittco.app.test_client().get(f"/closing/{row['token']}")
    assert b"Loan application" in opened.data
    signed = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert signed.status_code == 200
    assert b"Submitted" in signed.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT * FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    deed = open(os.path.join(brittco.UPLOAD_DIR, saved["dot_filename"]), "rb").read()
    note = open(os.path.join(brittco.UPLOAD_DIR, saved["note_filename"]), "rb").read()
    assert b"Mortgage" in deed
    assert b"Kansas" in deed
    assert b"Deed of Trust" not in deed
    assert b"Kansas" in note
    assert b"Deed of Trust" not in note
    payload = closing_packet.load_json(saved["payload"], {})
    assert payload["security_instrument"] == "mortgage"
    assert payload["state_code"] == "KS"


def test_unknown_state_does_not_use_the_missouri_deed():
    bid = _borrower("Terry State", "terry.state@example.com")
    row = _send(bid)
    fields = _application("Terry State")
    fields["state"] = "Texas"
    response = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert response.status_code == 200
    assert b"No document templates" in response.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT status FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert saved["status"] == "sent"


def test_new_borrower_sets_portal_password():
    bid = _borrower("Evan Pass", "evan.pass@example.com")
    row = _send(bid)
    client = brittco.app.test_client()
    opened = client.get(f"/closing/{row['token']}")
    assert b"portal password" in opened.data.lower()
    missing = _application("Evan Pass")
    missing.pop("portal_password")
    missing.pop("portal_password_confirm")
    denied = client.post(f"/closing/{row['token']}", data=missing)
    assert denied.status_code == 200
    assert b"Portal password" in denied.data
    fields = _application("Evan Pass")
    fields["portal_password"] = "Harbor-Lane-19"
    fields["portal_password_confirm"] = "Harbor-Lane-19"
    signed = client.post(f"/closing/{row['token']}", data=fields)
    assert signed.status_code == 200
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT password FROM borrowers WHERE id=?", (bid,)).fetchone()
        assert saved["password"].startswith("pbkdf2$")
        assert brittco.portal_password_matches(saved["password"], "Harbor-Lane-19")
        assert not brittco.portal_password_matches(saved["password"], "borrower")
    login = brittco.app.test_client().post(
        "/portal/login",
        data={"email": "evan.pass@example.com", "password": "Harbor-Lane-19"},
        follow_redirects=False,
    )
    assert login.status_code in (302, 303)


def test_existing_portal_password_is_not_reset():
    bid = _borrower("Fran Kept", "fran.kept@example.com")
    with brittco.app.app_context():
        brittco.db().execute("UPDATE borrowers SET password=? WHERE id=?", ("already-set", bid))
        brittco.db().commit()
    row = _send(bid)
    opened = brittco.app.test_client().get(f"/closing/{row['token']}")
    assert b"already have a borrower portal password" in opened.data
    fields = _application("Fran Kept")
    fields.pop("portal_password")
    fields.pop("portal_password_confirm")
    signed = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert signed.status_code == 200
    assert b"Submitted" in signed.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT password FROM borrowers WHERE id=?", (bid,)).fetchone()
    assert saved["password"] == "already-set"
    login = brittco.app.test_client().post(
        "/portal/login",
        data={"email": "fran.kept@example.com", "password": "already-set"},
        follow_redirects=False,
    )
    assert login.status_code in (302, 303)


def test_placeholder_password_must_be_replaced():
    bid = _borrower("Glen Default", "glen.default@example.com")
    with brittco.app.app_context():
        brittco.db().execute("UPDATE borrowers SET password=? WHERE id=?", ("borrower", bid))
        brittco.db().commit()
    row = _send(bid)
    fields = _application("Glen Default")
    fields.pop("portal_password")
    fields.pop("portal_password_confirm")
    denied = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert b"Portal password" in denied.data
    fields = _application("Glen Default")
    fields["portal_password"] = "Chosen-Pass-2"
    fields["portal_password_confirm"] = "Chosen-Pass-2"
    signed = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert b"Submitted" in signed.data
    login = brittco.app.test_client().post(
        "/portal/login",
        data={"email": "glen.default@example.com", "password": "Chosen-Pass-2"},
        follow_redirects=False,
    )
    assert login.status_code in (302, 303)


def test_submitted_profile_fields_sync_to_borrower():
    bid = _borrower("Dana Profile", "dana.profile@example.com")
    with brittco.app.app_context():
        brittco.db().execute("UPDATE borrowers SET ssn=? WHERE id=?", ("111-22-3333", bid))
        brittco.db().commit()
    row = _send(bid)
    masked = _application("Dana Profile")
    masked["borrower_ssn"] = "***-**-3333"
    masked["borrower_phone"] = "816-555-0199"
    masked["borrower_dob"] = "1981-03-04"
    masked["borrower_ein"] = "98-7654321"
    masked["mailing_address"] = "42 West St"
    masked["mailing_city"] = "Wichita"
    masked["mailing_state"] = "KS"
    masked["mailing_zip"] = "67202"
    masked["borrower_legal_name"] = "Dana Profile LLC"
    masked["borrower_entity_type"] = "LLC"
    signed = brittco.app.test_client().post(f"/closing/{row['token']}", data=masked)
    assert b"Submitted" in signed.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
        packet = brittco.db().execute("SELECT payload FROM closing_applications WHERE id=?", (row["id"],)).fetchone()
    assert saved["ssn"] == "111-22-3333"
    assert saved["phone"] == "816-555-0199"
    assert saved["dob"] == "1981-03-04"
    assert saved["ein"] == "98-7654321"
    assert saved["address"] == "42 West St"
    assert saved["city"] == "Wichita"
    assert saved["state"] == "KS"
    assert saved["zip"] == "67202"
    assert saved["entity_name"] == "Dana Profile LLC"
    assert saved["entity_type"] == "LLC"
    assert "111-22-3333" not in packet["payload"]
    assert "150000" not in (saved["notes"] or "")

    fields = dict(masked)
    fields["borrower_ssn"] = "123-45-6789"
    fields["spouse_name"] = "Sam Profile"
    fields["spouse_ssn"] = "222-33-4444"
    fields["spouse_dob"] = "1982-04-05"
    fields["marital_status"] = "Married"
    fields["spouse_signature_name"] = "Sam Profile"
    fields["spouse_perjury_ack"] = "yes"
    with brittco.app.app_context():
        brittco.db().execute(
            "UPDATE closing_applications SET status=? WHERE id=?",
            ("changes_requested", row["id"]),
        )
        brittco.db().commit()
    again = brittco.app.test_client().post(f"/closing/{row['token']}", data=fields)
    assert b"Submitted" in again.data
    with brittco.app.app_context():
        saved = brittco.db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    assert saved["ssn"] == "123-45-6789"
    assert saved["spouse_name"] == "Sam Profile"
    assert saved["spouse_ssn"] == "222-33-4444"
    assert saved["spouse_dob"] == "1982-04-05"
    assert saved["marital_status"] == "Married"


def _pending_ids():
    with brittco.app.app_context():
        return [item["id"] for item in brittco.pending_closing_reviews()]


def _forms_status_window(html, name):
    marker = f"<td>{name}</td>"
    start = html.find(marker)
    assert start >= 0
    row_start = html.rfind("<tr", 0, start)
    row_end = html.find("</tr>", start)
    assert row_start >= 0 and row_end >= 0
    return html[row_start:row_end]


def test_submitted_application_flags_staff_until_approved():
    bid = _borrower("Iris Review", "iris.review@example.com")
    row = _send(bid)
    assert row["id"] not in _pending_ids()

    staff = _staff()
    before = staff.get("/")
    assert before.status_code == 200
    assert b"Iris Review" not in before.data

    signed = brittco.app.test_client().post(
        f"/closing/{row['token']}", data=_application("Iris Review")
    )
    assert signed.status_code == 200
    assert b"Submitted" in signed.data
    pending = _pending_ids()
    assert row["id"] in pending

    dash = staff.get("/")
    assert dash.status_code == 200
    dash_html = dash.data.decode()
    assert "Loan applications awaiting review" in dash_html
    assert "Iris Review" in dash_html
    assert f"/admin/closings/{row['id']}" in dash_html
    assert (
        f'class="nav-count" title="Signed loan applications awaiting review">{len(pending)}</span>'
        in dash_html
    )

    forms_html = staff.get("/admin/forms").data.decode()
    window = _forms_status_window(forms_html, "Iris Review")
    assert "needs-review" in window
    assert "Needs review" in window

    borrower_html = staff.get(f"/borrowers/{bid}").data.decode()
    assert "Signed loan application needs review" in borrower_html
    assert f"/admin/closings/{row['id']}" in borrower_html

    with brittco.app.app_context():
        cur = brittco.db().execute(
            """INSERT INTO loans
               (borrower_id, loan_number, loan_type, property_address, status,
                original_principal, current_balance)
               VALUES (?,?,?,?,?,?,?)""",
            (
                bid,
                "BC-IRIS-1",
                "Fix and Flip",
                "10 Oak St, Kansas City, MO",
                "Current",
                150000,
                150000,
            ),
        )
        lid = cur.lastrowid
        brittco.db().execute(
            "UPDATE closing_applications SET loan_id=? WHERE id=?",
            (lid, row["id"]),
        )
        brittco.db().commit()

    loan_page = staff.get(f"/loans/{lid}")
    assert loan_page.status_code == 200, loan_page.data[:500]
    loan_html = loan_page.data.decode()
    assert "Signed loan application needs review" in loan_html
    assert f"/admin/closings/{row['id']}" in loan_html

    approved = staff.post(f"/admin/closings/{row['id']}", data={"action": "approve"})
    assert approved.status_code in (302, 303)
    assert row["id"] not in _pending_ids()

    cleared = staff.get("/").data.decode()
    assert f"/admin/closings/{row['id']}" not in cleared
    assert "Iris Review" not in cleared.split("Latest 5 loans")[0]
    forms_after = staff.get("/admin/forms").data.decode()
    window = _forms_status_window(forms_after, "Iris Review")
    assert "needs-review" not in window
    assert "Approved" in window
    assert "Needs review" not in window

    assert "Signed loan application needs review" not in staff.get(f"/loans/{lid}").data.decode()
    assert "Signed loan application needs review" not in staff.get(f"/borrowers/{bid}").data.decode()

    review = staff.get(f"/admin/closings/{row['id']}")
    assert review.status_code == 200
    package = staff.get(f"/admin/closings/{row['id']}/package.zip")
    assert package.status_code == 200
    assert package.data[:2] == b"PK"


def test_closing_notify_always_includes_john_and_nate(monkeypatch):
    monkeypatch.setattr(brittco, "staff_notify_list", lambda: [])
    assert [e.lower() for e in brittco.closing_submit_notify_list()] == [
        "john@brittcocapital.com",
        "nate@brittcocapital.com",
    ]
    monkeypatch.setattr(
        brittco,
        "staff_notify_list",
        lambda: ["Nate@BrittcoCapital.com", "ops@example.com"],
    )
    listed = brittco.closing_submit_notify_list()
    assert [e.lower() for e in listed] == [
        "nate@brittcocapital.com",
        "ops@example.com",
        "john@brittcocapital.com",
    ]
    assert listed[0] == "Nate@BrittcoCapital.com"


def _capture_logs():
    records = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _ListHandler(level=logging.INFO)
    brittco.logger.addHandler(handler)
    return records, handler


def test_signed_application_emails_john_and_nate_with_pdf_copy(monkeypatch):
    sent = []

    def fake_send(to_email, subject, body, attachment=None, attachment_name=None):
        sent.append(
            {
                "to": to_email,
                "subject": subject,
                "body": body,
                "attachment": attachment,
                "name": attachment_name,
            }
        )
        return True

    monkeypatch.setattr(brittco, "send_mail", fake_send)
    monkeypatch.setattr(brittco, "staff_notify_list", lambda: ["ops@example.com"])
    records, handler = _capture_logs()
    try:
        bid = _borrower("Helen Packet", "helen.packet@example.com")
        row = _send(bid)
        signed = brittco.app.test_client().post(
            f"/closing/{row['token']}", data=_application("Helen Packet")
        )
    finally:
        brittco.logger.removeHandler(handler)
    assert signed.status_code == 200
    assert b"Signed and received" in signed.data
    assert b"Your application was not submitted." not in signed.data
    recipients = [item["to"].lower() for item in sent]
    assert recipients == [
        "ops@example.com",
        "john@brittcocapital.com",
        "nate@brittcocapital.com",
    ]
    for item in sent:
        assert item["subject"] == "Loan application signed — Helen Packet"
        assert "Helen Packet" in item["body"]
        assert "Property: 10 Oak St, Kansas City, MO" in item["body"]
        assert "Fix and Flip" in item["body"]
        assert f"/admin/closings/{row['id']}" in item["body"]
        assert item["attachment"][:2] == b"PK"
        assert item["name"].endswith(".zip")
        assert "attached" in item["body"].lower()
    with brittco.app.app_context():
        saved = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()
        docs = brittco.db().execute(
            "SELECT filename FROM documents WHERE borrower_id=?", (bid,)
        ).fetchall()
    assert saved["status"] == "submitted"
    assert saved["dot_filename"] and saved["note_filename"]
    stored = {doc["filename"] for doc in docs}
    assert saved["dot_filename"] in stored and saved["note_filename"] in stored
    messages = [record.getMessage() for record in records]
    assert any(f"closing submit {row['id']} saved status=submitted" in message for message in messages)
    assert any("emailed john@brittcocapital.com with signed PDF copy" in message for message in messages)
    assert any("emailed nate@brittcocapital.com with signed PDF copy" in message for message in messages)


def test_email_failure_does_not_undo_saved_application(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(brittco, "send_mail", boom)
    records, handler = _capture_logs()
    try:
        bid = _borrower("Ivan Mail", "ivan.mail@example.com")
        row = _send(bid)
        signed = brittco.app.test_client().post(
            f"/closing/{row['token']}", data=_application("Ivan Mail")
        )
    finally:
        brittco.logger.removeHandler(handler)
    assert b"Signed and received" in signed.data
    assert b"Your application was not submitted." not in signed.data
    with brittco.app.app_context():
        saved = brittco.db().execute(
            "SELECT status, dot_filename, note_filename FROM closing_applications WHERE id=?",
            (row["id"],),
        ).fetchone()
    assert saved["status"] == "submitted"
    deed = open(os.path.join(brittco.UPLOAD_DIR, saved["dot_filename"]), "rb").read()
    note = open(os.path.join(brittco.UPLOAD_DIR, saved["note_filename"]), "rb").read()
    assert deed.startswith(b"%PDF") and note.startswith(b"%PDF")
    messages = [record.getMessage() for record in records]
    assert any(f"closing submit {row['id']} saved status=submitted" in message for message in messages)
    assert any("email to john@brittcocapital.com failed" in message for message in messages)
    assert any("email to nate@brittcocapital.com failed" in message for message in messages)


def test_attachment_failure_still_sends_review_link(monkeypatch):
    sent = []

    def fake_send(to_email, subject, body, attachment=None, attachment_name=None):
        if attachment is not None:
            raise RuntimeError("smtp rejected attachment")
        sent.append((to_email.lower(), body, attachment_name))
        return True

    monkeypatch.setattr(brittco, "send_mail", fake_send)
    monkeypatch.setattr(brittco, "staff_notify_list", lambda: [])
    bid = _borrower("Jill Attach", "jill.attach@example.com")
    row = _send(bid)
    signed = brittco.app.test_client().post(
        f"/closing/{row['token']}", data=_application("Jill Attach")
    )
    assert b"Signed and received" in signed.data
    assert [item[0] for item in sent] == [
        "john@brittcocapital.com",
        "nate@brittcocapital.com",
    ]
    for _email, body, name in sent:
        assert name is None
        assert f"/admin/closings/{row['id']}" in body
        assert "could not be attached" in body
        assert "Jill Attach" in body
        assert "10 Oak St" in body
    with brittco.app.app_context():
        status = brittco.db().execute(
            "SELECT status FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()["status"]
    assert status == "submitted"


def test_pdf_store_failure_is_obvious_and_does_not_submit(monkeypatch):
    calls = []

    def fail_store(*_args, **_kwargs):
        raise OSError("disk full")

    def fake_send(*args, **kwargs):
        calls.append(args)
        return True

    monkeypatch.setattr(brittco, "_store_closing_pdfs", fail_store)
    monkeypatch.setattr(brittco, "send_mail", fake_send)
    records, handler = _capture_logs()
    try:
        bid = _borrower("Ken Disk", "ken.disk@example.com")
        row = _send(bid)
        failed = brittco.app.test_client().post(
            f"/closing/{row['token']}", data=_application("Ken Disk")
        )
    finally:
        brittco.logger.removeHandler(handler)
    assert failed.status_code == 200
    assert b"Your application was not submitted." in failed.data
    assert b"Please try again" in failed.data
    assert b"contact Brittco Capital" in failed.data
    assert b"Signed and received" not in failed.data
    assert b"Submit and sign" in failed.data
    assert calls == []
    with brittco.app.app_context():
        saved = brittco.db().execute(
            "SELECT status, dot_filename FROM closing_applications WHERE id=?",
            (row["id"],),
        ).fetchone()
    assert saved["status"] == "sent"
    assert not saved["dot_filename"]
    messages = [record.getMessage() for record in records]
    assert any(f"closing submit {row['id']} failed before save" in message for message in messages)
    assert not any("saved status=submitted" in message for message in messages)


def test_pdf_generation_error_does_not_claim_success(monkeypatch):
    monkeypatch.setattr(
        closing_packet,
        "render_closing_pdfs",
        lambda *_args, **_kwargs: {"error": "No document templates are stored for Missouri."},
    )
    bid = _borrower("Lena Template", "lena.template@example.com")
    row = _send(bid)
    failed = brittco.app.test_client().post(
        f"/closing/{row['token']}", data=_application("Lena Template")
    )
    page = failed.data.decode()
    assert "Your application was not submitted." in page
    assert "No document templates are stored for Missouri." in page
    assert "Signed and received" not in page
    with brittco.app.app_context():
        status = brittco.db().execute(
            "SELECT status FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()["status"]
    assert status == "sent"


def test_document_dates_stay_independent_of_the_term():
    base = closing_packet.derive({
        "loan_type": "Fix and Flip",
        "interest_rate": "10",
        "term_months": "4",
        "term_days": "0",
        "points": "0",
        "extension_count": "2",
        "extension_rate": "2",
        "note_principal": "150000",
        "effective_date": "2026-10-01",
        "state": "Missouri",
    })
    assert base["maturity_date"] == "2027-02-01"
    assert base["outside_date"] == "2027-04-01"

    moved, changes = closing_packet.apply_document_dates(base, {
        "effective_date": "2026-10-15",
        "maturity_date": "2027-02-01",
    })
    assert moved["effective_date"] == "2026-10-15"
    assert moved["maturity_date"] == "2027-02-01"
    assert moved["maturity_override"] == "1"
    assert [item["key"] for item in changes] == ["effective_date"]

    aligned, aligned_changes = closing_packet.apply_document_dates(base, {
        "effective_date": "2026-10-15",
        "maturity_date": "2027-02-15",
    })
    assert aligned["maturity_date"] == "2027-02-15"
    assert aligned.get("maturity_override") in ("", None)
    assert {item["key"] for item in aligned_changes} == {"effective_date", "maturity_date"}

    try:
        closing_packet.apply_document_dates(base, {
            "effective_date": "2026-10-15",
            "maturity_date": "2026-10-01",
        })
    except closing_packet.DocumentDateError as exc:
        assert "on or after" in str(exc)
    else:
        raise AssertionError("maturity before the closing date should be rejected")

    resigned_errors, resigned = closing_packet.submission_errors(
        moved,
        _application("Ada Lender"),
    )
    assert resigned_errors == []
    assert resigned["effective_date"] == "2026-10-01"
    assert resigned["maturity_date"] == "2027-02-01"
    assert not closing_packet._flag(resigned.get("maturity_override"))


def test_stock_templates_print_closing_and_maturity_dates_only():
    with brittco.app.app_context():
        closing_packet.ensure_schema(brittco.db())
        fields = closing_packet.dates_on_documents(
            {"state": "Missouri", "effective_date": "2026-10-01", "term_months": "4", "loan_type": "Fix and Flip"},
            brittco.db(),
        )
        note = closing_packet.active_template(brittco.db(), "MO", "note_guaranty")
        original = note["body"]
        closing_packet.publish_template(
            brittco.db(),
            "MO",
            "note_guaranty",
            note["title"],
            original + "\nFirst payment date: {{first_payment_date}}.\n",
        )
        try:
            extra = closing_packet.dates_on_documents(
                {
                    "state": "Missouri",
                    "effective_date": "2026-10-01",
                    "term_months": "4",
                    "loan_type": "Fix and Flip",
                    "first_payment_date": "2026-11-01",
                },
                brittco.db(),
            )
            rendered = closing_packet.render_closing_pdfs(
                {
                    "state": "Missouri",
                    "property": "10 Oak St",
                    "county": "Jackson",
                    "borrower_legal_name": "Date LLC",
                    "signatory_name": "Dana",
                    "legal_description": "Lot 1",
                    "note_principal": "1000",
                    "loan_type": "Fix and Flip",
                    "interest_rate": "10",
                    "term_months": "4",
                    "guarantor_name": "Dana",
                    "marital_status": "Single",
                    "effective_date": "2026-10-01",
                    "first_payment_date": "2026-11-01",
                },
                {"borrower": {"typed_name": "Dana", "signed_at": "2026-10-01T12:00:00"}},
                brittco.db(),
            )
        finally:
            closing_packet.publish_template(
                brittco.db(), "MO", "note_guaranty", note["title"], original
            )
    assert [field["key"] for field in fields] == ["effective_date", "maturity_date"]
    assert [field["key"] for field in extra] == ["effective_date", "maturity_date", "first_payment_date"]
    assert b"November 1, 2026" in rendered["note_pdf"]


def test_date_form_waits_for_a_signed_packet_and_requires_staff():
    bid = _borrower("Ned Unsigned", "ned.unsigned@example.com")
    row = _send(bid)
    anon = brittco.app.test_client().post(
        f"/admin/closings/{row['id']}",
        data={"action": "dates", "effective_date": "2026-10-15", "maturity_date": "2027-02-01"},
    )
    assert anon.status_code in (302, 303)
    assert "login" in (anon.headers.get("Location") or "")
    staff = _staff()
    opened = staff.get(f"/admin/closings/{row['id']}")
    assert b"Document dates" not in opened.data
    refused = staff.post(
        f"/admin/closings/{row['id']}",
        data={"action": "dates", "effective_date": "2026-10-15", "maturity_date": "2027-02-01"},
        follow_redirects=True,
    )
    assert b"after the borrower has signed" in refused.data
    with brittco.app.app_context():
        saved = brittco.db().execute(
            "SELECT status FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()
    assert saved["status"] == "sent"


def test_staff_can_change_document_dates_without_a_new_signature():
    bid = _borrower("Owen Dates", "owen.dates@example.com")
    row = _send(bid)
    signed = brittco.app.test_client().post(f"/closing/{row['token']}", data=_application("Owen Dates"))
    assert signed.status_code == 200
    assert b"Signed and received" in signed.data
    staff = _staff()
    approved = staff.post(f"/admin/closings/{row['id']}", data={"action": "approve"})
    assert approved.status_code in (302, 303)

    with brittco.app.app_context():
        cur = brittco.db().execute(
            """INSERT INTO loans
               (borrower_id, loan_number, start_date, maturity_date, next_payment_due,
                payment_frequency, status)
               VALUES (?,?,?,?,?,?,?)""",
            (bid, "BC-DATE-1", "2026-10-01", "2027-02-01", "2027-02-01", "Due at maturity", "Current"),
        )
        lid = cur.lastrowid
        brittco.db().execute(
            """UPDATE closing_applications
               SET loan_id=?, title_sent_at=?
               WHERE id=?""",
            (lid, "2026-10-02T09:00:00", row["id"]),
        )
        brittco.db().commit()
        before = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()
        before_signatures = before["signatures"]
        before_note = open(os.path.join(brittco.UPLOAD_DIR, before["note_filename"]), "rb").read()

    review = staff.get(f"/admin/closings/{row['id']}")
    assert b"Document dates" in review.data
    assert b"Closing / note date" in review.data
    assert b"Maturity date" in review.data
    assert b"do not approve it again" in review.data
    assert b'value="2026-10-01"' in review.data
    assert b'value="2027-02-01"' in review.data

    changed = staff.post(
        f"/admin/closings/{row['id']}",
        data={
            "action": "dates",
            "effective_date": "2026-10-15",
            "maturity_date": "2027-03-15",
        },
        follow_redirects=True,
    )
    page = changed.data.decode()
    assert "Updated the closing documents" in page
    assert "October 15, 2026" in page
    assert "March 15, 2027" in page
    assert "Email the zip to title again" in page
    assert "admin@brittcocapital.com" in page

    with brittco.app.app_context():
        saved = brittco.db().execute(
            "SELECT * FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()
        payload = closing_packet.load_json(saved["payload"], {})
        edits = brittco.db().execute(
            "SELECT * FROM closing_date_edits WHERE closing_id=?", (row["id"],)
        ).fetchall()
        loan = brittco.db().execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        note = open(os.path.join(brittco.UPLOAD_DIR, saved["note_filename"]), "rb").read()
        deed = open(os.path.join(brittco.UPLOAD_DIR, saved["dot_filename"]), "rb").read()
        copies = brittco.db().execute(
            "SELECT COUNT(*) AS n FROM documents WHERE filename=?",
            (saved["note_filename"],),
        ).fetchone()["n"]

    assert saved["status"] == "approved"
    assert saved["signatures"] == before_signatures
    assert saved["submitted_at"] == before["submitted_at"]
    assert saved["reviewed_by"] == before["reviewed_by"]
    assert saved["token"] == before["token"]
    assert saved["title_sent_at"] == "2026-10-02T09:00:00"
    assert payload["effective_date"] == "2026-10-15"
    assert payload["maturity_date"] == "2027-03-15"
    assert note != before_note
    assert b"October 15, 2026" in note
    assert b"March 15, 2027" in note
    assert b"February 1, 2027" not in note
    assert b"October 15, 2026" in deed
    assert copies == 1
    assert len(edits) == 1
    assert edits[0]["staff_name"] == "admin@brittcocapital.com"
    assert loan["start_date"] == "2026-10-15"
    assert loan["maturity_date"] == "2027-03-15"
    assert loan["next_payment_due"] == "2027-03-15"

    borrower = brittco.app.test_client().get(f"/closing/{saved['token']}")
    assert b"Submit and sign" not in borrower.data
    assert b"Signed and received" in borrower.data

    same = staff.post(
        f"/admin/closings/{row['id']}",
        data={
            "action": "dates",
            "effective_date": "2026-10-15",
            "maturity_date": "2027-03-15",
        },
        follow_redirects=True,
    )
    assert b"already on the documents" in same.data

    with brittco.app.app_context():
        brittco.db().execute(
            "UPDATE loans SET payment_frequency=?, next_payment_due=? WHERE id=?",
            ("Monthly", "2026-11-01", lid),
        )
        brittco.db().commit()
    staff.post(
        f"/admin/closings/{row['id']}",
        data={
            "action": "dates",
            "effective_date": "2026-10-15",
            "maturity_date": "2027-04-01",
        },
    )
    with brittco.app.app_context():
        loan = brittco.db().execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        saved = brittco.db().execute(
            "SELECT status, payload FROM closing_applications WHERE id=?", (row["id"],)
        ).fetchone()
    assert loan["next_payment_due"] == "2026-11-01"
    assert loan["maturity_date"] == "2027-04-01"
    assert loan["start_date"] == "2026-10-15"
    assert saved["status"] == "approved"
    assert closing_packet.load_json(saved["payload"], {})["effective_date"] == "2026-10-15"
