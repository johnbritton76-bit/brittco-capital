#!/usr/bin/env python3
"""
Brittco Capital Lending Desk
----------------------------
Starter loan origination / pipeline application for Brittco Capital Inc.

Run:
    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "brittco.db"

LOAN_TYPES = [
    "Fix & Flip",
    "New Construction",
    "Rental Bridge",
    "DSCR",
    "Refinance",
]
STATUSES = [
    "Lead",
    "Application",
    "Underwriting",
    "Approved",
    "Funded",
    "Declined",
    "Closed",
]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("BRITTCO_SECRET", "brittco-desk-dev-only")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                loan_type TEXT NOT NULL,
                borrower_name TEXT NOT NULL,
                entity_name TEXT,
                email TEXT,
                phone TEXT,
                property_address TEXT,
                city TEXT,
                state TEXT,
                purchase_price REAL DEFAULT 0,
                rehab_budget REAL DEFAULT 0,
                arv REAL DEFAULT 0,
                requested_loan REAL DEFAULT 0,
                monthly_rent REAL DEFAULT 0,
                experience_deals INTEGER DEFAULT 0,
                notes TEXT,
                ltv REAL
            )
            """
        )
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        if count == 0:
            seed = [
                (
                    "2026-08-12 09:14:00",
                    "Funded",
                    "Fix & Flip",
                    "Midwest Flip Partners LLC",
                    "Midwest Flip Partners LLC",
                    "deals@example.com",
                    "(816) 555-0142",
                    "4418 Prospect Ave",
                    "Kansas City",
                    "MO",
                    185000,
                    42000,
                    310000,
                    208000,
                    0,
                    14,
                    "Exit: list at ARV after kitchen/bath. 90% purchase + 100% rehab structure.",
                    67.1,
                ),
                (
                    "2026-09-02 11:02:00",
                    "Underwriting",
                    "New Construction",
                    "Sarah Nguyen",
                    "Nguyen Build Co",
                    "snguyen@example.com",
                    "(941) 555-0190",
                    "Vacant lot — Vasca corridor",
                    "Sarasota",
                    "FL",
                    140000,
                    410000,
                    720000,
                    420000,
                    0,
                    6,
                    "Ground-up SFR. Draw schedule on foundation, dry-in, CO.",
                    58.3,
                ),
                (
                    "2026-09-10 16:40:00",
                    "Application",
                    "DSCR",
                    "Jeffery B",
                    "JB Holdings WA",
                    "jb@example.com",
                    "+1-604-555-0108",
                    "812 Maple Ridge",
                    "Tampa",
                    "FL",
                    365000,
                    0,
                    365000,
                    292000,
                    3100,
                    9,
                    "Foreign national investor. DSCR on in-place lease. Impounds TI.",
                    80.0,
                ),
                (
                    "2026-09-14 08:22:00",
                    "Lead",
                    "Rental Bridge",
                    "Yanira Suarez",
                    "Suarez Capital",
                    "ys@example.com",
                    "(305) 555-0177",
                    "2201 Brickell Ave #14",
                    "Miami",
                    "FL",
                    540000,
                    18000,
                    575000,
                    450000,
                    4200,
                    22,
                    "Bridge to DSCR after lease-up. Light cosmetic.",
                    78.3,
                ),
            ]
            db.executemany(
                """
                INSERT INTO deals (
                    created_at, status, loan_type, borrower_name, entity_name,
                    email, phone, property_address, city, state,
                    purchase_price, rehab_budget, arv, requested_loan,
                    monthly_rent, experience_deals, notes, ltv
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                seed,
            )
            db.commit()


def fnum(name: str, default: float = 0.0) -> float:
    raw = request.form.get(name, "")
    try:
        return float(raw) if raw not in ("", None) else default
    except ValueError:
        return default


def compute_metrics(deal) -> dict:
    purchase = float(deal["purchase_price"] or 0)
    rehab = float(deal["rehab_budget"] or 0)
    arv = float(deal["arv"] or 0)
    loan = float(deal["requested_loan"] or 0)
    rent = float(deal["monthly_rent"] or 0)
    cost = purchase + rehab
    value = arv if arv else purchase
    ltv = (loan / value * 100) if value else 0.0
    ltc = (loan / cost * 100) if cost else 0.0

    # Rough IO payment at product starting rate
    rate = 0.1099 if deal["loan_type"] == "New Construction" else 0.0999
    io = loan * rate / 12
    dscr = None
    if io and rent:
        # crude: rent / (IO + estimated TI placeholder 0)
        dscr = round(rent / io, 2)

    loan_type = deal["loan_type"]
    flags = []
    if loan_type in ("Fix & Flip", "Rental Bridge"):
        max_loan = purchase * 0.90 + rehab * 1.00
        if loan > max_loan + 1:
            flags.append("Requested loan exceeds 90% purchase + 100% rehab guideline.")
        else:
            flags.append("Within Fix & Flip / Bridge LTV guideline (90% purchase + 100% rehab).")
        flags.append("Starting rate 9.99%. Typical term 6–24 months. Target close 7–10 days.")
    elif loan_type == "New Construction":
        max_loan = cost * 0.80
        if loan > max_loan + 1:
            flags.append("Requested loan exceeds 80% of project cost guideline.")
        else:
            flags.append("Within construction LTC guideline (80% of land + soft + hard).")
        flags.append("Starting rate 10.99%. Typical term 12–24 months. Draws on milestones.")
    elif loan_type == "DSCR":
        max_loan = purchase * 0.80
        if loan > max_loan + 1:
            flags.append("Requested loan exceeds 80% purchase (75% if cash-out) guideline.")
        else:
            flags.append("Within DSCR LTV guideline (up to 80% purchase).")
        flags.append("No borrower income verification. Impounds for taxes and insurance.")
        if dscr is not None:
            flags.append(f"Indicative IO DSCR {dscr}. Desk typically wants ≥ 1.00–1.20 depending on file.")
    else:
        flags.append("Review structure against the specific refinance / custom program.")

    if deal["experience_deals"] is not None and int(deal["experience_deals"] or 0) < 3:
        flags.append("Light experience — consider tighter leverage or additional reserves.")

    return {
        "project_cost": cost,
        "ltv": ltv,
        "ltc": ltc,
        "dscr": dscr,
        "guidance": " ".join(flags),
    }


def calc_ltv(purchase: float, rehab: float, arv: float, loan: float) -> float:
    value = arv if arv else purchase
    return (loan / value * 100) if value else 0.0


@app.route("/")
def dashboard():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM deals ORDER BY datetime(created_at) DESC LIMIT 8"
    ).fetchall()
    counts = {"total": 0, "underwriting": 0, "approved": 0}
    funded_volume = 0.0
    for r in db.execute("SELECT status, requested_loan FROM deals"):
        if r["status"] not in ("Declined", "Closed"):
            counts["total"] += 1
        if r["status"] == "Underwriting":
            counts["underwriting"] += 1
        if r["status"] == "Approved":
            counts["approved"] += 1
        if r["status"] in ("Funded", "Closed"):
            funded_volume += float(r["requested_loan"] or 0)
    return render_template(
        "dashboard.html", recent=rows, counts=counts, funded_volume=funded_volume
    )


@app.route("/pipeline")
def pipeline():
    status = request.args.get("status", "")
    db = get_db()
    if status and status in STATUSES:
        deals = db.execute(
            "SELECT * FROM deals WHERE status = ? ORDER BY datetime(created_at) DESC",
            (status,),
        ).fetchall()
    else:
        deals = db.execute(
            "SELECT * FROM deals ORDER BY datetime(created_at) DESC"
        ).fetchall()
    return render_template(
        "pipeline.html", deals=deals, statuses=STATUSES, status_filter=status
    )


@app.route("/deals/new", methods=["GET", "POST"])
def new_deal():
    if request.method == "POST":
        purchase = fnum("purchase_price")
        rehab = fnum("rehab_budget")
        arv = fnum("arv")
        loan = fnum("requested_loan")
        ltv = calc_ltv(purchase, rehab, arv, loan)
        db = get_db()
        db.execute(
            """
            INSERT INTO deals (
                created_at, status, loan_type, borrower_name, entity_name,
                email, phone, property_address, city, state,
                purchase_price, rehab_budget, arv, requested_loan,
                monthly_rent, experience_deals, notes, ltv
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                request.form.get("status") or "Lead",
                request.form.get("loan_type") or "Fix & Flip",
                (request.form.get("borrower_name") or "").strip() or "Unnamed",
                request.form.get("entity_name"),
                request.form.get("email"),
                request.form.get("phone"),
                request.form.get("property_address"),
                request.form.get("city"),
                (request.form.get("state") or "").upper(),
                purchase,
                rehab,
                arv,
                loan,
                fnum("monthly_rent"),
                int(fnum("experience_deals")),
                request.form.get("notes"),
                ltv,
            ),
        )
        db.commit()
        flash("Deal saved to the pipeline.")
        return redirect(url_for("pipeline"))
    return render_template("new.html", loan_types=LOAN_TYPES, statuses=STATUSES)


