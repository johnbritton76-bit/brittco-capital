#!/usr/bin/env python3
"""Brittco Capital Inc — CRM + underwriting + borrower portal."""
import json
import os
import secrets
import smtplib
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.utils import formataddr
from functools import wraps

from io import BytesIO

from flask import (
    Flask, g, redirect, render_template, request, session, url_for, flash,
    send_from_directory, send_file,
)
from werkzeug.utils import secure_filename

from forms_catalog import DEFAULTS as FORM_DEFAULTS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else APP_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "brittco.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_UPLOADS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".doc", ".docx"}
CLOSING_DEFAULTS = [
    "Purchase contract",
    "Government ID / KYC",
    "Entity documents",
    "Title commitment",
    "Insurance binder",
    "Appraisal or BPO",
    "Scope of work (if rehab)",
    "Wiring instructions confirmed",
    "Closing statement",
    "Funds verified to close",
]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "brittco-local-dev-key-change-before-hosting")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE,
            password TEXT,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS borrowers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            entity_type TEXT,
            entity_name TEXT,
            email TEXT UNIQUE,
            phone TEXT,
            credit_score INTEGER,
            password TEXT,
            notes TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            years_at_address REAL,
            prev_address TEXT,
            prev_city TEXT,
            prev_state TEXT,
            prev_zip TEXT,
            own_or_rent TEXT,
            work_phone TEXT,
            ssn TEXT,
            dob TEXT,
            employer TEXT,
            occupation TEXT,
            employer_phone TEXT,
            employer_address TEXT,
            employer_city TEXT,
            employer_state TEXT,
            years_employed REAL,
            prev_employer TEXT,
            monthly_income REAL,
            liquid_assets REAL,
            credit_events TEXT,
            bank_name TEXT,
            bank_routing TEXT,
            bank_account TEXT,
            bank_account_type TEXT,
            ach_authorized INTEGER
        );
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY,
            borrower_id INTEGER,
            loan_type TEXT,
            address TEXT,
            purchase_price REAL,
            as_is_value REAL,
            arv REAL,
            rehab_budget REAL,
            loan_amount REAL,
            rate REAL,
            points REAL,
            term_months INTEGER,
            status TEXT,
            exit_strategy TEXT,
            notes TEXT,
            ltv_override_reason TEXT,
            created_at TEXT,
            acked INTEGER
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            deal_id INTEGER,
            borrower_id INTEGER,
            sender TEXT,
            body TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY,
            borrower_id INTEGER,
            deal_id INTEGER,
            loan_number TEXT,
            loan_type TEXT,
            property_address TEXT,
            original_principal REAL,
            current_balance REAL,
            rate REAL,
            points REAL,
            start_date TEXT,
            maturity_date TEXT,
            payment_type TEXT,
            payment_amount REAL,
            payment_frequency TEXT,
            next_payment_due TEXT,
            late_fee REAL,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY,
            loan_id INTEGER,
            paid_on TEXT,
            amount REAL,
            applied_to TEXT,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS credit_pulls (
            id INTEGER PRIMARY KEY,
            borrower_id INTEGER,
            pull_type TEXT,
            bureau TEXT,
            score INTEGER,
            status TEXT,
            vendor TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reminder_log (
            id INTEGER PRIMARY KEY,
            loan_id INTEGER,
            kind TEXT,
            sent_on TEXT,
            audience TEXT,
            body TEXT
        );
        CREATE TABLE IF NOT EXISTS investors (
            id INTEGER PRIMARY KEY,
            name TEXT,
            entity_name TEXT,
            email TEXT,
            phone TEXT,
            notes TEXT,
            ach_status TEXT,
            password TEXT,
            capital_available REAL,
            wire_bank TEXT,
            wire_routing TEXT,
            wire_account TEXT,
            wire_name TEXT,
            wire_further TEXT,
            wire_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS participations (
            id INTEGER PRIMARY KEY,
            loan_id INTEGER,
            investor_id INTEGER,
            amount REAL,
            investor_rate REAL,
            term_months INTEGER,
            extension_rate REAL,
            max_extensions INTEGER,
            extensions_used INTEGER,
            status TEXT,
            funded_on TEXT,
            notes TEXT,
            mgmt_fee_pct REAL
        );
        CREATE TABLE IF NOT EXISTS investor_accounts (
            id INTEGER PRIMARY KEY,
            investor_id INTEGER,
            nickname TEXT,
            kind TEXT,
            last4 TEXT,
            bank_name TEXT,
            is_borrowed INTEGER,
            apr REAL,
            status TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS participation_sources (
            id INTEGER PRIMARY KEY,
            participation_id INTEGER,
            account_id INTEGER,
            amount REAL
        );
        CREATE TABLE IF NOT EXISTS extensions (
            id INTEGER PRIMARY KEY,
            participation_id INTEGER,
            loan_id INTEGER,
            months INTEGER,
            rate REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS closing_items (
            id INTEGER PRIMARY KEY,
            deal_id INTEGER,
            title TEXT,
            done INTEGER,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            deal_id INTEGER,
            borrower_id INTEGER,
            filename TEXT,
            original_name TEXT,
            kind TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS distributions (
            id INTEGER PRIMARY KEY,
            payment_id INTEGER,
            loan_id INTEGER,
            investor_id INTEGER,
            investor_amount REAL,
            brittco_amount REAL,
            nate_amount REAL,
            kind TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ach_transfers (
            id INTEGER PRIMARY KEY,
            investor_id INTEGER,
            borrower_id INTEGER,
            loan_id INTEGER,
            direction TEXT,
            amount REAL,
            status TEXT,
            vendor TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY,
            token TEXT UNIQUE,
            kind TEXT,
            borrower_id INTEGER,
            name TEXT,
            email TEXT,
            phone TEXT,
            channel TEXT,
            created_at TEXT,
            used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS form_packets (
            id INTEGER PRIMARY KEY,
            token TEXT UNIQUE,
            form_key TEXT,
            borrower_id INTEGER,
            deal_id INTEGER,
            status TEXT,
            payload TEXT,
            created_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS form_templates (
            form_key TEXT PRIMARY KEY,
            title TEXT,
            blurb TEXT,
            fields_json TEXT,
            body TEXT,
            updated_at TEXT
        );
        """
    )
    if not c.execute("SELECT 1 FROM staff").fetchone():
        c.execute(
            "INSERT INTO staff (email, password, name) VALUES (?,?,?)",
            ("admin@brittcocapital.com", "brittco", "Brittco Staff"),
        )
        c.execute(
            """INSERT INTO borrowers
            (name, entity_type, entity_name, email, phone, credit_score, password, notes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                "Marcus Hale",
                "LLC",
                "Hale Holdings LLC",
                "marcus@example.com",
                "(407) 555-0142",
                712,
                "borrower",
                "Repeat flipper. Strong Orlando track record.",
            ),
        )
        c.execute(
            """INSERT INTO borrowers
            (name, entity_type, entity_name, email, phone, credit_score, password, notes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                "Sofia Ramirez",
                "Individual",
                "",
                "sofia@example.com",
                "(321) 555-0190",
                668,
                "borrower",
                "Credit below guideline. Needs holistic review.",
            ),
        )
        c.execute(
            """INSERT INTO deals
            (borrower_id, loan_type, address, purchase_price, as_is_value, arv,
             rehab_budget, loan_amount, rate, points, term_months, status,
             exit_strategy, notes, ltv_override_reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,
                "Fix and Flip",
                "1842 Pine Street, Orlando, FL",
                285000,
                280000,
                420000,
                45000,
                210000,
                11.5,
                2.0,
                12,
                "Underwriting",
                "Sell after rehab",
                "Solid neighborhood comps.",
                "",
                datetime.now().isoformat(timespec="minutes"),
            ),
        )
        c.execute(
            """INSERT INTO deals
            (borrower_id, loan_type, address, purchase_price, as_is_value, arv,
             rehab_budget, loan_amount, rate, points, term_months, status,
             exit_strategy, notes, ltv_override_reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                2,
                "Bridge",
                "90 Harbor Way, Tampa, FL",
                510000,
                500000,
                500000,
                0,
                390000,
                12.0,
                1.5,
                9,
                "Lead",
                "Refinance to DSCR in 6 months",
                "",
                "Strong cash-flowing asset; credit exception requested.",
                datetime.now().isoformat(timespec="minutes"),
            ),
        )
        c.commit()
    if not c.execute("SELECT 1 FROM loans").fetchone():
        today = date.today()
        c.execute(
            """INSERT INTO loans
            (borrower_id, deal_id, loan_number, loan_type, property_address,
             original_principal, current_balance, rate, points, start_date, maturity_date,
             payment_type, payment_amount, payment_frequency, next_payment_due, late_fee,
             status, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,
                1,
                "BC-1001",
                "Fix and Flip",
                "1842 Pine Street, Orlando, FL",
                210000,
                210000,
                11.5,
                2.0,
                (today - timedelta(days=60)).isoformat(),
                (today + timedelta(days=300)).isoformat(),
                "Interest only",
                2012.50,
                "Monthly",
                (today + timedelta(days=6)).isoformat(),
                150,
                "Current",
                "Sample funded loan for performance tracking.",
            ),
        )
        c.commit()
    if not c.execute("SELECT 1 FROM investors").fetchone():
        c.execute(
            "INSERT INTO investors (name, entity_name, email, phone, notes, ach_status, password) VALUES (?,?,?,?,?,?,?)",
            ("Elena Brooks", "Brooks Family Trust", "elena@example.com", "(407) 555-0177", "Repeat capital partner.", "Not connected", "investor"),
        )
        c.execute(
            "INSERT INTO investors (name, entity_name, email, phone, notes, ach_status, password) VALUES (?,?,?,?,?,?,?)",
            ("David Chen", "Chen Capital LLC", "david@example.com", "(813) 555-0114", "Prefers monthly interest ACH.", "Not connected", "investor"),
        )
        loan_row = c.execute("SELECT id, original_principal FROM loans ORDER BY id LIMIT 1").fetchone()
        if loan_row:
            half = (loan_row[1] or 210000) / 2
            c.execute(
                """INSERT INTO participations
                (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
                 max_extensions, extensions_used, status, funded_on, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (loan_row[0], 1, half, 10.0, 3, 2.0, 3, 0, "Funded", date.today().isoformat(), "Sample 50% · 3 mo at 10%"),
            )
            c.execute(
                """INSERT INTO participations
                (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
                 max_extensions, extensions_used, status, funded_on, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (loan_row[0], 2, half, 10.0, 3, 2.0, 3, 0, "Funded", date.today().isoformat(), "Sample 50% · 3 mo at 10%"),
            )
        c.commit()
    seed_demo_books(c)
    seed_transactional_sample(c)
    c.commit()
    c.close()


def seed_transactional_sample(c):
    if c.execute("SELECT 1 FROM loans WHERE loan_number=?", ("BC-TX-1001",)).fetchone():
        return
    if not c.execute("SELECT 1 FROM borrowers WHERE email=?", ("jordan@example.com",)).fetchone():
        c.execute(
            """INSERT INTO borrowers
            (name, entity_type, entity_name, email, phone, credit_score, password, notes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                "Jordan Pike",
                "LLC",
                "Pike Close LLC",
                "jordan@example.com",
                "(813) 555-0177",
                724,
                "borrower",
                "Sample transactional borrower. 7-day close.",
            ),
        )
    b = c.execute("SELECT id FROM borrowers WHERE email=?", ("jordan@example.com",)).fetchone()
    if not c.execute("SELECT 1 FROM investors WHERE email=?", ("apex@example.com",)).fetchone():
        c.execute(
            """INSERT INTO investors
            (name, entity_name, email, phone, notes, ach_status, password, capital_available)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                "Apex Short-Term Fund",
                "Apex Short-Term Fund LLC",
                "apex@example.com",
                "(813) 555-0108",
                "Sample lender for transactional loans.",
                "Not connected",
                "investor",
                400000,
            ),
        )
    inv = c.execute("SELECT id FROM investors WHERE email=?", ("apex@example.com",)).fetchone()
    if not b or not inv:
        return
    bid, iid = b[0], inv[0]
    today = date.today()
    c.execute(
        """INSERT INTO deals
        (borrower_id, loan_type, address, purchase_price, as_is_value, arv,
         rehab_budget, loan_amount, rate, points, term_months, status,
         exit_strategy, notes, ltv_override_reason, created_at, acked)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            bid,
            "Transactional Loan",
            "910 Harbor Court, Tampa, FL",
            310000,
            310000,
            None,
            0,
            275000,
            None,
            3.0,
            0,
            "Funded",
            "Close within 7 days",
            "Sample transactional loan. 3% flat fee for up to 7 days.",
            "",
            datetime.now().isoformat(timespec="minutes"),
            1,
        ),
    )
    deal_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    start = today - timedelta(days=2)
    maturity = start + timedelta(days=7)
    c.execute(
        """INSERT INTO loans
        (borrower_id, deal_id, loan_number, loan_type, property_address,
         original_principal, current_balance, rate, points, start_date, maturity_date,
         payment_type, payment_amount, payment_frequency, next_payment_due, late_fee,
         status, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            bid,
            deal_id,
            "BC-TX-1001",
            "Transactional Loan",
            "910 Harbor Court, Tampa, FL",
            275000,
            275000,
            None,
            3.0,
            start.isoformat(),
            maturity.isoformat(),
            "Flat fee",
            8250,
            "One time",
            maturity.isoformat(),
            0,
            "Current",
            "Sample transactional loan. 3% flat fee ($8,250) due at payoff within 7 days. Extensions negotiable.",
        ),
    )
    lid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute(
        """INSERT INTO participations
        (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
         max_extensions, extensions_used, status, funded_on, notes, mgmt_fee_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lid,
            iid,
            275000,
            3.0,
            0,
            0,
            0,
            0,
            "Funded",
            start.isoformat(),
            "Sample lender 100% of transactional loan. 3% / 7 days.",
            25,
        ),
    )


def seed_demo_books(c):
    """Add multi-deal sample P&L so both demo investors have YTD and all-time numbers."""
    if c.execute("SELECT COUNT(*) FROM distributions").fetchone()[0] > 0:
        return
    elena = c.execute("SELECT id FROM investors WHERE email=?", ("elena@example.com",)).fetchone()
    david = c.execute("SELECT id FROM investors WHERE email=?", ("david@example.com",)).fetchone()
    if not elena or not david:
        return
    e_id, d_id = elena[0], david[0]
    today = date.today()
    last_year = today.replace(year=today.year - 1)
    existing = c.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    if existing < 3:
        extras = [
            (
                1, 1, "BC-1002", "Bridge", "412 Magnolia Ave, Winter Park, FL",
                180000, 0, 12.0, 1.5,
                (last_year - timedelta(days=200)).isoformat(),
                (last_year + timedelta(days=10)).isoformat(),
                "Interest only", 1800, "Monthly", last_year.isoformat(), 0, "Paid Off",
                "Demo paid-off bridge.",
            ),
            (
                2, 2, "BC-1003", "Hard Money", "77 Lakeview Dr, Tampa, FL",
                150000, 150000, 12.5, 2.0,
                (today - timedelta(days=80)).isoformat(),
                (today + timedelta(days=40)).isoformat(),
                "Interest only", 1562.50, "Monthly", (today + timedelta(days=12)).isoformat(), 125,
                "Current", "Demo current hard-money loan.",
            ),
        ]
        for row in extras:
            c.execute(
                """INSERT INTO loans
                (borrower_id, deal_id, loan_number, loan_type, property_address,
                 original_principal, current_balance, rate, points, start_date, maturity_date,
                 payment_type, payment_amount, payment_frequency, next_payment_due, late_fee,
                 status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
    loans = list(c.execute("SELECT id, loan_number, original_principal FROM loans ORDER BY id").fetchall())
    if len(loans) < 1:
        return

    def part(loan_id, investor_id, amount, rate=10.0, term=3, status="Funded"):
        already = c.execute(
            "SELECT 1 FROM participations WHERE loan_id=? AND investor_id=?",
            (loan_id, investor_id),
        ).fetchone()
        if already:
            return
        c.execute(
            """INSERT INTO participations
            (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
             max_extensions, extensions_used, status, funded_on, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (loan_id, investor_id, amount, rate, term, 2.0, 3, 0, status, today.isoformat(), "Demo book"),
        )

    # Split first loan if present
    lid1 = loans[0][0]
    amt1 = (loans[0][2] or 210000) / 2
    part(lid1, e_id, amt1)
    part(lid1, d_id, amt1)
    if len(loans) > 1:
        lid2 = loans[1][0]
        part(lid2, e_id, 120000, 10.0, 3, "Termed")
        part(lid2, d_id, 60000, 10.0, 3, "Termed")
    if len(loans) > 2:
        lid3 = loans[2][0]
        part(lid3, e_id, 50000, 10.0, 3)
        part(lid3, d_id, 100000, 10.0, 3)

    def pay(loan_id, when, amount, kind):
        cur = c.execute(
            "INSERT INTO payments (loan_id, paid_on, amount, applied_to, note) VALUES (?,?,?,?,?)",
            (loan_id, when, amount, kind, "Demo sample"),
        )
        return cur.lastrowid

    def dist(pay_id, loan_id, investor_id, inv_amt, brit_amt, kind, when):
        c.execute(
            """INSERT INTO distributions
            (payment_id, loan_id, investor_id, investor_amount, brittco_amount, kind, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (pay_id, loan_id, investor_id, inv_amt, brit_amt, kind, when),
        )

    # Last-year activity on loan 2 (or loan 1)
    ly_loan = loans[1][0] if len(loans) > 1 else lid1
    for i, day in enumerate([40, 70, 100]):
        when = (last_year - timedelta(days=day)).isoformat()
        pid = pay(ly_loan, when, 2700, "Interest")
        dist(pid, ly_loan, e_id, 1800, 300, "Interest", when)
        dist(pid, ly_loan, d_id, 600, 0, "Interest", when)
    payoff = last_year.isoformat()
    pid = pay(ly_loan, payoff, 180000, "Principal")
    dist(pid, ly_loan, e_id, 120000, 0, "Principal", payoff)
    dist(pid, ly_loan, d_id, 60000, 0, "Principal", payoff)

    # This-year activity on loan 1
    for i, day in enumerate([75, 45, 15]):
        when = (today - timedelta(days=day)).isoformat()
        pid = pay(lid1, when, 2012.50, "Interest")
        dist(pid, lid1, e_id, 875, 131.25, "Interest", when)
        dist(pid, lid1, d_id, 875, 131.25, "Interest", when)

    if len(loans) > 2:
        lid3 = loans[2][0]
        for day in [50, 20]:
            when = (today - timedelta(days=day)).isoformat()
            pid = pay(lid3, when, 1562.50, "Interest")
            dist(pid, lid3, e_id, 417, 104, "Interest", when)
            dist(pid, lid3, d_id, 833, 208, "Interest", when)


# Initialize database when the app starts (works with gunicorn on Render)
init_db()
try:
    _c = sqlite3.connect(DB_PATH)
    ach_cols = [r[1] for r in _c.execute("PRAGMA table_info(ach_transfers)")]
    if ach_cols and "borrower_id" not in ach_cols:
        _c.execute("ALTER TABLE ach_transfers ADD COLUMN borrower_id INTEGER")
    inv_cols = [r[1] for r in _c.execute("PRAGMA table_info(investors)")]
    if inv_cols and "password" not in inv_cols:
        _c.execute("ALTER TABLE investors ADD COLUMN password TEXT")
        _c.execute("UPDATE investors SET password='investor' WHERE password IS NULL OR password=''")
    p_cols = [r[1] for r in _c.execute("PRAGMA table_info(participations)")]
    for col, spec in [
        ("term_months", "INTEGER"),
        ("extension_rate", "REAL"),
        ("max_extensions", "INTEGER"),
        ("extensions_used", "INTEGER"),
    ]:
        if p_cols and col not in p_cols:
            _c.execute(f"ALTER TABLE participations ADD COLUMN {col} {spec}")
    if p_cols and "mgmt_fee_pct" not in p_cols:
        _c.execute("ALTER TABLE participations ADD COLUMN mgmt_fee_pct REAL")
        _c.execute("UPDATE participations SET mgmt_fee_pct=25 WHERE mgmt_fee_pct IS NULL")
    dist_cols = [r[1] for r in _c.execute("PRAGMA table_info(distributions)")]
    if dist_cols and "nate_amount" not in dist_cols:
        _c.execute("ALTER TABLE distributions ADD COLUMN nate_amount REAL")
    _c.execute(
        """CREATE TABLE IF NOT EXISTS form_packets (
            id INTEGER PRIMARY KEY,
            token TEXT UNIQUE,
            form_key TEXT,
            borrower_id INTEGER,
            deal_id INTEGER,
            status TEXT,
            payload TEXT,
            created_at TEXT,
            completed_at TEXT
        )"""
    )
    _c.execute(
        """CREATE TABLE IF NOT EXISTS investor_accounts (
            id INTEGER PRIMARY KEY,
            investor_id INTEGER,
            nickname TEXT,
            kind TEXT,
            last4 TEXT,
            bank_name TEXT,
            is_borrowed INTEGER,
            apr REAL,
            status TEXT,
            created_at TEXT
        )"""
    )
    _c.execute(
        """CREATE TABLE IF NOT EXISTS participation_sources (
            id INTEGER PRIMARY KEY,
            participation_id INTEGER,
            account_id INTEGER,
            amount REAL
        )"""
    )
    _c.execute(
        """CREATE TABLE IF NOT EXISTS form_templates (
            form_key TEXT PRIMARY KEY,
            title TEXT,
            blurb TEXT,
            fields_json TEXT,
            body TEXT,
            updated_at TEXT
        )"""
    )
    _c.commit()
    deal_cols = [r[1] for r in _c.execute("PRAGMA table_info(deals)")]
    if deal_cols and "acked" not in deal_cols:
        _c.execute("ALTER TABLE deals ADD COLUMN acked INTEGER")
        _c.execute("UPDATE deals SET acked=1 WHERE acked IS NULL")
    b_cols = [r[1] for r in _c.execute("PRAGMA table_info(borrowers)")]
    for col, spec in [
        ("address", "TEXT"),
        ("city", "TEXT"),
        ("state", "TEXT"),
        ("zip", "TEXT"),
        ("years_at_address", "REAL"),
        ("prev_address", "TEXT"),
        ("prev_city", "TEXT"),
        ("prev_state", "TEXT"),
        ("prev_zip", "TEXT"),
        ("own_or_rent", "TEXT"),
        ("work_phone", "TEXT"),
        ("ssn", "TEXT"),
        ("dob", "TEXT"),
        ("employer", "TEXT"),
        ("occupation", "TEXT"),
        ("employer_phone", "TEXT"),
        ("employer_address", "TEXT"),
        ("employer_city", "TEXT"),
        ("employer_state", "TEXT"),
        ("years_employed", "REAL"),
        ("prev_employer", "TEXT"),
        ("monthly_income", "REAL"),
        ("liquid_assets", "REAL"),
        ("credit_events", "TEXT"),
        ("bank_name", "TEXT"),
        ("bank_routing", "TEXT"),
        ("bank_account", "TEXT"),
        ("bank_account_type", "TEXT"),
        ("ach_authorized", "INTEGER"),
    ]:
        if b_cols and col not in b_cols:
            _c.execute(f"ALTER TABLE borrowers ADD COLUMN {col} {spec}")
    inv_cols = [r[1] for r in _c.execute("PRAGMA table_info(investors)")]
    for col, spec in [
        ("capital_available", "REAL"),
        ("wire_bank", "TEXT"),
        ("wire_routing", "TEXT"),
        ("wire_account", "TEXT"),
        ("wire_name", "TEXT"),
        ("wire_further", "TEXT"),
        ("wire_notes", "TEXT"),
    ]:
        if inv_cols and col not in inv_cols:
            _c.execute(f"ALTER TABLE investors ADD COLUMN {col} {spec}")
    _c.execute(
        "UPDATE investors SET capital_available=250000 WHERE email='elena@example.com' AND (capital_available IS NULL OR capital_available=0)"
    )
    _c.execute(
        "UPDATE investors SET capital_available=150000 WHERE email='david@example.com' AND (capital_available IS NULL OR capital_available=0)"
    )
    _c.commit()
    _c.close()
except sqlite3.Error:
    pass


def staff_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("staff_id"):
            return redirect(url_for("login"))
        return fn(*a, **k)

    return wrap


@app.context_processor
def inject_new_apps():
    if not session.get("staff_id"):
        return {"new_apps": []}
    try:
        rows = db().execute(
            """SELECT d.id, d.address, b.name AS borrower_name
               FROM deals d JOIN borrowers b ON b.id=d.borrower_id
               WHERE d.status='Application' AND COALESCE(d.acked,0)=0
               ORDER BY d.id DESC"""
        ).fetchall()
        return {"new_apps": [dict(r) for r in rows]}
    except sqlite3.Error:
        return {"new_apps": []}


def ensure_closing_list(deal_id):
    exists = db().execute("SELECT 1 FROM closing_items WHERE deal_id=?", (deal_id,)).fetchone()
    if exists:
        return
    for title in CLOSING_DEFAULTS:
        db().execute(
            "INSERT INTO closing_items (deal_id, title, done, notes) VALUES (?,?,0,'')",
            (deal_id, title),
        )
    db().commit()


def save_uploads(deal_id, borrower_id, files):
    saved = 0
    for f in files:
        if not f or not f.filename:
            continue
        name = secure_filename(f.filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_UPLOADS:
            continue
        stored = f"{deal_id}_{int(datetime.now().timestamp())}_{saved}_{name}"
        f.save(os.path.join(UPLOAD_DIR, stored))
        db().execute(
            """INSERT INTO documents (deal_id, borrower_id, filename, original_name, kind, created_at)
               VALUES (?,?,?,?,?,?)""",
            (deal_id, borrower_id, stored, name, "Upload", datetime.now().isoformat(timespec="minutes")),
        )
        saved += 1
    if saved:
        db().commit()
    return saved


def investor_books(iid):
    year = str(date.today().year)
    prior = str(date.today().year - 1)
    parts = db().execute(
        "SELECT * FROM participations WHERE investor_id=?", (iid,)
    ).fetchall()
    dists = db().execute(
        "SELECT * FROM distributions WHERE investor_id=?", (iid,)
    ).fetchall()
    deployed = sum(money(p["amount"]) for p in parts if p["status"] not in ("Closed",))
    all_int = sum(money(d["investor_amount"]) for d in dists if (d["kind"] or "") != "Principal")
    all_prin = sum(money(d["investor_amount"]) for d in dists if (d["kind"] or "") == "Principal")
    ytd_int = sum(
        money(d["investor_amount"])
        for d in dists
        if (d["kind"] or "") != "Principal" and str(d["created_at"] or "").startswith(year)
    )
    ytd_prin = sum(
        money(d["investor_amount"])
        for d in dists
        if (d["kind"] or "") == "Principal" and str(d["created_at"] or "").startswith(year)
    )
    by_loan = {}
    for p in parts:
        slot = by_loan.setdefault(
            p["loan_id"],
            {
                "capital": 0.0,
                "interest": 0.0,
                "principal": 0.0,
                "ytd_int": 0.0,
                "ye_int": 0.0,
                "nate": 0.0,
                "ytd_nate": 0.0,
                "math": None,
                "lines": [],
            },
        )
        slot["capital"] += money(p["amount"])
        if slot["math"] is None:
            slot["math"] = participation_math(p)
    for d in dists:
        slot = by_loan.setdefault(
            d["loan_id"],
            {
                "capital": 0.0,
                "interest": 0.0,
                "principal": 0.0,
                "ytd_int": 0.0,
                "ye_int": 0.0,
                "nate": 0.0,
                "ytd_nate": 0.0,
                "math": None,
                "lines": [],
            },
        )
        amt = money(d["investor_amount"])
        try:
            stored_nate = money(d["nate_amount"])
        except (KeyError, IndexError):
            stored_nate = 0.0
        line = dict(d)
        # Monthly lines are investor cash. Any old Nate split is added back into profit.
        if (d["kind"] or "") == "Principal":
            slot["principal"] += amt
            line["nate_amount"] = 0
        else:
            profit_line = amt + stored_nate
            line["investor_amount"] = profit_line
            line["nate_amount"] = 0
            slot["interest"] += profit_line
            when = str(d["created_at"] or "")
            if when.startswith(year):
                slot["ytd_int"] += profit_line
            if when.startswith(prior):
                slot["ye_int"] += profit_line
        slot["lines"].append(line)
    rows = []
    w_ann = 0.0
    w_cap = 0.0
    for lid, s in by_loan.items():
        loan = db().execute("SELECT loan_number, property_address, status FROM loans WHERE id=?", (lid,)).fetchone()
        if not loan:
            continue
        cap = s["capital"] or 0
        gross = s["interest"]
        ytd_gross = s["ytd_int"]
        m = s["math"] or {}
        fee = m.get("fee", 25.0)
        status = (loan["status"] or "")
        basis_back = s["principal"] >= cap - 0.5 or status in ("Paid Off", "Termed", "Closed")
        accrued = round(gross * fee / 100.0, 2) if fee else 0.0
        nate = accrued if basis_back else 0.0
        ytd_nate = round(ytd_gross * fee / 100.0, 2) if basis_back else 0.0
        ye_gross = s.get("ye_int") or 0.0
        ye_nate = round(ye_gross * fee / 100.0, 2) if basis_back else 0.0
        net = gross - nate
        ytd_net = ytd_gross - ytd_nate
        ye_net = ye_gross - ye_nate
        ror = (net / cap * 100) if cap else 0
        ytd_ror = (ytd_net / cap * 100) if cap else 0
        gross_ror = (gross / cap * 100) if cap else 0
        ann_rate = m.get("annualized_net") if basis_back else (m.get("annualized_actual") or m.get("annualized_initial") or 0)
        if basis_back:
            ann_rate = m.get("annualized_net") or 0
        else:
            ann_rate = m.get("annualized_actual") or m.get("annualized_initial") or 0
        ann_gross = m.get("annualized_actual") or m.get("annualized_initial") or 0
        ann_dollars = cap * (ann_rate or 0) / 100
        w_ann += (ann_rate or 0) * cap
        w_cap += cap
        if basis_back:
            story = (
                f"You invested ${cap:,.0f} in this loan and have your basis back. "
                f"Gross profit is ${gross:,.2f}. Nate Holland’s {fee:.1f}% fee is now due: ${nate:,.2f}. "
                f"Your net profit is ${net:,.2f} ({ror:.1f}% / {ann_rate or 0:.1f}% annualized)."
            )
        else:
            story = (
                f"You invested ${cap:,.0f} in this loan. You have received ${gross:,.2f} in interest income so far "
                f"and still have ${max(0.0, cap - s['principal']):,.0f} of capital invested in this deal. "
                f"Nate Holland’s {fee:.1f}% fee (${accrued:,.2f}) is estimated only — it is not payable "
                f"until your basis is paid off and a profit is realized."
            )
        rows.append(
            {
                "loan_id": lid,
                "loan_number": loan["loan_number"],
                "property": loan["property_address"],
                "status": status,
                "capital": cap,
                "interest": net,
                "principal_back": s["principal"],
                "still_out": max(0.0, cap - s["principal"]),
                "ytd_int": ytd_net,
                "ye_int": ye_net,
                "ye_nate": ye_nate,
                "profit": net,
                "gross_profit": gross,
                "nate_fee": nate,
                "nate_accrued": accrued,
                "nate_due": basis_back,
                "ytd_nate": ytd_nate,
                "fee": fee,
                "ror": ror,
                "ytd_ror": ytd_ror,
                "gross_ror": gross_ror,
                "ann_rate": ann_rate or 0,
                "ann_gross": ann_gross or 0,
                "ann_dollars": ann_dollars,
                "term": m.get("term") or 0,
                "base": m.get("base") or 0,
                "ext_rate": m.get("ext_rate") or 0,
                "used": m.get("used") or 0,
                "lines": s.get("lines") or [],
                "story": story,
            }
        )
    net_all = sum(r["profit"] for r in rows)
    net_ytd = sum(r["ytd_int"] for r in rows)
    net_ye = sum(r.get("ye_int") or 0 for r in rows)
    nate_all = sum(r["nate_fee"] for r in rows)
    nate_ytd = sum(r["ytd_nate"] for r in rows)
    nate_ye = sum(r.get("ye_nate") or 0 for r in rows)
    all_ror = (net_all / deployed * 100) if deployed else 0
    ytd_ror = (net_ytd / deployed * 100) if deployed else 0
    ye_ror = (net_ye / deployed * 100) if deployed else 0
    ann_rate = (w_ann / w_cap) if w_cap else 0
    return {
        "deployed": deployed,
        "all_int": net_all,
        "all_prin": all_prin,
        "ytd_int": net_ytd,
        "ytd_prin": ytd_prin,
        "still_out": max(0.0, deployed - all_prin),
        "all_profit": net_all,
        "ytd_profit": net_ytd,
        "ye_profit": net_ye,
        "ye_ror": ye_ror,
        "nate_all": nate_all,
        "nate_ytd": nate_ytd,
        "nate_ye": nate_ye,
        "all_ror": all_ror,
        "ytd_ror": ytd_ror,
        "ye_year": prior,
        "ann_rate": ann_rate,
        "ann_dollars": deployed * ann_rate / 100 if deployed else 0,
        "rows": rows,
        "year": year,
    }


ACCOUNT_KINDS = [
    "Checking",
    "Savings",
    "HELOC",
    "Line of credit",
    "Credit card",
    "Other",
]


def investor_funding_accounts(iid, include_archived=False):
    q = "SELECT * FROM investor_accounts WHERE investor_id=?"
    args = [iid]
    if not include_archived:
        q += " AND COALESCE(status,'Active') != 'Archived'"
    q += " ORDER BY nickname"
    return db().execute(q, args).fetchall()


def carry_days(loan, part):
    start = parse_date(row_val(part, "funded_on")) or parse_date(row_val(loan, "start_date"))
    if not start:
        start = date.today()
    status = row_val(loan, "status")
    if status in ("Paid Off", "Termed", "Closed", "Sold"):
        end = parse_date(row_val(loan, "maturity_date")) or date.today()
    else:
        end = date.today()
    return max(1, (end - start).days)


def attach_investor_carry(iid, books):
    """Investor-only cost-of-funds overlay. Does not change Brittco books."""
    accs = {a["id"]: a for a in investor_funding_accounts(iid, include_archived=True)}
    parts = db().execute(
        """SELECT p.*, l.start_date, l.maturity_date, l.status AS loan_status, l.id AS lid
           FROM participations p JOIN loans l ON l.id=p.loan_id
           WHERE p.investor_id=?""",
        (iid,),
    ).fetchall()
    by_loan = {}
    total_carry = 0.0
    for p in parts:
        sources = db().execute(
            """SELECT s.*, a.nickname, a.kind, a.last4, a.is_borrowed, a.apr, a.bank_name
               FROM participation_sources s
               JOIN investor_accounts a ON a.id=s.account_id
               WHERE s.participation_id=?""",
            (p["id"],),
        ).fetchall()
        days = carry_days(p, p)
        carry = 0.0
        tagged = []
        for s in sources:
            amt = money(s["amount"]) or money(p["amount"])
            apr = money(s["apr"]) if s["is_borrowed"] else 0.0
            cost = round(amt * (apr / 100.0) * (days / 365.0), 2) if apr else 0.0
            carry += cost
            tagged.append(
                {
                    "id": s["id"],
                    "nickname": s["nickname"],
                    "kind": s["kind"],
                    "last4": s["last4"],
                    "amount": amt,
                    "apr": apr,
                    "days": days,
                    "cost": cost,
                    "borrowed": bool(s["is_borrowed"]),
                }
            )
        total_carry += carry
        by_loan.setdefault(p["lid"] if "lid" in p.keys() else p["loan_id"], {"carry": 0.0, "sources": []})
        lid = p["loan_id"]
        slot = by_loan.setdefault(lid, {"carry": 0.0, "sources": []})
        slot["carry"] += carry
        slot["sources"].extend(tagged)
        slot["days"] = days
    for r in books.get("rows") or []:
        extra = by_loan.get(r["loan_id"]) or {"carry": 0.0, "sources": [], "days": 0}
        r["carry"] = extra["carry"]
        r["sources"] = extra["sources"]
        r["net_after_carry"] = (r.get("profit") or 0) - extra["carry"]
        cap = r.get("capital") or 0
        r["net_after_carry_ror"] = (r["net_after_carry"] / cap * 100) if cap else 0
    books["carry_all"] = total_carry
    books["net_after_carry"] = (books.get("all_profit") or 0) - total_carry
    deployed = books.get("deployed") or 0
    books["net_after_carry_ror"] = (books["net_after_carry"] / deployed * 100) if deployed else 0
    books["accounts"] = [dict(a) for a in investor_funding_accounts(iid)]
    return books


def money_txt(n):
    return f"${money(n):,.2f}"


def investor_statement_pdf(inv, books, include_carry=False):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable, PageBreak,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"Brittco Capital statement — {inv['name']}",
        author="Brittco Capital Inc",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("T", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#16324f"), spaceAfter=2)
    sub = ParagraphStyle("S", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#5b6b7a"), spaceAfter=6)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("SM", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#334"))
    foot = ParagraphStyle("F", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6b7a88"), alignment=TA_CENTER)
    story = []
    logo = os.path.join(APP_DIR, "static", "logo.jpg")
    head = []
    if os.path.exists(logo):
        img = Image(logo, width=1.35 * inch, height=0.55 * inch)
        head.append([
            img,
            Paragraph("Investor financial statement", title),
        ])
        story.append(Table(head, colWidths=[1.6 * inch, 5.4 * inch], hAlign="LEFT"))
    else:
        story.append(Paragraph("Brittco Capital Inc", title))
        story.append(Paragraph("Investor financial statement", sub))
    today = date.today().isoformat()
    story.append(Paragraph("Your Bridge to Building Wealth", sub))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c5d3e0"), spaceAfter=8))
    story.append(Paragraph(f"<b>{inv['name']}</b>", body))
    if inv["entity_name"]:
        story.append(Paragraph(inv["entity_name"], small))
    story.append(Paragraph(f"{inv['email'] or ''} &nbsp; {inv['phone'] or ''}", small))
    story.append(Paragraph(f"Statement date {today}", small))
    story.append(Spacer(1, 10))

    year = books.get("year") or str(date.today().year)
    prior = books.get("ye_year") or str(date.today().year - 1)
    summary = [
        ["", "YTD " + year, "Year-end " + prior, "All time"],
        ["Net profit to investor", money_txt(books["ytd_profit"]), money_txt(books["ye_profit"]), money_txt(books["all_profit"])],
        ["Rate of return", f"{books['ytd_ror']:.1f}%", f"{books['ye_ror']:.1f}%", f"{books['all_ror']:.1f}%"],
        ["Nate Holland fee (realized)", money_txt(books["nate_ytd"]), money_txt(books.get("nate_ye") or 0), money_txt(books["nate_all"])],
        ["Capital still in deals", money_txt(books["still_out"]), "—", money_txt(books["still_out"])],
        ["Annualized book rate", f"{books['ann_rate']:.1f}%", "—", f"{books['ann_rate']:.1f}%"],
    ]
    t = Table(summary, colWidths=[2.2 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f0f7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16324f")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0dbe6")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Nate is paid 25% of each loan’s realized net profit. "
        "Year-end is the prior calendar year. YTD is this calendar year to date.",
        small,
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Deal-by-deal detail", ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#16324f"))))

    header = ["Deal", "Capital", "Gross", "Nate fee", "Net all-time", "YTD net", "Year-end net", "Net rate", "Ann."]
    data = [header]
    for r in books["rows"]:
        data.append([
            Paragraph(f"{r['loan_number']}<br/><font size='7'>{r['property'] or ''}</font>", small),
            money_txt(r["capital"]),
            money_txt(r["gross_profit"]),
            money_txt(r["nate_fee"]),
            money_txt(r["profit"]),
            money_txt(r["ytd_int"]),
            money_txt(r.get("ye_int") or 0),
            f"{r['ror']:.1f}%",
            f"{r['ann_rate']:.1f}%",
        ])
    data.append([
        "Totals",
        money_txt(books["deployed"]),
        "",
        money_txt(books["nate_all"]),
        money_txt(books["all_profit"]),
        money_txt(books["ytd_profit"]),
        money_txt(books["ye_profit"]),
        f"{books['all_ror']:.1f}%",
        f"{books['ann_rate']:.1f}%",
    ])
    widths = [1.35*inch, 0.72*inch, 0.68*inch, 0.68*inch, 0.82*inch, 0.72*inch, 0.82*inch, 0.58*inch, 0.52*inch]
    dt = Table(data, colWidths=widths, repeatRows=1)
    dt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f0f7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5d3e0")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(dt)
    story.append(Spacer(1, 14))
    story.append(Paragraph("Activity detail", ParagraphStyle("H3", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#16324f"))))
    for r in books["rows"]:
        story.append(Paragraph(f"<b>{r['loan_number']}</b> — {r['property'] or ''} ({r['status']})", body))
        story.append(Paragraph(r.get("story") or "", small))
        if r.get("lines"):
            lines = [["Date", "Type", "To investor", "Note"]]
            for line in r["lines"]:
                lines.append([
                    str(line.get("created_at") or "")[:16],
                    line.get("kind") or "",
                    money_txt(line.get("investor_amount") or 0),
                    "",
                ])
            lt = Table(lines, colWidths=[1.4*inch, 1.2*inch, 1.2*inch, 3.1*inch])
            lt.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f7fb")),
                ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#d7e0ea")),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(lt)
        story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#c5d3e0"), spaceBefore=6, spaceAfter=6))
    story.append(Paragraph(
        "Prepared by Brittco Capital Inc for the investor named above. "
        "Figures follow realized net profit after Nate Holland’s fee on paid-off loans. "
        "This statement is for account review and is not a tax form.",
        foot,
    ))
    if include_carry:
        story.append(PageBreak())
        story.append(Paragraph("Personal cost of funds — for your records only", title))
        story.append(Paragraph(
            "Brittco Capital does not keep this worksheet. Carry cost is estimated from the accounts and rates you entered "
            f"(drawn × rate ÷ 365 × days). Estimated carry ${money(books.get('carry_all') or 0):,.2f}. "
            f"Net after carry ${money(books.get('net_after_carry') or 0):,.2f}.",
            small,
        ))
        rows = [["Deal", "Funded from", "Carry cost", "Net after carry"]]
        for r in books.get("rows") or []:
            src = ", ".join(
                f"{s['nickname']} {s['kind']} {money_txt(s['amount'])}"
                for s in (r.get("sources") or [])
            ) or "Not tagged"
            rows.append([
                Paragraph(f"{r['loan_number']}<br/><font size='7'>{r.get('property') or ''}</font>", small),
                Paragraph(src, small),
                money_txt(r.get("carry") or 0),
                money_txt(r.get("net_after_carry") or r.get("profit") or 0),
            ])
        ct = Table(rows, colWidths=[1.8*inch, 2.6*inch, 1.2*inch, 1.4*inch], repeatRows=1)
        ct.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5d3e0")),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(Spacer(1, 8))
        story.append(ct)
    doc.build(story)
    return buf.getvalue()


def borrower_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("borrower_id"):
            return redirect(url_for("portal_login"))
        return fn(*a, **k)

    return wrap


def investor_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("investor_id"):
            return redirect(url_for("investor_login"))
        return fn(*a, **k)

    return wrap


def annualized(rate_pct, months):
    months = money(months)
    if months <= 0:
        return None
    return money(rate_pct) * (12.0 / months)


def annualized_days(rate_pct, days):
    days = money(days)
    if days <= 0:
        return None
    return money(rate_pct) * (365.0 / days)


def _row_months(row):
    if row is None:
        return 0
    try:
        if "term_months" not in row.keys():
            return 0
        return int(row["term_months"] or 0)
    except Exception:
        return 0


def loan_term_days(loan, p=None):
    loan_type = row_val(loan, "loan_type") if loan is not None else ""
    months = _row_months(p) or _row_months(loan)
    if loan_type == "Transactional Loan" and not months:
        start = parse_date(row_val(loan, "start_date"))
        end = parse_date(row_val(loan, "maturity_date"))
        if start and end and (end - start).days >= 2:
            return (end - start).days
        return 7
    if months:
        return max(1, months * 30)
    start = parse_date(row_val(loan, "start_date")) if loan is not None else None
    end = parse_date(row_val(loan, "maturity_date")) if loan is not None else None
    if start and end and (end - start).days >= 2:
        return (end - start).days
    return 90


def participation_returns(loan, p, dists_for_investor=None):
    math = participation_math(p)
    cap = money(p["amount"])
    days = loan_term_days(loan, p)
    closed = (loan["status"] or "") in ("Paid Off", "Termed", "Closed", "Sold")
    rate = money(p["investor_rate"])
    if not rate:
        rate = money(loan["points"]) or money(loan["rate"])
    fee = math["fee"]
    est_gross = cap * rate / 100.0
    est_nate = est_gross * fee / 100.0
    est_net = est_gross - est_nate
    est_ann = annualized_days(rate, days) or 0
    est_ann_net = annualized_days(rate * (1 - fee / 100.0), days) or 0
    actual_gross = 0.0
    if dists_for_investor:
        actual_gross = sum(
            money(d["investor_amount"])
            for d in dists_for_investor
            if (d["kind"] or "") != "Principal"
        )
    actual_nate = actual_gross * fee / 100.0 if closed else 0.0
    actual_net = actual_gross - actual_nate
    actual_rate = (actual_gross / cap * 100.0) if cap else 0.0
    actual_ann = annualized_days(actual_rate, days) or 0
    actual_ann_net = annualized_days((actual_net / cap * 100.0) if cap else 0, days) or 0
    return {
        **math,
        "days": days,
        "closed": closed,
        "rate": rate,
        "est_gross": est_gross,
        "est_nate": est_nate,
        "est_net": est_net,
        "est_ann": est_ann,
        "est_ann_net": est_ann_net,
        "actual_gross": actual_gross,
        "actual_nate": actual_nate,
        "actual_net": actual_net,
        "actual_ann": actual_ann,
        "actual_ann_net": actual_ann_net,
        "show_actual": closed,
    }


def participation_math(p):
    term = p["term_months"] or 0
    used = p["extensions_used"] or 0
    base = money(p["investor_rate"])
    ext_rate = money(p["extension_rate"])
    ext_return = used * ext_rate
    total_return = base + ext_return
    total_months = term + used
    try:
        fee = p["mgmt_fee_pct"]
        fee = 25.0 if fee is None else money(fee)
    except (KeyError, IndexError):
        fee = 25.0
    keep = max(0.0, 1.0 - fee / 100.0)
    net_base = base * keep
    net_total = total_return * keep
    return {
        "term": term,
        "used": used,
        "max_ext": p["max_extensions"] or 0,
        "base": base,
        "ext_rate": ext_rate,
        "ext_return": ext_return,
        "total_return": total_return,
        "total_months": total_months,
        "fee": fee,
        "net_base": net_base,
        "annualized_initial": annualized(base, term),
        "annualized_actual": annualized(total_return, total_months) if total_months else annualized(base, term),
        "annualized_net": annualized(net_total, total_months) if total_months else annualized(net_base, term),
    }


def money(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def request_soft_pull(bid, source):
    api_key = os.environ.get("CREDIT_API_KEY")
    status = "Requested via vendor" if api_key else "Auto-requested on application (pending vendor key)"
    vendor = os.environ.get("CREDIT_VENDOR", "Soft Pull Solutions") if api_key else "Pending vendor"
    db().execute(
        """INSERT INTO credit_pulls
        (borrower_id, pull_type, bureau, score, status, vendor, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            bid,
            "Soft",
            "Tri-merge",
            None,
            status,
            vendor,
            source,
            datetime.now().isoformat(timespec="minutes"),
        ),
    )


def profile_ready(b):
    if not b:
        return False, ["profile"]
    missing = []
    for label, key in [
        ("Full name", "name"),
        ("Email", "email"),
        ("Mobile phone", "phone"),
        ("Date of birth", "dob"),
        ("Social Security number", "ssn"),
        ("Street address", "address"),
        ("City", "city"),
        ("State", "state"),
        ("ZIP", "zip"),
        ("Own or rent", "own_or_rent"),
        ("Current employer", "employer"),
        ("Job title / occupation", "occupation"),
    ]:
        if not str(b[key] if key in b.keys() else "" or "").strip():
            missing.append(label)
    years = None
    try:
        years = float(b["years_at_address"]) if b["years_at_address"] not in (None, "") else None
    except (KeyError, TypeError, ValueError):
        years = None
    if years is None:
        missing.append("Years at current address")
    elif years < 2:
        if not str(b["prev_address"] if "prev_address" in b.keys() else "" or "").strip():
            missing.append("Previous address (required if under 2 years)")
    emp_years = None
    try:
        emp_years = float(b["years_employed"]) if b["years_employed"] not in (None, "") else None
    except (KeyError, TypeError, ValueError):
        emp_years = None
    if emp_years is None:
        missing.append("Years with current employer")
    elif emp_years < 2:
        if not str(b["prev_employer"] if "prev_employer" in b.keys() else "" or "").strip():
            missing.append("Previous employer (required if under 2 years on the job)")
    return (len(missing) == 0), missing


def public_base():
    return (os.environ.get("PUBLIC_BASE_URL") or request.url_root or "").rstrip("/")


def mail_address():
    return os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or ""


def mail_from_header():
    addr = mail_address()
    name = os.environ.get("MAIL_FROM_NAME") or "Brittco Capital Inc"
    return formataddr((name, addr)) if addr else ""


def send_mail(to_email, subject, body):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    mail_from = mail_address()
    if not (host and user and password and mail_from and to_email):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_from_header()
    msg["To"] = to_email
    msg["Reply-To"] = mail_from
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT") or 587), timeout=20) as s:
        s.starttls()
        s.login(user, password)
        s.sendmail(mail_from, [to_email], msg.as_string())
    return True


def send_invite(email, phone, channel, link, name):
    body = (
        f"Hello {name},\n\n"
        "This message is from Brittco Capital Inc.\n\n"
        "Please use the link below to complete your borrower profile and loan application. "
        "The link is unique to you.\n\n"
        f"{link}\n\n"
        "If you were not expecting this email, you may ignore it.\n\n"
        "Brittco Capital Inc\n"
    )
    sent = []
    errors = []
    want_email = channel in ("email", "both") and email
    want_sms = channel in ("text", "both") and phone
    if want_email:
        host = os.environ.get("SMTP_HOST")
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_PASS")
        mail_from = os.environ.get("MAIL_FROM") or user
        if host and user and password and mail_from:
            try:
                msg = MIMEText(body, "plain", "utf-8")
                msg["Subject"] = "Brittco Capital Inc — borrower application"
                msg["From"] = mail_from_header()
                msg["To"] = email
                msg["Reply-To"] = mail_from
                with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT") or 587), timeout=20) as s:
                    s.starttls()
                    s.login(user, password)
                    s.sendmail(mail_from, [email], msg.as_string())
                sent.append("email")
            except Exception as exc:
                errors.append(f"email: {exc}")
        else:
            errors.append("email not sent — add SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_FROM in Render")
    if want_sms:
        sid = os.environ.get("TWILIO_SID")
        token = os.environ.get("TWILIO_TOKEN")
        tw_from = os.environ.get("TWILIO_FROM")
        if sid and token and tw_from:
            try:
                data = urllib.parse.urlencode(
                    {"To": phone, "From": tw_from, "Body": f"Brittco invite: complete your profile {link}"}
                ).encode()
                req = urllib.request.Request(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                    data=data,
                )
                creds = urllib.parse.quote(sid) + ":" + urllib.parse.quote(token)
                req.add_header("Authorization", "Basic " + __import__("base64").b64encode(creds.encode()).decode())
                urllib.request.urlopen(req, timeout=20)
                sent.append("text")
            except Exception as exc:
                errors.append(f"text: {exc}")
        else:
            errors.append("text not sent — add TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM in Render")
    return sent, errors


def seed_form_templates(conn=None):
    c = conn or db()
    c.execute(
        """CREATE TABLE IF NOT EXISTS form_templates (
            form_key TEXT PRIMARY KEY,
            title TEXT,
            blurb TEXT,
            fields_json TEXT,
            body TEXT,
            updated_at TEXT
        )"""
    )
    now = datetime.now().isoformat(timespec="minutes")
    for key, spec in FORM_DEFAULTS.items():
        exists = c.execute("SELECT 1 FROM form_templates WHERE form_key=?", (key,)).fetchone()
        if exists:
            continue
        c.execute(
            """INSERT INTO form_templates (form_key, title, blurb, fields_json, body, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (key, spec["title"], spec["blurb"], json.dumps(spec["fields"]), spec["body"], now),
        )
    if conn is None:
        c.commit()


def load_form_defs():
    seed_form_templates()
    rows = db().execute("SELECT * FROM form_templates ORDER BY title").fetchall()
    out = {}
    for r in rows:
        try:
            fields = json.loads(r["fields_json"] or "[]")
            fields = [tuple(x) for x in fields]
        except Exception:
            fields = []
        out[r["form_key"]] = {
            "key": r["form_key"],
            "title": r["title"],
            "blurb": r["blurb"],
            "fields": fields,
            "body": r["body"] or "",
        }
    return out


def get_form_spec(key):
    defs = load_form_defs()
    return defs.get(key)


def apply_form_body(body, data):
    text = body or ""
    for key, val in (data or {}).items():
        text = text.replace("{{" + key + "}}", str(val or ""))
    return text


FORM_DEFS = FORM_DEFAULTS


def row_val(row, key):
    if row is None:
        return ""
    try:
        if key not in row.keys():
            return ""
    except Exception:
        return ""
    v = row[key]
    return "" if v is None else str(v)


def form_prefill(borrower, deal=None):
    parts = [
        row_val(borrower, "address"),
        row_val(borrower, "city"),
        row_val(borrower, "state"),
        row_val(borrower, "zip"),
    ]
    addr = ", ".join(p for p in parts if p)
    name = row_val(borrower, "name")
    entity = row_val(borrower, "entity_name")
    state = row_val(borrower, "state") or "FL"
    amt = ""
    if deal:
        raw = money(deal["loan_amount"]) if "loan_amount" in deal.keys() else 0
        amt = f"{raw:,.2f}" if raw else ""
    lender_addr = os.environ.get("LENDER_ADDRESS") or "Brittco Capital Inc"
    return {
        "guarantor_name": name,
        "borrower_name": name,
        "legal_name": name,
        "entity_name": entity,
        "address": addr,
        "phone": row_val(borrower, "phone"),
        "email": row_val(borrower, "email"),
        "dob": row_val(borrower, "dob"),
        "state": state,
        "county": "",
        "aka": "",
        "id_type": "Driver license",
        "id_number": "",
        "id_state": state,
        "occupancy": "Investment",
        "property": row_val(deal, "address") if deal else "",
        "loan_amount": amt,
        "closing_date": "",
        "effective_date": date.today().isoformat(),
        "secured_amount": amt,
        "secured_amount_words": "",
        "borrower_legal_name": entity or name,
        "borrower_entity_type": row_val(borrower, "entity_type") or "Limited Liability Company",
        "borrower_formation_state": state,
        "borrower_notice_address": addr,
        "lender_notice_address": lender_addr,
        "lender_phone": os.environ.get("LENDER_PHONE") or "",
        "trustee_name": os.environ.get("TRUSTEE_NAME") or "",
        "trustee_address": os.environ.get("TRUSTEE_ADDRESS") or "",
        "note_principal": amt,
        "note_principal_words": "",
        "legal_description": "",
        "insurance_amount": "",
        "signatory_name": name,
        "signatory_title": "Authorized Member",
        "notary_state": state,
        "notary_county": "",
        "profit_fee": "",
        "profit_fee_words": "",
        "maturity_date": "",
        "extension_rate": "",
        "extension_payment": "",
        "outside_date": "",
        "payment_day": "28",
        "late_charge_rate": "10",
        "late_charge_per_day": "",
        "guarantor_address": addr,
    }


def merge_form_data(stored, borrower, deal=None):
    filled = form_prefill(borrower, deal)
    data = dict(stored or {})
    for key, val in filled.items():
        if not str(data.get(key) or "").strip() and val:
            data[key] = val
    return data


def form_body_lines(spec, data):
    body = apply_form_body((spec or {}).get("body") or "", data)
    parts = [p.strip() for p in body.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if parts:
        return parts
    return [p.strip() for p in body.split("\n") if p.strip()] or ["Complete the fields above."]


def form_packet_pdf(spec, data, borrower_name):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        title=spec["title"], author="Brittco Capital Inc",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("T", parent=styles["Heading1"], fontSize=14, textColor=colors.HexColor("#16324f"), spaceAfter=4)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=8)
    small = ParagraphStyle("SM", parent=styles["Normal"], fontSize=8, leading=11, textColor=colors.HexColor("#445") )
    center = ParagraphStyle("C", parent=styles["Normal"], fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor("#667") )
    story = []
    logo = os.path.join(APP_DIR, "static", "logo.jpg")
    if os.path.exists(logo):
        story.append(Image(logo, width=1.4 * inch, height=0.55 * inch))
    story.append(Paragraph("Brittco Capital Inc", title))
    story.append(Paragraph(spec["title"], ParagraphStyle("H", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#16324f"))))
    story.append(Paragraph("Complete on screen, then download or print. Sign in wet ink before a notary public. This working form is not legal advice.", small))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c5d3e0"), spaceAfter=10, spaceBefore=6))
    def xml_esc(s):
        return (
            str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    for line in form_body_lines(spec, data):
        story.append(Paragraph(xml_esc(line), body))
    story.append(Spacer(1, 8))
    story.append(Paragraph("<b>Typed information</b>", body))
    rows = [[Paragraph("<b>Field</b>", small), Paragraph("<b>Value</b>", small)]]
    for key, label in spec["fields"]:
        rows.append([Paragraph(label, small), Paragraph(str(data.get(key) or "—"), small)])
    t = Table(rows, colWidths=[2.6 * inch, 4.4 * inch])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d3e0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f0f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))
    story.append(Paragraph("<b>Wet-ink signature (do not sign until you are in front of the notary)</b>", body))
    sig = [
        ["Signature: ________________________________", f"Date: ____________________"],
        [f"Printed name: {borrower_name or data.get('legal_name') or data.get('borrower_name') or ''}", ""],
    ]
    st = Table(sig, colWidths=[4.4 * inch, 2.6 * inch])
    st.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    story.append(st)
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#c5d3e0"), spaceAfter=8))
    story.append(Paragraph("<b>Notary public — jurat</b>", body))
    state = data.get("state") or "____________"
    county = data.get("county") or "____________"
    story.append(Paragraph(
        f"State of {state}<br/>County of {county}<br/><br/>"
        "Sworn to (or affirmed) and subscribed before me by means of ☐ physical presence  ☐ online notarization "
        f"this ______ day of ______________, ________, by {borrower_name or '________________'}, "
        "who ☐ is personally known to me  ☐ produced identification type ______________.",
        body,
    ))
    story.append(Paragraph("Notary signature: ________________________________     Commission expires: ______________", body))
    story.append(Paragraph("Notary printed name: ______________________________     Stamp / seal:", body))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Prepared for wet-ink notarization. Retain the signed original with the loan file.", center))
    doc.build(story)
    return buf.getvalue()


def mask_ssn(ssn):
    digits = "".join(ch for ch in (ssn or "") if ch.isdigit())
    if len(digits) >= 4:
        return f"***-**-{digits[-4:]}"
    return "—"


def save_borrower_from_form(f, bid=None, existing=None):
    years = f.get("years_at_address")
    payload = (
        f.get("name"),
        f.get("entity_type"),
        f.get("entity_name"),
        (f.get("email") or "").strip().lower(),
        f.get("phone"),
        int(f["credit_score"]) if f.get("credit_score") else None,
        f.get("password") or (existing["password"] if existing else "borrower"),
        f.get("notes"),
        f.get("address"),
        f.get("city"),
        f.get("state"),
        f.get("zip"),
        float(years) if years else None,
        f.get("prev_address"),
        f.get("prev_city"),
        f.get("prev_state"),
        f.get("prev_zip"),
        f.get("own_or_rent"),
        f.get("work_phone"),
        f.get("ssn"),
        f.get("dob"),
        f.get("employer"),
        f.get("occupation"),
        f.get("employer_phone"),
        f.get("employer_address"),
        f.get("employer_city"),
        f.get("employer_state"),
        float(f["years_employed"]) if f.get("years_employed") else None,
        f.get("prev_employer"),
        money(f.get("monthly_income")) if f.get("monthly_income") else None,
        money(f.get("liquid_assets")) if f.get("liquid_assets") else None,
        f.get("credit_events"),
    )
    cols = """name=?, entity_type=?, entity_name=?, email=?, phone=?, credit_score=?,
              password=?, notes=?, address=?, city=?, state=?, zip=?, years_at_address=?,
              prev_address=?, prev_city=?, prev_state=?, prev_zip=?, own_or_rent=?,
              work_phone=?, ssn=?, dob=?, employer=?, occupation=?, employer_phone=?,
              employer_address=?, employer_city=?, employer_state=?, years_employed=?,
              prev_employer=?, monthly_income=?, liquid_assets=?, credit_events=?"""
    if bid:
        db().execute(f"UPDATE borrowers SET {cols} WHERE id=?", payload + (bid,))
    else:
        db().execute(
            """INSERT INTO borrowers
            (name, entity_type, entity_name, email, phone, credit_score, password, notes,
             address, city, state, zip, years_at_address, prev_address, prev_city, prev_state,
             prev_zip, own_or_rent, work_phone, ssn, dob, employer, occupation, employer_phone,
             employer_address, employer_city, employer_state, years_employed, prev_employer,
             monthly_income, liquid_assets, credit_events)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )


def save_investor_profile(f, iid, existing=None):
    pw = f.get("password") or (existing["password"] if existing else "investor")
    db().execute(
        """UPDATE investors SET name=?, entity_name=?, email=?, phone=?, notes=?,
           password=?, capital_available=?, wire_bank=?, wire_routing=?, wire_account=?,
           wire_name=?, wire_further=?, wire_notes=? WHERE id=?""",
        (
            f.get("name") or (existing["name"] if existing else ""),
            f.get("entity_name"),
            f.get("email"),
            f.get("phone"),
            f.get("notes"),
            pw,
            money(f.get("capital_available")),
            f.get("wire_bank"),
            f.get("wire_routing"),
            f.get("wire_account"),
            f.get("wire_name"),
            f.get("wire_further"),
            f.get("wire_notes"),
            iid,
        ),
    )


def parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def days_until(s):
    d = parse_date(s)
    if not d:
        return None
    return (d - date.today()).days


def loan_stack(lid):
    parts = db().execute(
        """SELECT p.*, i.name AS investor_name, i.entity_name
           FROM participations p JOIN investors i ON i.id=p.investor_id
           WHERE p.loan_id=? ORDER BY p.id""",
        (lid,),
    ).fetchall()
    funded = sum(money(p["amount"]) for p in parts)
    return parts, funded


def split_payment(loan, amount, applied_to):
    """Split a borrower payment across investors vs Brittco."""
    parts, funded = loan_stack(loan["id"])
    borrower_rate = money(loan["rate"]) or 0
    rows = []
    if not parts or funded <= 0:
        rows.append(
            {
                "investor_id": None,
                "investor_name": "Unallocated / Brittco book",
                "investor_amount": 0.0,
                "brittco_amount": amount,
            }
        )
        return rows
    remaining = amount
    for i, p in enumerate(parts):
        share = amount * (money(p["amount"]) / funded)
        if i == len(parts) - 1:
            share = remaining
        remaining -= share
        inv_rate = money(p["investor_rate"])
        if applied_to == "Principal":
            inv_amt, brit_amt = share, 0.0
        elif borrower_rate > 0 and inv_rate > 0:
            inv_amt = share * (inv_rate / borrower_rate)
            brit_amt = share - inv_amt
        else:
            inv_amt, brit_amt = share, 0.0
        try:
            fee = 25.0 if p["mgmt_fee_pct"] is None else money(p["mgmt_fee_pct"])
        except (KeyError, IndexError):
            fee = 25.0
        rows.append(
            {
                "investor_id": p["investor_id"],
                "investor_name": p["investor_name"],
                "investor_amount": round(inv_amt, 2),
                "gross_investor": round(inv_amt, 2),
                "nate_amount": 0.0,
                "brittco_amount": round(brit_amt, 2),
                "participation_id": p["id"],
                "mgmt_fee_pct": fee,
            }
        )
    return rows


def loan_alerts():
    rows = db().execute(
        """SELECT l.*, b.name AS borrower_name, b.email AS borrower_email
           FROM loans l JOIN borrowers b ON b.id=l.borrower_id
           WHERE l.status NOT IN ('Paid Off','Sold','Written Off')
           ORDER BY l.next_payment_due"""
    ).fetchall()
    alerts = []
    for r in rows:
        due = days_until(r["next_payment_due"])
        mat = days_until(r["maturity_date"])
        if due is not None and due < 0:
            alerts.append(
                {
                    "level": "bad",
                    "loan": r,
                    "kind": "payment_late",
                    "text": f"{r['borrower_name']} payment is {abs(due)} day(s) late on {r['loan_number']}.",
                }
            )
        elif due is not None and due <= 7:
            alerts.append(
                {
                    "level": "warn",
                    "loan": r,
                    "kind": "payment_due",
                    "text": f"{r['borrower_name']} payment due in {due} day(s) on {r['loan_number']}.",
                }
            )
        if mat is not None and 0 <= mat <= 45:
            alerts.append(
                {
                    "level": "warn",
                    "loan": r,
                    "kind": "maturity",
                    "text": f"{r['borrower_name']} loan {r['loan_number']} matures in {mat} day(s).",
                }
            )
        elif mat is not None and mat < 0:
            alerts.append(
                {
                    "level": "bad",
                    "loan": r,
                    "kind": "matured",
                    "text": f"{r['borrower_name']} loan {r['loan_number']} is past maturity.",
                }
            )
    return alerts


def post_reminders(alerts):
    sent = 0
    today = date.today().isoformat()
    for a in alerts:
        loan = a["loan"]
        exists = db().execute(
            "SELECT 1 FROM reminder_log WHERE loan_id=? AND kind=? AND sent_on=?",
            (loan["id"], a["kind"], today),
        ).fetchone()
        if exists:
            continue
        body = a["text"]
        db().execute(
            "INSERT INTO messages (deal_id, borrower_id, sender, body, created_at) VALUES (?,?,?,?,?)",
            (loan["deal_id"], loan["borrower_id"], "Brittco System", body, datetime.now().isoformat(timespec="minutes")),
        )
        db().execute(
            "INSERT INTO reminder_log (loan_id, kind, sent_on, audience, body) VALUES (?,?,?,?,?)",
            (loan["id"], a["kind"], today, "staff_and_borrower", body),
        )
        sent += 1
    if sent:
        db().commit()
    return sent


def underwrite(deal, credit_score):
    purchase = money(deal["purchase_price"])
    as_is = money(deal["as_is_value"]) or purchase
    basis = as_is or purchase
    loan = money(deal["loan_amount"])
    arv = money(deal["arv"])
    rehab = money(deal["rehab_budget"])
    ltv = (loan / basis * 100) if basis else 0
    cost_basis = purchase + rehab
    net_profit = arv - cost_basis if arv else None
    net_pct = (net_profit / arv * 100) if arv and net_profit is not None else None
    flags = []
    decision = "Approve"

    if credit_score and credit_score < 680:
        flags.append(f"Credit {credit_score} is below the 680 minimum.")
        decision = "Decline or exception"
    if ltv > 75:
        flags.append(f"LTV {ltv:.1f}% is above the 75% guideline.")
        if (deal["ltv_override_reason"] or "").strip():
            flags.append("Override reason is on file — holistic review.")
            if decision == "Approve":
                decision = "Review"
        else:
            decision = "Review" if decision == "Approve" else decision
            flags.append("Add an override reason if you want to proceed above 75% LTV.")
    if (deal["loan_type"] or "") == "Transactional Loan":
        flags.append(
            "Transactional Loan standard terms: 3% flat fee for up to 7 days. "
            "Extensions beyond 7 days are negotiable."
        )
        pts = money(deal["points"]) if "points" in deal.keys() else None
        if pts and abs(pts - 3) > 0.05:
            flags.append(f"Fee is {pts:g}% — standard transactional fee is 3%.")
            if decision == "Approve":
                decision = "Review"
    if deal["loan_type"] == "Fix and Flip":
        if net_pct is None:
            flags.append("Enter ARV and rehab so projected profit can be calculated.")
            if decision == "Approve":
                decision = "Review"
        elif net_pct < 25:
            flags.append(
                f"Net projected profit on ARV is {net_pct:.1f}% (preference is 25%+)."
            )
            if decision == "Approve":
                decision = "Review"
        else:
            flags.append(f"Projected profit {net_pct:.1f}% meets the 25% preference.")
    if not flags:
        flags.append("Within standard guidelines. Still apply holistic judgment.")
    return {
        "ltv": ltv,
        "credit": credit_score,
        "net_profit_pct": net_pct,
        "decision": decision,
        "flags": flags,
    }


def deal_rows(rows):
    out = []
    for r in rows:
        d = dict(r)
        basis = money(d.get("as_is_value")) or money(d.get("purchase_price"))
        loan = money(d.get("loan_amount"))
        d["ltv"] = (loan / basis * 100) if basis else None
        arv = money(d.get("arv"))
        rehab = money(d.get("rehab_budget"))
        purchase = money(d.get("purchase_price"))
        if arv:
            d["net_profit_pct"] = (arv - purchase - rehab) / arv * 100
        else:
            d["net_profit_pct"] = None
        out.append(d)
    return out


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        row = db().execute(
            "SELECT * FROM staff WHERE email=? AND password=?",
            (request.form["email"].strip().lower(), request.form["password"]),
        ).fetchone()
        if row:
            session.clear()
            session["staff_id"] = row["id"]
            return redirect(url_for("dashboard"))
        error = "Email or password is incorrect."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@staff_required
def dashboard():
    deals = deal_rows(
        db().execute(
            """SELECT d.*, b.name AS borrower_name
               FROM deals d JOIN borrowers b ON b.id=d.borrower_id
               ORDER BY d.id DESC"""
        ).fetchall()
    )
    funded = sum(money(d["loan_amount"]) for d in deals if d["status"] == "Funded")
    book = db().execute(
        "SELECT COUNT(*) c, COALESCE(SUM(current_balance),0) bal FROM loans WHERE status NOT IN ('Paid Off','Written Off')"
    ).fetchone()
    alerts = loan_alerts()
    post_reminders(alerts)
    year = str(date.today().year)
    closed_all = db().execute(
        "SELECT COUNT(*) c FROM loans WHERE status IN ('Paid Off','Closed','Termed','Sold','Written Off')"
    ).fetchone()["c"]
    closed_ytd = db().execute(
        """SELECT COUNT(*) c FROM loans
           WHERE status IN ('Paid Off','Closed','Termed','Sold','Written Off')
           AND (maturity_date LIKE ? OR start_date LIKE ?)""",
        (year + "%", year + "%"),
    ).fetchone()["c"]
    active_loans = db().execute(
        "SELECT COUNT(*) c FROM loans WHERE status NOT IN ('Paid Off','Closed','Termed','Sold','Written Off')"
    ).fetchone()["c"]
    active_investors = db().execute(
        """SELECT COUNT(*) c FROM investors i
           WHERE COALESCE(i.capital_available,0) > 0
              OR EXISTS (SELECT 1 FROM participations p WHERE p.investor_id=i.id AND p.status!='Closed')"""
    ).fetchone()["c"]
    stats = {
        "active": sum(1 for d in deals if d["status"] not in ("Declined", "Paid Off")),
        "uw": sum(1 for d in deals if d["status"] == "Underwriting"),
        "borrowers": db().execute("SELECT COUNT(*) c FROM borrowers").fetchone()["c"],
        "investors": db().execute("SELECT COUNT(*) c FROM investors").fetchone()["c"],
        "active_investors": active_investors,
        "active_loans": active_loans,
        "closed_ytd": closed_ytd,
        "closed_all": closed_all,
        "capital_available": db().execute(
            "SELECT COALESCE(SUM(capital_available),0) t FROM investors"
        ).fetchone()["t"],
        "funded": funded,
        "loans": book["c"],
        "servicing": book["bal"],
        "alerts": len(alerts),
        "year": year,
        "nate_payable": sum(
            investor_books(i["id"])["nate_all"]
            for i in db().execute("SELECT id FROM investors").fetchall()
        ),
    }
    return render_template(
        "dashboard.html",
        title="Dashboard",
        nav="dash",
        deals=deals[:5],
        stats=stats,
        alerts=alerts,
    )


@app.route("/borrowers")
@staff_required
def borrowers():
    rows = db().execute(
        """SELECT b.*, (SELECT COUNT(*) FROM deals d WHERE d.borrower_id=b.id) deal_count
           FROM borrowers b ORDER BY b.name"""
    ).fetchall()
    return render_template(
        "borrowers.html",
        title="Borrowers",
        nav="borrowers",
        borrowers=rows,
        invite_url=session.pop("last_invite_url", None),
        invite_note=session.pop("last_invite_note", None),
    )


@app.route("/borrowers/invite", methods=["GET", "POST"])
@staff_required
def borrower_invite():
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        email = (f.get("email") or "").strip().lower()
        phone = (f.get("phone") or "").strip()
        channel = f.get("channel") or "email"
        if not name or (not email and not phone):
            return render_template(
                "borrower_invite.html",
                title="Invite borrower",
                nav="invite",
                error="Name and at least an email or mobile number are required.",
            )
        existing = None
        if email:
            existing = db().execute("SELECT * FROM borrowers WHERE email=?", (email,)).fetchone()
        if existing:
            bid = existing["id"]
        else:
            placeholder = email or f"invite-{secrets.token_hex(4)}@pending.brittco"
            db().execute(
                """INSERT INTO borrowers (name, entity_type, email, phone, password, notes)
                   VALUES (?,?,?,?,?,?)""",
                (name, "Individual", placeholder, phone, secrets.token_hex(6), "Invited — profile incomplete"),
            )
            db().commit()
            bid = db().execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        token = secrets.token_urlsafe(24)
        db().execute(
            """INSERT INTO invites (token, kind, borrower_id, name, email, phone, channel, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (token, "borrower", bid, name, email, phone, channel, datetime.now().isoformat(timespec="minutes")),
        )
        db().commit()
        link = public_base() + url_for("accept_invite", token=token)
        sent, errors = send_invite(email, phone, channel, link, name)
        note = "Invite created."
        if sent:
            note += " Sent by " + " and ".join(sent) + "."
        if errors:
            note += " " + " ".join(errors)
        session["last_invite_url"] = link
        session["last_invite_note"] = note
        return redirect(url_for("borrowers"))
    return render_template("borrower_invite.html", title="Invite borrower", nav="invite", error=None)


@app.route("/borrowers/new", methods=["GET", "POST"])
@staff_required
def borrower_new():
    if request.method == "POST":
        save_borrower_from_form(request.form)
        db().commit()
        return redirect(url_for("borrowers"))
    return render_template(
        "borrower_form.html", title="New borrower", nav="newborrower", b=None
    )


@app.route("/borrowers/<int:bid>")
@staff_required
def borrower_detail(bid):
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    deals = deal_rows(
        db().execute("SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC", (bid,)).fetchall()
    )
    pulls = db().execute(
        "SELECT * FROM credit_pulls WHERE borrower_id=? ORDER BY id DESC", (bid,)
    ).fetchall()
    return render_template(
        "borrower_detail.html",
        title=b["name"],
        nav="borrowers",
        b=b,
        deals=deals,
        pulls=pulls,
        ssn_mask=mask_ssn(b["ssn"] if "ssn" in b.keys() else ""),
        ach_url=os.environ.get("ACH_PORTAL_URL", "https://dashboard.dwolla.com"),
        ach_rows=db().execute(
            """SELECT t.*, l.loan_number FROM ach_transfers t
               LEFT JOIN loans l ON l.id=t.loan_id
               WHERE t.borrower_id=? ORDER BY t.id DESC""",
            (bid,),
        ).fetchall(),
        form_defs=load_form_defs(),
        packets=db().execute(
            "SELECT * FROM form_packets WHERE borrower_id=? ORDER BY id DESC", (bid,)
        ).fetchall(),
    )


@app.route("/borrowers/<int:bid>/forms", methods=["POST"])
@staff_required
def send_borrower_form(bid):
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    defs = load_form_defs()
    keys = [k for k in request.form.getlist("form_key") if k in defs]
    if not keys:
        single = request.form.get("form_key")
        if single in defs:
            keys = [single]
    if not keys:
        session["last_form_note"] = "Select at least one form."
        return redirect(url_for("borrower_detail", bid=bid))
    deal = db().execute(
        "SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC LIMIT 1", (bid,)
    ).fetchone()
    payload = json.dumps(form_prefill(b, deal))
    created = []
    for key in keys:
        token = secrets.token_urlsafe(16)
        db().execute(
            """INSERT INTO form_packets
               (token, form_key, borrower_id, deal_id, status, payload, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (token, key, bid, deal["id"] if deal else None, "Sent", payload, datetime.now().isoformat(timespec="minutes")),
        )
        created.append((key, token))
    db().commit()
    lines = []
    for key, token in created:
        link = public_base() + url_for("fill_form", token=token)
        lines.append(f"{defs[key]['title']}: {link}")
    mailed = False
    if b["email"] and lines:
        try:
            mailed = send_mail(
                b["email"],
                "Brittco forms to complete and notarize",
                f"Hello {b['name'] or ''},\n\n"
                "Please complete these Brittco Capital forms. Your profile information is already filled in. "
                "Download or print each one and sign in wet ink in front of a notary public.\n\n"
                + "\n".join(lines)
                + "\n",
            )
        except Exception:
            mailed = False
    session["last_form_url"] = public_base() + url_for("fill_form", token=created[0][1])
    session["last_form_note"] = (
        f"{len(created)} form(s) ready. "
        + ("Emailed to " + b["email"] + ". " if mailed else "Email not sent — copy the links from the list below. ")
        + " | ".join(lines)
    )
    return redirect(url_for("borrower_detail", bid=bid))


@app.route("/forms/<token>", methods=["GET", "POST"])
def fill_form(token):
    row = db().execute("SELECT * FROM form_packets WHERE token=?", (token,)).fetchone()
    if not row:
        return "This form link is not valid.", 404
    spec = get_form_spec(row["form_key"])
    if not spec:
        return "Unknown form.", 404
    spec = dict(spec)
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (row["borrower_id"],)).fetchone()
    deal = None
    if row["deal_id"]:
        deal = db().execute("SELECT * FROM deals WHERE id=?", (row["deal_id"],)).fetchone()
    if not deal:
        deal = db().execute(
            "SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC LIMIT 1", (row["borrower_id"],)
        ).fetchone()
    data = merge_form_data(json.loads(row["payload"] or "{}"), b, deal)
    if request.method == "POST":
        for key, _label in spec["fields"]:
            data[key] = request.form.get(key) or ""
        db().execute(
            "UPDATE form_packets SET payload=?, status=?, completed_at=? WHERE id=?",
            (json.dumps(data), "Completed", datetime.now().isoformat(timespec="minutes"), row["id"]),
        )
        db().commit()
        if request.form.get("download"):
            pdf = form_packet_pdf(spec, data, b["name"] if b else "")
            name = f"{row['form_key']}.pdf"
            return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=name)
        return redirect(url_for("fill_form", token=token, saved=1))
    return render_template(
        "form_fill.html",
        spec=spec,
        data=data,
        packet=row,
        borrower=b,
        saved=request.args.get("saved"),
        preview=apply_form_body(spec.get("body") or "", data),
    )


@app.route("/forms/<token>.pdf")
def form_pdf(token):
    row = db().execute("SELECT * FROM form_packets WHERE token=?", (token,)).fetchone()
    if not row:
        return "This form link is not valid.", 404
    spec = get_form_spec(row["form_key"]) or {}
    spec = dict(spec)
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (row["borrower_id"],)).fetchone()
    deal = None
    if row["deal_id"]:
        deal = db().execute("SELECT * FROM deals WHERE id=?", (row["deal_id"],)).fetchone()
    data = merge_form_data(json.loads(row["payload"] or "{}"), b, deal)
    pdf = form_packet_pdf(spec, data, b["name"] if b else "")
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"{row['form_key']}.pdf")


@app.route("/borrowers/<int:bid>/ach", methods=["POST"])
@staff_required
def borrower_ach(bid):
    f = request.form
    db().execute(
        """UPDATE borrowers SET bank_name=?, bank_routing=?, bank_account=?,
           bank_account_type=?, ach_authorized=? WHERE id=?""",
        (
            f.get("bank_name"),
            f.get("bank_routing"),
            f.get("bank_account"),
            f.get("bank_account_type"),
            1 if f.get("ach_authorized") else 0,
            bid,
        ),
    )
    db().commit()
    if f.get("open_processor"):
        return redirect(os.environ.get("ACH_PORTAL_URL", "https://dashboard.dwolla.com"))
    return redirect(url_for("borrower_detail", bid=bid))


@app.route("/borrowers/<int:bid>/edit", methods=["GET", "POST"])
@staff_required
def borrower_edit(bid):
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    if request.method == "POST":
        save_borrower_from_form(request.form, bid=bid, existing=b)
        db().commit()
        return redirect(url_for("borrower_detail", bid=bid))
    return render_template(
        "borrower_form.html", title="Edit borrower", nav="borrowers", b=b
    )


@app.route("/deals")
@staff_required
def deals():
    rows = deal_rows(
        db().execute(
            """SELECT d.*, b.name AS borrower_name
               FROM deals d JOIN borrowers b ON b.id=d.borrower_id
               ORDER BY d.id DESC"""
        ).fetchall()
    )
    return render_template("deals.html", title="Deals", nav="deals", deals=rows)


def save_deal(deal_id=None):
    f = request.form
    fields = (
        int(f["borrower_id"]),
        f.get("loan_type"),
        f.get("address"),
        money(f.get("purchase_price")) or None,
        money(f.get("as_is_value")) or None,
        money(f.get("arv")) or None,
        money(f.get("rehab_budget")) or None,
        money(f.get("loan_amount")) or None,
        money(f.get("rate")) or None,
        money(f.get("points")) or None,
        int(f["term_months"]) if f.get("term_months") else None,
        f.get("status") or "Lead",
        f.get("exit_strategy"),
        f.get("notes"),
        f.get("ltv_override_reason"),
    )
    if deal_id:
        db().execute(
            """UPDATE deals SET borrower_id=?, loan_type=?, address=?, purchase_price=?,
               as_is_value=?, arv=?, rehab_budget=?, loan_amount=?, rate=?, points=?,
               term_months=?, status=?, exit_strategy=?, notes=?, ltv_override_reason=?
               WHERE id=?""",
            fields + (deal_id,),
        )
        db().commit()
        if (f.get("status") or "") == "Closing":
            ensure_closing_list(deal_id)
        return deal_id
    cur = db().execute(
        """INSERT INTO deals
        (borrower_id, loan_type, address, purchase_price, as_is_value, arv, rehab_budget,
         loan_amount, rate, points, term_months, status, exit_strategy, notes,
         ltv_override_reason, created_at, acked)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        fields + (datetime.now().isoformat(timespec="minutes"), 1),
    )
    db().commit()
    did = cur.lastrowid
    if (f.get("status") or "") == "Closing":
        ensure_closing_list(did)
    return did


@app.route("/deals/new", methods=["GET", "POST"])
@app.route("/new-deal", methods=["GET", "POST"])
@staff_required
def deal_new():
    borrowers = db().execute("SELECT id, name FROM borrowers ORDER BY name").fetchall()
    selected = request.args.get("borrower_id", type=int)
    if request.method == "POST":
        did = save_deal()
        return redirect(url_for("deal_detail", did=did))
    return render_template(
        "deal_form.html",
        title="New deal",
        nav="newdeal",
        d=None,
        borrowers=borrowers,
        selected_borrower=selected,
    )


@app.route("/deals/<int:did>")
@staff_required
def deal_detail(did):
    d = db().execute(
        """SELECT d.*, b.name AS borrower_name, b.credit_score
           FROM deals d JOIN borrowers b ON b.id=d.borrower_id WHERE d.id=?""",
        (did,),
    ).fetchone()
    uw = underwrite(d, d["credit_score"])
    messages = db().execute(
        "SELECT * FROM messages WHERE deal_id=? OR (borrower_id=? AND deal_id IS NULL) ORDER BY id",
        (did, d["borrower_id"]),
    ).fetchall()
    if d["status"] == "Closing":
        ensure_closing_list(did)
    checklist = db().execute(
        "SELECT * FROM closing_items WHERE deal_id=? ORDER BY id", (did,)
    ).fetchall()
    docs = db().execute(
        "SELECT * FROM documents WHERE deal_id=? ORDER BY id DESC", (did,)
    ).fetchall()
    existing_loan = db().execute(
        "SELECT id, loan_number FROM loans WHERE deal_id=? ORDER BY id DESC", (did,)
    ).fetchone()
    return render_template(
        "deal_detail.html",
        title=d["address"],
        nav="deals",
        d=d,
        uw=uw,
        messages=messages,
        checklist=checklist,
        docs=docs,
        existing_loan=existing_loan,
    )


@app.route("/deals/<int:did>/create-loan", methods=["POST"])
@staff_required
def deal_create_loan(did):
    d = db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    existing = db().execute("SELECT id FROM loans WHERE deal_id=?", (did,)).fetchone()
    if existing:
        return redirect(url_for("loan_detail", lid=existing["id"]))
    principal = money(d["loan_amount"])
    start = date.today()
    transactional = (d["loan_type"] or "") == "Transactional Loan"
    if transactional:
        maturity = (start + timedelta(days=7)).isoformat()
        nxt = maturity
        points = money(d["points"]) or 3.0
        pay_type = "Flat fee"
        notes = "Transactional Loan: 3% flat fee for up to 7 days. Extensions beyond 7 days are negotiable."
    else:
        months = int(d["term_months"] or 12)
        maturity = (start + timedelta(days=30 * months)).isoformat()
        nxt = (start + timedelta(days=30)).isoformat()
        points = money(d["points"]) or None
        pay_type = "Interest only"
        notes = "Created from funded deal"
    number = f"BC-{did:04d}"
    cur = db().execute(
        """INSERT INTO loans
        (borrower_id, deal_id, loan_number, loan_type, property_address,
         original_principal, current_balance, rate, points, start_date, maturity_date,
         payment_type, payment_amount, payment_frequency, next_payment_due, late_fee,
         status, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            d["borrower_id"],
            did,
            number,
            d["loan_type"],
            d["address"],
            principal,
            principal,
            money(d["rate"]) or None,
            points,
            start.isoformat(),
            maturity,
            pay_type,
            None,
            "Monthly",
            nxt,
            0,
            "Current",
            notes,
        ),
    )
    db().execute("UPDATE deals SET status=? WHERE id=?", ("Funded", did))
    db().commit()
    return redirect(url_for("loan_detail", lid=cur.lastrowid))


@app.route("/deals/<int:did>/edit", methods=["GET", "POST"])
@staff_required
def deal_edit(did):
    d = db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    borrowers = db().execute("SELECT id, name FROM borrowers ORDER BY name").fetchall()
    if request.method == "POST":
        save_deal(did)
        return redirect(url_for("deal_detail", did=did))
    return render_template(
        "deal_form.html",
        title="Edit deal",
        nav="deals",
        d=d,
        borrowers=borrowers,
        selected_borrower=d["borrower_id"],
    )


@app.route("/deals/<int:did>/message", methods=["POST"])
@staff_required
def staff_message(did):
    d = db().execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    db().execute(
        "INSERT INTO messages (deal_id, borrower_id, sender, body, created_at) VALUES (?,?,?,?,?)",
        (did, d["borrower_id"], "Brittco Staff", request.form.get("body"), datetime.now().isoformat(timespec="minutes")),
    )
    db().commit()
    return redirect(url_for("deal_detail", did=did))


@app.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token):
    inv = db().execute("SELECT * FROM invites WHERE token=?", (token,)).fetchone()
    if not inv:
        return render_template("invite_accept.html", error="This invite link is not valid.", inv=None, b=None)
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (inv["borrower_id"],)).fetchone()
    if request.method == "POST":
        if not request.form.get("password"):
            return render_template(
                "invite_accept.html",
                error="Please choose a password.",
                inv=inv,
                b=b,
            )
        save_borrower_from_form(request.form, bid=b["id"], existing=b)
        db().execute(
            "UPDATE invites SET used_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="minutes"), inv["id"]),
        )
        db().commit()
        session.clear()
        session["borrower_id"] = b["id"]
        return redirect(url_for("portal_home", msg="Welcome. Finish any remaining required fields, then you can apply."))
    return render_template("invite_accept.html", error=None, inv=inv, b=b)


@app.route("/portal/login", methods=["GET", "POST"])
def portal_login():
    error = None
    if request.method == "POST":
        row = db().execute(
            "SELECT * FROM borrowers WHERE email=? AND password=?",
            (request.form["email"].strip().lower(), request.form["password"]),
        ).fetchone()
        if row:
            session.clear()
            session["borrower_id"] = row["id"]
            return redirect(url_for("portal_home"))
        error = "Email or password is incorrect."
    return render_template("portal_login.html", error=error)


@app.route("/portal/logout")
def portal_logout():
    session.clear()
    return redirect(url_for("portal_login"))


@app.route("/portal")
@borrower_required
def portal_home():
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (session["borrower_id"],)).fetchone()
    deals = db().execute(
        "SELECT * FROM deals WHERE borrower_id=? ORDER BY id DESC", (b["id"],)
    ).fetchall()
    loans = db().execute(
        "SELECT * FROM loans WHERE borrower_id=? AND status NOT IN ('Written Off') ORDER BY id DESC",
        (b["id"],),
    ).fetchall()
    messages = db().execute(
        "SELECT * FROM messages WHERE borrower_id=? ORDER BY id", (b["id"],)
    ).fetchall()
    docs = db().execute(
        "SELECT * FROM documents WHERE borrower_id=? ORDER BY id DESC", (b["id"],)
    ).fetchall()
    ready, missing = profile_ready(b)
    packets = db().execute(
        "SELECT * FROM form_packets WHERE borrower_id=? ORDER BY id DESC", (b["id"],)
    ).fetchall()
    return render_template(
        "portal.html",
        b=b,
        deals=deals,
        loans=loans,
        messages=messages,
        docs=docs,
        flash=request.args.get("msg"),
        ready=ready,
        missing=missing,
        packets=packets,
        form_defs=load_form_defs(),
    )


@app.route("/portal/profile", methods=["POST"])
@borrower_required
def portal_profile():
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (session["borrower_id"],)).fetchone()
    save_borrower_from_form(request.form, bid=b["id"], existing=b)
    db().commit()
    return redirect(url_for("portal_home", msg="Profile saved"))


@app.route("/portal/apply", methods=["POST"])
@borrower_required
def portal_apply():
    f = request.form
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (session["borrower_id"],)).fetchone()
    ready, missing = profile_ready(b)
    if not ready or f.get("credit_consent") != "yes":
        return redirect(url_for("portal_home"))
    cur = db().execute(
        """INSERT INTO deals
        (borrower_id, loan_type, address, purchase_price, as_is_value, arv, rehab_budget,
         loan_amount, rate, points, term_months, status, exit_strategy, notes,
         ltv_override_reason, created_at, acked)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session["borrower_id"],
            f.get("loan_type"),
            f.get("address"),
            money(f.get("purchase_price")) or None,
            None,
            money(f.get("arv")) or None,
            money(f.get("rehab_budget")) or None,
            money(f.get("loan_amount")) or None,
            None,
            None,
            12,
            "Application",
            f.get("exit_strategy"),
            "Submitted from borrower portal",
            "",
            datetime.now().isoformat(timespec="minutes"),
            0,
        ),
    )
    db().commit()
    did = cur.lastrowid
    files = request.files.getlist("docs")
    save_uploads(did, session["borrower_id"], files)
    request_soft_pull(
        session["borrower_id"],
        "Automatic soft pull on portal application. Borrower consented in writing that a soft inquiry will not adversely affect their credit report.",
    )
    db().commit()
    return redirect(url_for("portal_home"))


@app.route("/portal/message", methods=["POST"])
@borrower_required
def portal_message():
    db().execute(
        "INSERT INTO messages (deal_id, borrower_id, sender, body, created_at) VALUES (?,?,?,?,?)",
        (
            None,
            session["borrower_id"],
            "Borrower",
            request.form.get("body"),
            datetime.now().isoformat(timespec="minutes"),
        ),
    )
    db().commit()
    return redirect(url_for("portal_home"))


@app.route("/loans")
@staff_required
def loans():
    rows = db().execute(
        """SELECT l.*, b.name AS borrower_name
           FROM loans l JOIN borrowers b ON b.id=l.borrower_id
           ORDER BY l.id DESC"""
    ).fetchall()
    enriched = []
    for r in rows:
        d = dict(r)
        d["due_in"] = days_until(r["next_payment_due"])
        d["matures_in"] = days_until(r["maturity_date"])
        enriched.append(d)
    return render_template("loans.html", title="Loan management", nav="loans", loans=enriched)


@app.route("/loans/new", methods=["GET", "POST"])
@staff_required
def loan_new():
    borrowers = db().execute("SELECT id, name FROM borrowers ORDER BY name").fetchall()
    deals = db().execute("SELECT id, address, loan_type FROM deals ORDER BY id DESC").fetchall()
    if request.method == "POST":
        f = request.form
        principal = money(f.get("original_principal"))
        db().execute(
            """INSERT INTO loans
            (borrower_id, deal_id, loan_number, loan_type, property_address,
             original_principal, current_balance, rate, points, start_date, maturity_date,
             payment_type, payment_amount, payment_frequency, next_payment_due, late_fee,
             status, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(f["borrower_id"]),
                int(f["deal_id"]) if f.get("deal_id") else None,
                f.get("loan_number"),
                f.get("loan_type"),
                f.get("property_address"),
                principal,
                money(f.get("current_balance")) or principal,
                money(f.get("rate")) or None,
                money(f.get("points")) or None,
                f.get("start_date"),
                f.get("maturity_date"),
                f.get("payment_type"),
                money(f.get("payment_amount")) or None,
                f.get("payment_frequency") or "Monthly",
                f.get("next_payment_due"),
                money(f.get("late_fee")) or 0,
                f.get("status") or "Current",
                f.get("notes"),
            ),
        )
        db().commit()
        return redirect(url_for("loans"))
    return render_template(
        "loan_form.html", title="New loan", nav="loans", borrowers=borrowers, deals=deals, loan=None
    )


@app.route("/loans/<int:lid>")
@staff_required
def loan_detail(lid):
    loan = db().execute(
        """SELECT l.*, b.name AS borrower_name, b.email, b.phone, b.credit_score
           FROM loans l JOIN borrowers b ON b.id=l.borrower_id WHERE l.id=?""",
        (lid,),
    ).fetchone()
    payments = db().execute(
        "SELECT * FROM payments WHERE loan_id=? ORDER BY paid_on DESC, id DESC", (lid,)
    ).fetchall()
    paid = sum(money(p["amount"]) for p in payments)
    raw_parts, funded = loan_stack(lid)
    dists = db().execute(
        """SELECT d.*, i.name AS investor_name
           FROM distributions d LEFT JOIN investors i ON i.id=d.investor_id
           WHERE d.loan_id=? ORDER BY d.id DESC""",
        (lid,),
    ).fetchall()
    parts = []
    for p in raw_parts:
        d = dict(p)
        mine = [x for x in dists if x["investor_id"] == p["investor_id"]]
        d.update(participation_returns(loan, p, mine))
        parts.append(d)
    inv_paid = sum(money(d["investor_amount"]) for d in dists)
    brit_paid = sum(money(d["brittco_amount"]) for d in dists)
    investors = db().execute("SELECT id, name FROM investors ORDER BY name").fetchall()
    perf = {
        "paid": paid,
        "due_in": days_until(loan["next_payment_due"]),
        "matures_in": days_until(loan["maturity_date"]),
        "remaining": money(loan["current_balance"]),
        "funded": funded,
        "gap": money(loan["original_principal"]) - funded,
        "investor_paid": inv_paid,
        "brittco_paid": brit_paid,
        "days": loan_term_days(loan, parts[0] if parts else None),
        "closed": (loan["status"] or "") in ("Paid Off", "Termed", "Closed", "Sold"),
        "est_gross": sum(p.get("est_gross") or 0 for p in parts),
        "est_nate": sum(p.get("est_nate") or 0 for p in parts),
        "est_net": sum(p.get("est_net") or 0 for p in parts),
        "est_ann": (parts[0]["est_ann"] if parts else annualized_days(money(loan["points"]) or money(loan["rate"]), loan_term_days(loan)) or 0),
        "est_ann_net": (parts[0]["est_ann_net"] if parts else 0),
        "actual_gross": sum(p.get("actual_gross") or 0 for p in parts),
        "actual_nate": sum(p.get("actual_nate") or 0 for p in parts),
        "actual_net": sum(p.get("actual_net") or 0 for p in parts),
    }
    return render_template(
        "loan_detail.html",
        title=loan["loan_number"],
        nav="loans",
        loan=loan,
        payments=payments,
        perf=perf,
        parts=parts,
        dists=dists,
        investors=investors,
    )


@app.route("/loans/<int:lid>/payment", methods=["POST"])
@staff_required
def loan_payment(lid):
    f = request.form
    amt = money(f.get("amount"))
    applied = f.get("applied_to") or "Interest"
    loan = db().execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
    new_bal = money(loan["current_balance"])
    if applied == "Principal":
        new_bal = max(0.0, new_bal - amt)
    status = "Paid Off" if new_bal <= 0 else loan["status"]
    nxt = f.get("next_payment_due") or loan["next_payment_due"]
    cur = db().execute(
        "INSERT INTO payments (loan_id, paid_on, amount, applied_to, note) VALUES (?,?,?,?,?)",
        (lid, f.get("paid_on") or date.today().isoformat(), amt, applied, f.get("note")),
    )
    pay_id = cur.lastrowid
    for row in split_payment(loan, amt, applied):
        db().execute(
            """INSERT INTO distributions
            (payment_id, loan_id, investor_id, investor_amount, brittco_amount, nate_amount, kind, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                pay_id,
                lid,
                row["investor_id"],
                row["investor_amount"],
                row["brittco_amount"],
                row.get("nate_amount") or 0,
                applied,
                datetime.now().isoformat(timespec="minutes"),
            ),
        )
    db().execute(
        "UPDATE loans SET current_balance=?, status=?, next_payment_due=? WHERE id=?",
        (new_bal, status, nxt, lid),
    )
    db().commit()
    return redirect(url_for("loan_detail", lid=lid))


@app.route("/credit")
@staff_required
def credit():
    pulls = db().execute(
        """SELECT p.*, b.name AS borrower_name
           FROM credit_pulls p JOIN borrowers b ON b.id=p.borrower_id
           ORDER BY p.id DESC"""
    ).fetchall()
    borrowers = db().execute("SELECT id, name FROM borrowers ORDER BY name").fetchall()
    vendor_ready = bool(os.environ.get("CREDIT_API_KEY"))
    return render_template(
        "credit.html",
        title="Credit pulls",
        nav="credit",
        pulls=pulls,
        borrowers=borrowers,
        vendor_ready=vendor_ready,
    )


@app.route("/credit/pull", methods=["POST"])
@staff_required
def credit_pull():
    f = request.form
    bid = int(f["borrower_id"])
    pull_type = f.get("pull_type") or "Soft"
    bureau = f.get("bureau") or "Tri-merge"
    score = int(f["score"]) if f.get("score") else None
    notes = f.get("notes") or ""
    api_key = os.environ.get("CREDIT_API_KEY")
    status = "Recorded"
    vendor = "Manual entry"
    if api_key:
        vendor = os.environ.get("CREDIT_VENDOR", "Soft Pull Solutions")
        status = "Requested via vendor"
        notes = (notes + " Live API keys are set. Connect the vendor SDK next for automatic JSON import.").strip()
    else:
        notes = (notes + " No vendor key on file. Result stored as a manual / pending soft pull.").strip()
    db().execute(
        """INSERT INTO credit_pulls
        (borrower_id, pull_type, bureau, score, status, vendor, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (bid, pull_type, bureau, score, status, vendor, notes, datetime.now().isoformat(timespec="minutes")),
    )
    if score:
        db().execute("UPDATE borrowers SET credit_score=? WHERE id=?", (score, bid))
    db().commit()
    nxt = request.form.get("next") or url_for("credit")
    return redirect(nxt)


@app.route("/borrowers/<int:bid>/soft-pull", methods=["GET", "POST"])
@staff_required
def borrower_soft_pull(bid):
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    if request.method == "GET":
        return render_template(
            "credit_confirm.html",
            title="Confirm soft pull",
            nav="borrowers",
            b=b,
            ssn_mask=mask_ssn(b["ssn"] if b and "ssn" in b.keys() else ""),
        )
    if request.form.get("confirm") != "yes":
        return redirect(url_for("borrower_detail", bid=bid))
    api_key = os.environ.get("CREDIT_API_KEY")
    status = "Requested via vendor" if api_key else "Pending / recorded (no vendor key)"
    vendor = os.environ.get("CREDIT_VENDOR", "Soft Pull Solutions") if api_key else "Manual / pending"
    notes = "One-click soft pull confirmed by admin."
    db().execute(
        """INSERT INTO credit_pulls
        (borrower_id, pull_type, bureau, score, status, vendor, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            bid,
            "Soft",
            request.form.get("bureau") or "Tri-merge",
            None,
            status,
            vendor,
            notes,
            datetime.now().isoformat(timespec="minutes"),
        ),
    )
    db().commit()
    return redirect(url_for("borrower_detail", bid=bid))


@app.route("/investors")
@staff_required
def investors():
    rows = db().execute("SELECT * FROM investors ORDER BY name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        stats = db().execute(
            """SELECT COALESCE(SUM(amount),0) deployed, COUNT(*) n
               FROM participations WHERE investor_id=? AND status!='Closed'""",
            (r["id"],),
        ).fetchone()
        earned = db().execute(
            "SELECT COALESCE(SUM(investor_amount),0) t FROM distributions WHERE investor_id=?",
            (r["id"],),
        ).fetchone()["t"]
        d["deployed"] = stats["deployed"]
        d["deals"] = stats["n"]
        d["earned"] = earned
        out.append(d)
    return render_template("investors.html", title="Investors", nav="investors", investors=out)


@app.route("/investors/new", methods=["GET", "POST"])
@staff_required
def investor_new():
    if request.method == "POST":
        f = request.form
        db().execute(
            """INSERT INTO investors (name, entity_name, email, phone, notes, ach_status, password, capital_available)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f.get("name"),
                f.get("entity_name"),
                f.get("email"),
                f.get("phone"),
                f.get("notes"),
                "Not connected",
                f.get("password") or "investor",
                money(f.get("capital_available")),
            ),
        )
        db().commit()
        return redirect(url_for("investors"))
    return render_template("investor_form.html", title="New investor", nav="investors")


@app.route("/investors/<int:iid>")
@staff_required
def investor_detail(iid):
    inv = db().execute("SELECT * FROM investors WHERE id=?", (iid,)).fetchone()
    raw_parts = db().execute(
        """SELECT p.*, l.loan_number, l.property_address, l.rate AS borrower_rate, l.status AS loan_status
           FROM participations p JOIN loans l ON l.id=p.loan_id
           WHERE p.investor_id=? ORDER BY p.id DESC""",
        (iid,),
    ).fetchall()
    parts = []
    for p in raw_parts:
        d = dict(p)
        d.update(participation_math(p))
        parts.append(d)
    dists = db().execute(
        """SELECT d.*, l.loan_number FROM distributions d
           JOIN loans l ON l.id=d.loan_id WHERE d.investor_id=? ORDER BY d.id DESC""",
        (iid,),
    ).fetchall()
    ach = db().execute(
        "SELECT * FROM ach_transfers WHERE investor_id=? ORDER BY id DESC", (iid,)
    ).fetchall()
    books = investor_books(iid)
    return render_template(
        "investor_detail.html",
        title=inv["name"],
        nav="investors",
        inv=inv,
        parts=parts,
        dists=dists,
        ach=ach,
        books=books,
        dwolla_ready=bool(os.environ.get("ACH_API_KEY")),
    )


@app.route("/investors/<int:iid>/profile", methods=["GET", "POST"])
@staff_required
def investor_profile(iid):
    inv = db().execute("SELECT * FROM investors WHERE id=?", (iid,)).fetchone()
    if request.method == "POST":
        save_investor_profile(request.form, iid, inv)
        db().commit()
        return redirect(url_for("investor_detail", iid=iid))
    return render_template(
        "investor_profile.html", title="Investor profile", nav="investors", inv=inv, staff=True
    )


@app.route("/loans/<int:lid>/participate", methods=["POST"])
@staff_required
def add_participation(lid):
    f = request.form
    db().execute(
        """INSERT INTO participations
        (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
         max_extensions, extensions_used, status, funded_on, notes, mgmt_fee_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lid,
            int(f["investor_id"]),
            money(f.get("amount")),
            money(f.get("investor_rate")),
            int(f.get("term_months") or 3),
            money(f.get("extension_rate") or 2),
            int(f.get("max_extensions") or 3),
            0,
            f.get("status") or "Funded",
            f.get("funded_on") or date.today().isoformat(),
            f.get("notes"),
            money(f.get("mgmt_fee_pct") if f.get("mgmt_fee_pct") not in (None, "") else 25),
        ),
    )
    db().commit()
    return redirect(url_for("loan_detail", lid=lid))


@app.route("/loans/<int:lid>/fee/<int:pid>", methods=["POST"])
@staff_required
def set_mgmt_fee(lid, pid):
    return update_participation(lid, pid)


@app.route("/loans/<int:lid>/participation/<int:pid>", methods=["POST"])
@staff_required
def update_participation(lid, pid):
    f = request.form
    cur = db().execute("SELECT * FROM participations WHERE id=? AND loan_id=?", (pid, lid)).fetchone()
    if not cur:
        return redirect(url_for("loan_detail", lid=lid))
    inv = f.get("investor_id")
    db().execute(
        """UPDATE participations SET
           investor_id=?, amount=?, investor_rate=?, term_months=?,
           extension_rate=?, max_extensions=?, mgmt_fee_pct=?, funded_on=?
           WHERE id=? AND loan_id=?""",
        (
            int(inv) if inv else cur["investor_id"],
            money(f.get("amount")) if f.get("amount") not in (None, "") else money(cur["amount"]),
            money(f.get("investor_rate")) if f.get("investor_rate") not in (None, "") else money(cur["investor_rate"]),
            int(f.get("term_months") or cur["term_months"] or 3),
            money(f.get("extension_rate")) if f.get("extension_rate") not in (None, "") else money(cur["extension_rate"]),
            int(f.get("max_extensions") or cur["max_extensions"] or 3),
            money(f.get("mgmt_fee_pct") if f.get("mgmt_fee_pct") not in (None, "") else (cur["mgmt_fee_pct"] if cur["mgmt_fee_pct"] is not None else 25)),
            f.get("funded_on") or cur["funded_on"],
            pid,
            lid,
        ),
    )
    db().commit()
    return redirect(url_for("loan_detail", lid=lid))


@app.route("/loans/<int:lid>/extend/<int:pid>", methods=["POST"])
@staff_required
def add_extension(lid, pid):
    p = db().execute("SELECT * FROM participations WHERE id=?", (pid,)).fetchone()
    used = (p["extensions_used"] or 0) + 1
    max_ext = p["max_extensions"] or 0
    status = "Terming" if max_ext and used >= max_ext else p["status"]
    db().execute(
        "UPDATE participations SET extensions_used=?, status=? WHERE id=?",
        (used, status, pid),
    )
    db().execute(
        "INSERT INTO extensions (participation_id, loan_id, months, rate, created_at) VALUES (?,?,?,?,?)",
        (pid, lid, 1, money(p["extension_rate"]), datetime.now().isoformat(timespec="minutes")),
    )
    if status == "Terming":
        db().execute(
            "UPDATE loans SET status='Terming', notes=COALESCE(notes,'') || ? WHERE id=?",
            (f"\nReached max extensions ({used}). Ready to term.", lid),
        )
    db().commit()
    return redirect(url_for("loan_detail", lid=lid))


@app.route("/loans/<int:lid>/term", methods=["POST"])
@staff_required
def term_loan(lid):
    db().execute(
        "UPDATE loans SET status='Termed', maturity_date=? WHERE id=?",
        (date.today().isoformat(), lid),
    )
    db().execute(
        "UPDATE participations SET status='Termed' WHERE loan_id=? AND status!='Closed'",
        (lid,),
    )
    db().commit()
    return redirect(url_for("loan_detail", lid=lid))


@app.route("/investor/login", methods=["GET", "POST"])
def investor_login():
    error = None
    if request.method == "POST":
        row = db().execute(
            "SELECT * FROM investors WHERE email=? AND password=?",
            (request.form["email"].strip().lower(), request.form["password"]),
        ).fetchone()
        if row:
            session.clear()
            session["investor_id"] = row["id"]
            return redirect(url_for("investor_portal"))
        error = "Email or password is incorrect."
    return render_template("investor_login.html", error=error)


@app.route("/investor/logout")
def investor_logout():
    session.clear()
    return redirect(url_for("investor_login"))


@app.route("/investor")
@investor_required
def investor_portal():
    inv = db().execute("SELECT * FROM investors WHERE id=?", (session["investor_id"],)).fetchone()
    raw = db().execute(
        """SELECT p.*, l.loan_number, l.property_address, l.status AS loan_status, l.maturity_date
           FROM participations p JOIN loans l ON l.id=p.loan_id
           WHERE p.investor_id=? ORDER BY p.id DESC""",
        (inv["id"],),
    ).fetchall()
    parts = []
    for p in raw:
        d = dict(p)
        d.update(participation_math(p))
        parts.append(d)
    exts = db().execute(
        """SELECT e.*, l.loan_number FROM extensions e
           JOIN loans l ON l.id=e.loan_id
           JOIN participations p ON p.id=e.participation_id
           WHERE p.investor_id=? ORDER BY e.id DESC""",
        (inv["id"],),
    ).fetchall()
    books = attach_investor_carry(inv["id"], investor_books(inv["id"]))
    accounts = investor_funding_accounts(inv["id"])
    return render_template(
        "investor_portal.html",
        inv=inv,
        parts=parts,
        exts=exts,
        books=books,
        accounts=accounts,
        account_kinds=ACCOUNT_KINDS,
    )


@app.route("/investor/statement.pdf")
@investor_required
def investor_statement_self():
    inv = db().execute("SELECT * FROM investors WHERE id=?", (session["investor_id"],)).fetchone()
    books = attach_investor_carry(inv["id"], investor_books(inv["id"]))
    data = investor_statement_pdf(inv, books, include_carry=True)
    name = f"Brittco-statement-{inv['name'].replace(' ', '-')}.pdf"
    return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)


@app.route("/investors/<int:iid>/statement.pdf")
@staff_required
def investor_statement_staff(iid):
    inv = db().execute("SELECT * FROM investors WHERE id=?", (iid,)).fetchone()
    data = investor_statement_pdf(inv, investor_books(iid))
    name = f"Brittco-statement-{inv['name'].replace(' ', '-')}.pdf"
    return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)


@app.route("/investor/profile", methods=["GET", "POST"])
@investor_required
def investor_self_profile():
    inv = db().execute("SELECT * FROM investors WHERE id=?", (session["investor_id"],)).fetchone()
    if request.method == "POST":
        save_investor_profile(request.form, inv["id"], inv)
        db().commit()
        return redirect(url_for("investor_portal"))
    return render_template("investor_self_profile.html", inv=inv, staff=False)


@app.route("/investor/accounts", methods=["GET", "POST"])
@investor_required
def investor_accounts():
    iid = session["investor_id"]
    if request.method == "POST":
        nick = (request.form.get("nickname") or "").strip()
        kind = request.form.get("kind") if request.form.get("kind") in ACCOUNT_KINDS else "Other"
        last4 = "".join(ch for ch in (request.form.get("last4") or "") if ch.isdigit())[-4:]
        borrowed = 1 if request.form.get("is_borrowed") else 0
        if nick:
            db().execute(
                """INSERT INTO investor_accounts
                   (investor_id, nickname, kind, last4, bank_name, is_borrowed, apr, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    iid,
                    nick,
                    kind,
                    last4,
                    request.form.get("bank_name") or "",
                    borrowed,
                    money(request.form.get("apr")),
                    "Active",
                    datetime.now().isoformat(timespec="minutes"),
                ),
            )
            db().commit()
        return redirect(url_for("investor_accounts"))
    inv = db().execute("SELECT * FROM investors WHERE id=?", (iid,)).fetchone()
    return render_template(
        "investor_accounts.html",
        inv=inv,
        accounts=investor_funding_accounts(iid, include_archived=True),
        account_kinds=ACCOUNT_KINDS,
        msg=request.args.get("msg"),
    )


@app.route("/investor/accounts/<int:aid>/archive", methods=["POST"])
@investor_required
def investor_account_archive(aid):
    db().execute(
        "UPDATE investor_accounts SET status='Archived' WHERE id=? AND investor_id=?",
        (aid, session["investor_id"]),
    )
    db().commit()
    return redirect(url_for("investor_accounts", msg="Account archived"))


@app.route("/investor/participations/<int:pid>/source", methods=["POST"])
@investor_required
def investor_tag_source(pid):
    iid = session["investor_id"]
    part = db().execute(
        "SELECT * FROM participations WHERE id=? AND investor_id=?", (pid, iid)
    ).fetchone()
    if not part:
        return redirect(url_for("investor_portal"))
    acc_id = int(request.form.get("account_id") or 0)
    acc = db().execute(
        "SELECT * FROM investor_accounts WHERE id=? AND investor_id=?", (acc_id, iid)
    ).fetchone()
    if not acc:
        return redirect(url_for("investor_portal"))
    amt = money(request.form.get("amount")) or money(part["amount"])
    db().execute(
        "INSERT INTO participation_sources (participation_id, account_id, amount) VALUES (?,?,?)",
        (pid, acc_id, amt),
    )
    db().commit()
    return redirect(url_for("investor_portal"))


@app.route("/investor/sources/<int:sid>/remove", methods=["POST"])
@investor_required
def investor_source_remove(sid):
    db().execute(
        """DELETE FROM participation_sources WHERE id=? AND participation_id IN
           (SELECT id FROM participations WHERE investor_id=?)""",
        (sid, session["investor_id"]),
    )
    db().commit()
    return redirect(url_for("investor_portal"))


@app.route("/ach", methods=["GET", "POST"])
@staff_required
def ach():
    if request.method == "POST":
        f = request.form
        vendor = "Dwolla" if os.environ.get("ACH_API_KEY") else "Manual / pending bank"
        lid = int(f["loan_id"]) if f.get("loan_id") else None
        bid = int(f["borrower_id"]) if f.get("borrower_id") else None
        if lid and not bid:
            row = db().execute("SELECT borrower_id FROM loans WHERE id=?", (lid,)).fetchone()
            bid = row["borrower_id"] if row else None
        db().execute(
            """INSERT INTO ach_transfers
            (investor_id, borrower_id, loan_id, direction, amount, status, vendor, notes, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                None,
                bid,
                lid,
                "Debit borrower (loan payment)",
                money(f.get("amount")),
                "Queued" if os.environ.get("ACH_API_KEY") else "Recorded — collect at bank or connect processor",
                vendor,
                f.get("notes"),
                datetime.now().isoformat(timespec="minutes"),
            ),
        )
        db().commit()
        return redirect(url_for("ach"))
    transfers = db().execute(
        """SELECT t.*, b.name AS borrower_name, l.loan_number
           FROM ach_transfers t
           LEFT JOIN borrowers b ON b.id=t.borrower_id
           LEFT JOIN loans l ON l.id=t.loan_id
           ORDER BY t.id DESC"""
    ).fetchall()
    borrowers = db().execute("SELECT id, name FROM borrowers ORDER BY name").fetchall()
    loans = db().execute(
        """SELECT l.id, l.loan_number, l.property_address, b.name AS borrower_name
           FROM loans l JOIN borrowers b ON b.id=l.borrower_id
           ORDER BY l.id DESC"""
    ).fetchall()
    return render_template(
        "ach.html",
        title="ACH collections",
        nav="ach",
        transfers=transfers,
        borrowers=borrowers,
        loans=loans,
        vendor_ready=bool(os.environ.get("ACH_API_KEY")),
    )


@app.route("/alerts/ack/<int:did>", methods=["POST"])
@staff_required
def ack_application(did):
    db().execute("UPDATE deals SET acked=1 WHERE id=?", (did,))
    db().commit()
    return redirect(request.referrer or url_for("deal_detail", did=did))


@app.route("/deals/<int:did>/checklist", methods=["POST"])
@staff_required
def toggle_closing_item(did):
    iid = int(request.form["item_id"])
    row = db().execute("SELECT done FROM closing_items WHERE id=?", (iid,)).fetchone()
    db().execute("UPDATE closing_items SET done=? WHERE id=?", (0 if row["done"] else 1, iid))
    db().commit()
    return redirect(url_for("deal_detail", did=did))


@app.route("/deals/<int:did>/upload", methods=["POST"])
@staff_required
def staff_upload(did):
    d = db().execute("SELECT borrower_id FROM deals WHERE id=?", (did,)).fetchone()
    save_uploads(did, d["borrower_id"], request.files.getlist("docs"))
    return redirect(url_for("deal_detail", did=did))


@app.route("/files/<path:name>")
def serve_upload(name):
    if not (session.get("staff_id") or session.get("borrower_id") or session.get("investor_id")):
        return redirect(url_for("login"))
    return send_from_directory(UPLOAD_DIR, name)


@app.route("/capital")
@staff_required
def capital_available():
    rows = db().execute(
        """SELECT id, name, entity_name, email, COALESCE(capital_available,0) AS capital_available
           FROM investors ORDER BY capital_available DESC, name"""
    ).fetchall()
    total = sum(r["capital_available"] or 0 for r in rows)
    return render_template(
        "capital.html",
        title="Capital available to deploy",
        nav="capital",
        investors=rows,
        total=total,
    )


@app.route("/nate")
@staff_required
def nate_fees():
    investors = db().execute("SELECT id, name FROM investors ORDER BY name").fetchall()
    books = [dict(investor_books(i["id"]), name=i["name"], id=i["id"]) for i in investors]
    total = sum(b["nate_all"] for b in books)
    ytd = sum(b["nate_ytd"] for b in books)
    return render_template("nate.html", title="Nate Holland fee", nav="nate", books=books, total=total, ytd=ytd)


@app.route("/cron/reminders")
def cron_reminders():
    expected = os.environ.get("CRON_SECRET", "brittco-cron")
    if request.args.get("key") != expected:
        return "Forbidden", 403
    alerts = loan_alerts()
    sent = post_reminders(alerts)
    return {"ok": True, "alerts": len(alerts), "new_reminders": sent}


@app.route("/admin/forms")
@staff_required
def admin_forms():
    return render_template(
        "forms_admin.html",
        title="Forms",
        nav="forms",
        forms=load_form_defs(),
        saved=request.args.get("saved"),
    )


@app.route("/admin/forms/<key>", methods=["GET", "POST"])
@staff_required
def admin_form_edit(key):
    seed_form_templates()
    row = db().execute("SELECT * FROM form_templates WHERE form_key=?", (key,)).fetchone()
    if not row:
        return "Unknown form.", 404
    if request.method == "POST":
        db().execute(
            """UPDATE form_templates SET title=?, blurb=?, body=?, updated_at=? WHERE form_key=?""",
            (
                request.form.get("title") or row["title"],
                request.form.get("blurb") or row["blurb"],
                request.form.get("body") or row["body"],
                datetime.now().isoformat(timespec="minutes"),
                key,
            ),
        )
        db().commit()
        return redirect(url_for("admin_forms", saved=1))
    return render_template(
        "form_edit.html",
        title="Edit form",
        nav="forms",
        row=row,
        fields=json.loads(row["fields_json"] or "[]"),
    )


if __name__ == "__main__":
    init_db()
    print("Brittco Capital Inc system is running at http://127.0.0.1:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
