#!/usr/bin/env python3
"""Brittco Capital Inc — CRM + underwriting + borrower portal."""
import os
import sqlite3
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, g, redirect, render_template, request, session, url_for, flash
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "brittco.db")

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
            notes TEXT
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
            created_at TEXT
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
    c.close()


# Initialize database when the app starts (works with gunicorn on Render)
init_db()


def staff_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("staff_id"):
            return redirect(url_for("login"))
        return fn(*a, **k)

    return wrap


def borrower_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("borrower_id"):
            return redirect(url_for("portal_login"))
        return fn(*a, **k)

    return wrap


def money(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


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
    stats = {
        "active": sum(1 for d in deals if d["status"] not in ("Declined", "Paid Off")),
        "uw": sum(1 for d in deals if d["status"] == "Underwriting"),
        "borrowers": db().execute("SELECT COUNT(*) c FROM borrowers").fetchone()["c"],
        "funded": funded,
        "loans": book["c"],
        "servicing": book["bal"],
        "alerts": len(alerts),
    }
    return render_template(
        "dashboard.html", title="Dashboard", nav="dash", deals=deals, stats=stats, alerts=alerts
    )


@app.route("/borrowers")
@staff_required
def borrowers():
    rows = db().execute(
        """SELECT b.*, (SELECT COUNT(*) FROM deals d WHERE d.borrower_id=b.id) deal_count
           FROM borrowers b ORDER BY b.name"""
    ).fetchall()
    return render_template(
        "borrowers.html", title="Borrowers", nav="borrowers", borrowers=rows
    )


@app.route("/borrowers/new", methods=["GET", "POST"])
@staff_required
def borrower_new():
    if request.method == "POST":
        f = request.form
        db().execute(
            """INSERT INTO borrowers
            (name, entity_type, entity_name, email, phone, credit_score, password, notes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                f.get("name"),
                f.get("entity_type"),
                f.get("entity_name"),
                (f.get("email") or "").strip().lower(),
                f.get("phone"),
                int(f["credit_score"]) if f.get("credit_score") else None,
                f.get("password") or "borrower",
                f.get("notes"),
            ),
        )
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
    return render_template(
        "borrower_detail.html", title=b["name"], nav="borrowers", b=b, deals=deals
    )


@app.route("/borrowers/<int:bid>/edit", methods=["GET", "POST"])
@staff_required
def borrower_edit(bid):
    b = db().execute("SELECT * FROM borrowers WHERE id=?", (bid,)).fetchone()
    if request.method == "POST":
        f = request.form
        pw = f.get("password") or b["password"]
        db().execute(
            """UPDATE borrowers SET name=?, entity_type=?, entity_name=?, email=?,
               phone=?, credit_score=?, password=?, notes=? WHERE id=?""",
            (
                f.get("name"),
                f.get("entity_type"),
                f.get("entity_name"),
                (f.get("email") or "").strip().lower(),
                f.get("phone"),
                int(f["credit_score"]) if f.get("credit_score") else None,
                pw,
                f.get("notes"),
                bid,
            ),
        )
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
        return deal_id
    cur = db().execute(
        """INSERT INTO deals
        (borrower_id, loan_type, address, purchase_price, as_is_value, arv, rehab_budget,
         loan_amount, rate, points, term_months, status, exit_strategy, notes,
         ltv_override_reason, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        fields + (datetime.now().isoformat(timespec="minutes"),),
    )
    db().commit()
    return cur.lastrowid


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
    return render_template(
        "deal_detail.html", title=d["address"], nav="deals", d=d, uw=uw, messages=messages
    )


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
    return render_template("portal.html", b=b, deals=deals, loans=loans, messages=messages, flash=None)


@app.route("/portal/apply", methods=["POST"])
@borrower_required
def portal_apply():
    f = request.form
    db().execute(
        """INSERT INTO deals
        (borrower_id, loan_type, address, purchase_price, as_is_value, arv, rehab_budget,
         loan_amount, rate, points, term_months, status, exit_strategy, notes,
         ltv_override_reason, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        ),
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
    perf = {
        "paid": paid,
        "due_in": days_until(loan["next_payment_due"]),
        "matures_in": days_until(loan["maturity_date"]),
        "remaining": money(loan["current_balance"]),
    }
    return render_template(
        "loan_detail.html", title=loan["loan_number"], nav="loans", loan=loan, payments=payments, perf=perf
    )


@app.route("/loans/<int:lid>/payment", methods=["POST"])
@staff_required
def loan_payment(lid):
    f = request.form
    amt = money(f.get("amount"))
    loan = db().execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
    new_bal = max(0.0, money(loan["current_balance"]) - amt)
    status = "Paid Off" if new_bal <= 0 else loan["status"]
    nxt = f.get("next_payment_due") or loan["next_payment_due"]
    db().execute(
        "INSERT INTO payments (loan_id, paid_on, amount, applied_to, note) VALUES (?,?,?,?,?)",
        (lid, f.get("paid_on") or date.today().isoformat(), amt, f.get("applied_to") or "Interest", f.get("note")),
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
    return redirect(url_for("credit"))


@app.route("/cron/reminders")
def cron_reminders():
    expected = os.environ.get("CRON_SECRET", "brittco-cron")
    if request.args.get("key") != expected:
        return "Forbidden", 403
    alerts = loan_alerts()
    sent = post_reminders(alerts)
    return {"ok": True, "alerts": len(alerts), "new_reminders": sent}


if __name__ == "__main__":
    init_db()
    print("Brittco Capital Inc system is running at http://127.0.0.1:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