@app.route("/deals/<int:deal_id>")
def deal_detail(deal_id: int):
    db = get_db()
    deal = db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    if deal is None:
        flash("Deal not found.")
        return redirect(url_for("pipeline"))
    return render_template(
        "detail.html", deal=deal, statuses=STATUSES, metrics=compute_metrics(deal)
    )


@app.route("/deals/<int:deal_id>/status", methods=["POST"])
def update_status(deal_id: int):
    status = request.form.get("status")
    if status in STATUSES:
        db = get_db()
        db.execute("UPDATE deals SET status = ? WHERE id = ?", (status, deal_id))
        db.commit()
        flash(f"Status updated to {status}.")
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/calculator", methods=["GET", "POST"])
def calculator():
    form = {
        "product": "Fix & Flip",
        "rate": 9.99,
        "purchase": 200000,
        "rehab": 40000,
        "arv": 320000,
        "loan": 220000,
        "term": 12,
        "points": 2.0,
        "rent": 0,
        "ti": 4800,
    }
    result = None
    if request.method == "POST":
        for key in form:
            if key in ("product",):
                form[key] = request.form.get(key) or form[key]
            else:
                form[key] = fnum(key, form[key])
        purchase = form["purchase"]
        rehab = form["rehab"]
        arv = form["arv"]
        loan = form["loan"]
        cost = purchase + rehab
        value = arv if arv else purchase
        ltv = (loan / value * 100) if value else 0
        ltc = (loan / cost * 100) if cost else 0
        rate = form["rate"] / 100.0
        io = loan * rate / 12
        points_usd = loan * (form["points"] / 100.0)
        product = form["product"]
        if product in ("Fix & Flip", "Rental Bridge"):
            max_loan = purchase * 0.90 + rehab * 1.00
            ok = loan <= max_loan + 1
            flag = (
                "Inside 90% purchase + 100% rehab guideline."
                if ok
                else "Above Fix & Flip / Bridge guideline."
            )
        elif product == "New Construction":
            max_loan = cost * 0.80
            ok = loan <= max_loan + 1
            flag = "Inside 80% project-cost guideline." if ok else "Above construction LTC guideline."
        else:
            max_loan = purchase * 0.80
            ok = loan <= max_loan + 1
            flag = "Inside 80% DSCR purchase guideline." if ok else "Above DSCR LTV guideline."
        dscr = None
        if form["rent"] and io:
            monthly_ti = form["ti"] / 12.0
            dscr = round(form["rent"] / (io + monthly_ti), 2)
        result = {
            "cost": cost,
            "ltv": ltv,
            "ltc": ltc,
            "max_loan": max_loan,
            "io_pmt": io,
            "points_usd": points_usd,
            "dscr": dscr,
            "flag": flag,
        }
    return render_template("calculator.html", form=form, result=result)


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
