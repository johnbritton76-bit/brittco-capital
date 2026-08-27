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
        CREATE TABLE IF NOT EXISTS investors (
            id INTEGER PRIMARY KEY,
            name TEXT,
            entity_name TEXT,
            email TEXT,
            phone TEXT,
            notes TEXT,
            ach_status TEXT,
            password TEXT
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
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS extensions (
            id INTEGER PRIMARY KEY,
            participation_id INTEGER,
            loan_id INTEGER,
            months INTEGER,
            rate REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS distributions (
            id INTEGER PRIMARY KEY,
            payment_id INTEGER,
            loan_id INTEGER,
            investor_id INTEGER,
            investor_amount REAL,
            brittco_amount REAL,
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
    c.close()


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


def participation_math(p):
    term = p["term_months"] or 0
    used = p["extensions_used"] or 0
    base = money(p["investor_rate"])
    ext_rate = money(p["extension_rate"])
    ext_return = used * ext_rate
    total_return = base + ext_return
    total_months = term + used
    return {
        "term": term,
        "used": used,
        "max_ext": p["max_extensions"] or 0,
        "base": base,
        "ext_rate": ext_rate,
        "ext_return": ext_return,
        "total_return": total_return,
        "total_months": total_months,
        "annualized_initial": annualized(base, term),
        "annualized_actual": annualized(total_return, total_months) if total_months else annualized(base, term),
    }


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
        rows.append(
            {
                "investor_id": p["investor_id"],
                "investor_name": p["investor_name"],
                "investor_amount": round(inv_amt, 2),
                "brittco_amount": round(brit_amt, 2),
                "participation_id": p["id"],
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
    raw_parts, funded = loan_stack(lid)
    parts = []
    for p in raw_parts:
        d = dict(p)
        d.update(participation_math(p))
        parts.append(d)
    dists = db().execute(
        """SELECT d.*, i.name AS investor_name
           FROM distributions d LEFT JOIN investors i ON i.id=d.investor_id
           WHERE d.loan_id=? ORDER BY d.id DESC""",
        (lid,),
    ).fetchall()
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
            (payment_id, loan_id, investor_id, investor_amount, brittco_amount, kind, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (
                pay_id,
                lid,
                row["investor_id"],
                row["investor_amount"],
                row["brittco_amount"],
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
    return redirect(url_for("credit"))


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
            """INSERT INTO investors (name, entity_name, email, phone, notes, ach_status, password)
               VALUES (?,?,?,?,?,?,?)""",
            (
                f.get("name"),
                f.get("entity_name"),
                f.get("email"),
                f.get("phone"),
                f.get("notes"),
                "Not connected",
                f.get("password") or "investor",
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
    return render_template(
        "investor_detail.html",
        title=inv["name"],
        nav="investors",
        inv=inv,
        parts=parts,
        dists=dists,
        ach=ach,
        dwolla_ready=bool(os.environ.get("ACH_API_KEY")),
    )


@app.route("/loans/<int:lid>/participate", methods=["POST"])
@staff_required
def add_participation(lid):
    f = request.form
    db().execute(
        """INSERT INTO participations
        (loan_id, investor_id, amount, investor_rate, term_months, extension_rate,
         max_extensions, extensions_used, status, funded_on, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
    return render_template("investor_portal.html", inv=inv, parts=parts, exts=exts)


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
