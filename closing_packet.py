"""Loan applications: typed e-sign, state security instrument, and note with guaranty.

Staff choose the loan type, rate, and term, then send a magic link. The borrower
finishes the missing facts and types a legal name. That typed name is the
signature. Missouri loans use a deed of trust. Kansas loans use a mortgage.
Generated PDFs are working forms for Brittco and should be reviewed by counsel
before recording.
"""

import calendar
import json
import re
from datetime import date, datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

LOAN_TYPES = ("Fix and Flip", "Bridge", "Transactional Loan", "Gap Loan", "Custom Loan")

ADMIN_DISCLAIMER = (
    "Generated closing documents are working forms for Brittco Capital's use. "
    "Missouri loans use a deed of trust. Kansas loans use a mortgage. "
    "Have a licensed attorney review them before recording or reliance. "
    "This tool does not provide legal advice and is not a claim of bar admission."
)

PERJURY_NOTICE = (
    "Under penalties of perjury, I declare that the information in this application "
    "is true, correct, and complete to the best of my knowledge, and that typing my "
    "legal name is my electronic signature on the security instrument and the "
    "Secured Promissory Note and Personal Guaranty prepared from this application."
)

SPOUSE_PERJURY_NOTICE = (
    "Under penalties of perjury, I declare that I am the spouse named in this "
    "application and that typing my legal name is my electronic signature joining "
    "the Personal Guaranty as an additional guarantor."
)

BORROWER_FIELDS = (
    ("borrower_legal_name", "Borrower legal name", "text"),
    ("borrower_entity_type", "Borrower entity type", "text"),
    ("borrower_formation_state", "State of organization", "text"),
    ("borrower_notice_address", "Borrower notice address", "text"),
    ("signatory_name", "Name of the person signing", "text"),
    ("signatory_title", "Signatory title", "text"),
    ("property", "Property street address", "text"),
    ("county", "County where the property sits", "text"),
    ("state", "Property state", "text"),
    ("legal_description", "Legal description", "area"),
    ("trustee_name", "Trustee (or “to be named before recording”)", "text"),
    ("trustee_address", "Trustee address", "text"),
    ("insurance_amount", "Required insurance amount", "text"),
    ("effective_date", "Effective date", "date"),
    ("payment_day", "ACH / payment day of the month", "text"),
    ("guarantor_name", "Guarantor legal name", "text"),
    ("guarantor_address", "Guarantor address", "text"),
    ("marital_status", "Marital status", "marital"),
    ("spouse_name", "Spouse legal name", "text"),
    ("spouse_dob", "Spouse date of birth", "date"),
    ("spouse_ssn", "Spouse Social Security / TIN", "text"),
    ("borrower_phone", "Phone", "text"),
    ("borrower_email", "Email", "text"),
    ("borrower_ssn", "Social Security / TIN", "text"),
    ("borrower_dob", "Date of birth", "date"),
    ("borrower_ein", "EIN, if the borrower is an entity", "text"),
    ("mailing_address", "Mailing street", "text"),
    ("mailing_city", "Mailing city", "text"),
    ("mailing_state", "Mailing state", "text"),
    ("mailing_zip", "Mailing ZIP", "text"),
)

REQUIRED_FIELDS = (
    ("borrower_legal_name", "Borrower legal name"),
    ("borrower_entity_type", "Borrower entity type"),
    ("borrower_notice_address", "Borrower notice address"),
    ("signatory_name", "Name of the person signing"),
    ("property", "Property address"),
    ("county", "County"),
    ("state", "Property state"),
    ("legal_description", "Legal description"),
    ("effective_date", "Effective date"),
    ("guarantor_name", "Guarantor legal name"),
    ("guarantor_address", "Guarantor address"),
    ("marital_status", "Marital status"),
)


def ensure_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS closing_applications (
            id INTEGER PRIMARY KEY,
            token TEXT UNIQUE,
            borrower_id INTEGER,
            deal_id INTEGER,
            loan_id INTEGER,
            status TEXT,
            loan_type TEXT,
            interest_rate TEXT,
            term_months INTEGER,
            term_days INTEGER,
            points TEXT,
            extension_count INTEGER,
            extension_rate TEXT,
            loan_amount TEXT,
            payload TEXT,
            signatures TEXT,
            review_note TEXT,
            reviewed_by TEXT,
            dot_filename TEXT,
            note_filename TEXT,
            created_at TEXT,
            submitted_at TEXT,
            reviewed_at TEXT,
            title_sent_at TEXT,
            title_email TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS state_document_templates (
            id INTEGER PRIMARY KEY,
            state_code TEXT NOT NULL,
            instrument_type TEXT NOT NULL,
            title TEXT,
            body TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_state_tpl_version
           ON state_document_templates (state_code, instrument_type, version)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_state_tpl_active
           ON state_document_templates (state_code, instrument_type)
           WHERE active = 1"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS closing_date_edits (
            id INTEGER PRIMARY KEY,
            closing_id INTEGER,
            staff_name TEXT,
            changed_at TEXT,
            changes_json TEXT
        )"""
    )
    seed_state_templates(conn)


def spouse_required(data):
    """Spouse must sign the guaranty when the borrower is married or a spouse is named."""
    status = (data.get("marital_status") or "").strip().casefold()
    if status == "married":
        return True
    return bool((data.get("spouse_name") or "").strip())


def norm_name(value):
    return " ".join((value or "").split()).casefold()


def _num(value):
    try:
        return float(str(value or "0").replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def clean_num(value):
    n = _num(value)
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    text = f"{n:.4f}".rstrip("0").rstrip(".")
    return text


def figures(value):
    return f"{_num(value):,.2f}"


def money_words(n):
    n = int(round(_num(n)))
    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def chunk(x):
        if x < 20:
            return ones[x]
        if x < 100:
            return (tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")).strip()
        return ones[x // 100] + " Hundred" + ((" " + chunk(x % 100)) if x % 100 else "")

    if n == 0:
        return "Zero"
    parts = []
    remainder = n
    for val, name in ((1_000_000_000, "Billion"), (1_000_000, "Million"), (1000, "Thousand")):
        if remainder >= val:
            parts.append(chunk(remainder // val) + " " + name)
            remainder %= val
    if remainder:
        parts.append(chunk(remainder))
    return " ".join(parts)


def amount_words(value):
    n = _num(value)
    dollars = int(n)
    cents = int(round((n - dollars) * 100))
    if cents == 100:
        dollars += 1
        cents = 0
    return f"{money_words(dollars)} and {cents:02d}/100"


def parse_iso(value):
    raw = (value or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _flag(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class DocumentDateError(ValueError):
    """A staff date edit that should be shown and not saved."""


def long_date(value):
    parsed = parse_iso(value)
    if not parsed:
        return (value or "").strip() or "____________________"
    return parsed.strftime("%B ") + str(parsed.day) + parsed.strftime(", %Y")


def add_months(start, months):
    months = int(months or 0)
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_business_days(start, n):
    cur = start
    added = 0
    count = int(n or 0)
    while added < count:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def ssn_last4(value):
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    return ""


def term_label(data):
    days = int(_num(data.get("term_days")))
    months = int(_num(data.get("term_months")))
    if days and not months:
        return f"{days} business days"
    if months and days:
        return f"{months} months or {days} business days, whichever is stated as the maturity"
    if months:
        return f"{months} months"
    return "the term stated in this Note"


def rate_label(data):
    kind = data.get("loan_type") or ""
    rate = clean_num(data.get("interest_rate"))
    points = clean_num(data.get("points"))
    if kind == "Transactional Loan":
        return f"{points}% flat fee, due at payoff"
    if kind == "Gap Loan":
        return f"{rate}% flat for the term"
    return f"{rate}% interest"


def terms_summary(data):
    kind = data.get("loan_type") or "Loan"
    bits = [
        kind,
        rate_label(data),
        term_label(data),
        f"principal ${figures(data.get('note_principal'))}",
    ]
    ext = int(_num(data.get("extension_count")))
    if ext:
        bits.append(
            f"{ext} one-month extension(s) at {clean_num(data.get('extension_rate'))}% of principal each, if agreed"
        )
    return ". ".join(bits) + "."


def derive(data):
    """Fill computed loan figures from staff terms and the effective date."""
    out = dict(data or {})
    principal = _num(out.get("note_principal") or out.get("loan_amount"))
    out["note_principal"] = figures(principal) if principal else ""
    out["secured_amount"] = out["note_principal"]
    out["note_principal_words"] = amount_words(principal) if principal else ""
    out["secured_amount_words"] = out["note_principal_words"]
    points = _num(out.get("points"))
    fee = principal * points / 100.0 if principal and points else 0
    out["profit_fee"] = figures(fee) if fee else "0.00"
    out["profit_fee_words"] = amount_words(fee) if fee else "Zero and 00/100"
    if not (out.get("insurance_amount") or "").strip() and principal:
        out["insurance_amount"] = figures(principal)
    if not (out.get("state") or "").strip():
        out["state"] = "Missouri"
    if not (out.get("payment_day") or "").strip():
        out["payment_day"] = "1"
    if not (out.get("late_charge_rate") or "").strip():
        out["late_charge_rate"] = "10"
    start = parse_iso(out.get("effective_date")) or date.today()
    out["effective_date"] = start.isoformat()
    days = int(_num(out.get("term_days")))
    months = int(_num(out.get("term_months")))
    pinned_maturity = parse_iso(out.get("maturity_date")) if _flag(out.get("maturity_override")) else None
    if pinned_maturity:
        maturity = pinned_maturity
    elif (out.get("loan_type") == "Transactional Loan" and days) or (days and not months):
        maturity = add_business_days(start, days)
    elif months:
        maturity = add_months(start, months)
    else:
        maturity = parse_iso(out.get("maturity_date")) or start
    out["maturity_date"] = maturity.isoformat()
    ext_n = int(_num(out.get("extension_count")))
    pinned_outside = parse_iso(out.get("outside_date")) if _flag(out.get("outside_override")) else None
    if pinned_outside:
        out["outside_date"] = pinned_outside.isoformat()
    else:
        out["outside_date"] = add_months(maturity, ext_n).isoformat() if ext_n else maturity.isoformat()
    ext_rate = _num(out.get("extension_rate"))
    ext_pay = principal * ext_rate / 100.0 if principal and ext_rate else 0
    out["extension_payment"] = figures(ext_pay) if ext_pay else "0.00"
    annual = _num(out.get("interest_rate"))
    monthly = principal * annual / 100.0 / 12.0 if principal and annual else 0
    late_base = monthly or ext_pay or fee
    late_pct = _num(out.get("late_charge_rate")) or 0
    late_day = late_base * late_pct / 100.0
    out["late_charge_per_day"] = figures(late_day) if late_day else "0.00"
    if not (out.get("trustee_name") or "").strip():
        out["trustee_name"] = "a trustee to be named by Lender before recording"
    return out


# Dates the closing PDFs can print. effective_date is the closing date and the note date.
DOCUMENT_DATES = (
    (
        "effective_date",
        "Closing / note date",
        ("execution_date", "origination_date", "effective_date"),
        "Closing date and note date. Prints as the “made as of” date and as the date on the note.",
    ),
    (
        "maturity_date",
        "Maturity date",
        ("maturity_date",),
        "Prints as the maturity date on the note and the security instrument.",
    ),
    (
        "outside_date",
        "Outside date",
        ("outside_date",),
        "Prints when the template includes the outside date, after every extension is used.",
    ),
    (
        "first_payment_date",
        "First payment date",
        ("first_payment_date",),
        "Prints when the template includes a first payment date.",
    ),
)


def _template_text(conn, data):
    code = resolve_state_code(data)
    if not code or conn is None:
        return ""
    chunks = []
    for instrument in instruments_for_state(code, conn):
        row = active_template(conn, code, instrument)
        if row and row["body"]:
            chunks.append(row["body"])
    return "\n".join(chunks)


def _token_used(text, token):
    return re.search(r"\{\{\s*" + re.escape(token) + r"\s*\}\}", text or "") is not None


def dates_on_documents(data, conn):
    """Date fields the active closing templates actually print, with current values."""
    derived = derive(data or {})
    text = _template_text(conn, derived)
    fields = []
    for key, label, tokens, hint in DOCUMENT_DATES:
        if any(_token_used(text, token) for token in tokens):
            fields.append({
                "key": key,
                "label": label,
                "hint": hint,
                "value": (derived.get(key) or "")[:10],
            })
    return fields


def apply_document_dates(data, submitted):
    """Set document dates exactly as staff entered them.

    A date left unchanged stays put. Changing the closing date does not move
    maturity, and a maturity that still matches the term is not pinned.
    Returns (payload, changes). changes lists only dates that differ.
    """
    source = dict(data or {})
    before = derive(source)
    working = dict(source)
    provided = {}
    for key, label, _tokens, _hint in DOCUMENT_DATES:
        if key not in submitted:
            continue
        raw = (submitted.get(key) or "").strip()
        parsed = parse_iso(raw)
        if not parsed:
            raise DocumentDateError(f"Enter a valid {label.lower()}.")
        provided[key] = parsed.isoformat()
    if not provided:
        raise DocumentDateError("Enter the document dates to update.")

    if "effective_date" in provided:
        working["effective_date"] = provided["effective_date"]
    if "maturity_date" in provided:
        unpinned = derive({**working, "maturity_override": ""})
        working["maturity_date"] = provided["maturity_date"]
        working["maturity_override"] = "" if provided["maturity_date"] == unpinned["maturity_date"] else "1"
    if "outside_date" in provided:
        outside_formula = derive({**working, "outside_override": ""})["outside_date"]
        working["outside_date"] = provided["outside_date"]
        working["outside_override"] = "" if provided["outside_date"] == outside_formula else "1"
    if "first_payment_date" in provided:
        working["first_payment_date"] = provided["first_payment_date"]

    after = derive(working)
    effective = parse_iso(after.get("effective_date"))
    maturity = parse_iso(after.get("maturity_date"))
    if effective and maturity and maturity < effective:
        raise DocumentDateError("Maturity date has to be on or after the closing / note date.")
    if "outside_date" in provided:
        outside = parse_iso(provided["outside_date"])
        if outside and maturity and outside < maturity:
            raise DocumentDateError("Outside date has to be on or after the maturity date.")
    if "first_payment_date" in provided:
        first = parse_iso(provided["first_payment_date"])
        if first and effective and first < effective:
            raise DocumentDateError("First payment date has to be on or after the closing / note date.")

    changes = []
    for key, label, _tokens, _hint in DOCUMENT_DATES:
        if key not in provided:
            continue
        old = (before.get(key) or "")[:10]
        new = (after.get(key) or "")[:10]
        if old == new:
            continue
        changes.append({
            "key": key,
            "label": label,
            "before": old,
            "after": new,
            "before_label": long_date(old),
            "after_label": long_date(new),
        })
    return after, changes


def blank_payload():
    today = date.today().isoformat()
    return derive({
        "loan_type": "Fix and Flip",
        "interest_rate": "10",
        "term_months": "4",
        "term_days": "0",
        "points": "0",
        "extension_count": "2",
        "extension_rate": "2",
        "note_principal": "",
        "effective_date": today,
        "state": "Missouri",
        "borrower_formation_state": "Missouri",
        "payment_day": "1",
        "marital_status": "",
        "lender_name": "Brittco Capital Inc",
        "lender_entity": "Florida corporation",
        "lender_notice_address": "4825 Vasca Drive, Sarasota, FL 34240",
        "lender_phone": "(816) 694-1658",
        "lender_email": "john@brittcocapital.com",
        "lender_officer": "John Britton, President",
    })


def submission_errors(data, form):
    """Return human labels for anything that blocks e-sign submit."""
    merged = dict(data or {})
    source = form or {}
    for key, _label, _kind in BORROWER_FIELDS:
        if key in source:
            merged[key] = (source.get(key) or "").strip()
    # A later borrower signature follows the term again. Staff date pins apply
    # only to the documents already generated.
    merged.pop("maturity_override", None)
    merged.pop("outside_override", None)
    merged = derive(merged)
    errors = []
    for key, label in REQUIRED_FIELDS:
        if not (merged.get(key) or "").strip():
            errors.append(label)
    if _num(merged.get("note_principal")) <= 0:
        errors.append("Loan amount")
    typed = (source.get("borrower_signature_name") or "").strip()
    if not typed:
        errors.append("Your typed legal name (signature)")
    elif norm_name(typed) != norm_name(merged.get("signatory_name")):
        errors.append("Typed signature must match the name of the person signing")
    if source.get("perjury_ack") != "yes":
        errors.append("Acknowledgment under penalties of perjury")
    if (merged.get("guarantor_name") or "").strip() and norm_name(merged.get("guarantor_name")) != norm_name(merged.get("signatory_name")):
        errors.append("Guarantor legal name must match the person signing")
    if spouse_required(merged):
        if not (merged.get("spouse_name") or "").strip():
            errors.append("Spouse legal name")
        spouse_typed = (source.get("spouse_signature_name") or "").strip()
        if not spouse_typed:
            errors.append("Spouse typed legal name (signature)")
        elif (merged.get("spouse_name") or "").strip() and norm_name(spouse_typed) != norm_name(merged.get("spouse_name")):
            errors.append("Spouse signature must match the spouse legal name")
        if source.get("spouse_perjury_ack") != "yes":
            errors.append("Spouse acknowledgment under penalties of perjury")
    return errors, merged


def signature_record(typed_name, signed_at, ip, user_agent):
    return {
        "typed_name": (typed_name or "").strip(),
        "signed_at": signed_at,
        "ip": ip or "",
        "user_agent": (user_agent or "")[:500],
    }


def build_signatures(data, form, signed_at, ip, user_agent):
    borrower = signature_record(form.get("borrower_signature_name"), signed_at, ip, user_agent)
    spouse = None
    if spouse_required(data):
        spouse = signature_record(form.get("spouse_signature_name"), signed_at, ip, user_agent)
    return {
        "notice": perjury_for(data),
        "spouse_notice": SPOUSE_PERJURY_NOTICE if spouse else "",
        "borrower": borrower,
        "spouse": spouse,
    }


def load_json(raw, fallback):
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    navy = colors.HexColor("#16324f")
    ink = colors.HexColor("#1c2430")
    muted = colors.HexColor("#5c6b7a")
    return {
        "kicker": ParagraphStyle(
            "Kicker", parent=base["Normal"], fontName="Times-Bold", fontSize=9,
            textColor=navy, alignment=TA_CENTER, spaceAfter=2,
        ),
        "title": ParagraphStyle(
            "DocTitle", parent=base["Normal"], fontName="Times-Bold", fontSize=16,
            leading=19, textColor=navy, alignment=TA_CENTER, spaceAfter=2,
        ),
        "sub": ParagraphStyle(
            "DocSub", parent=base["Normal"], fontName="Times-Italic", fontSize=10,
            leading=13, textColor=muted, alignment=TA_CENTER, spaceAfter=8,
        ),
        "h": ParagraphStyle(
            "SecHead", parent=base["Normal"], fontName="Times-Bold", fontSize=11,
            leading=14, textColor=navy, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"], fontName="Times-Roman", fontSize=10,
            leading=13.5, textColor=ink, alignment=TA_JUSTIFY, spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"], fontName="Times-Roman", fontSize=8.5,
            leading=11, textColor=muted, spaceAfter=4,
        ),
        "sig": ParagraphStyle(
            "Sig", parent=base["Normal"], fontName="Times-BoldItalic", fontSize=18,
            leading=22, textColor=colors.HexColor("#1a2744"), spaceBefore=2, spaceAfter=1,
        ),
        "label": ParagraphStyle(
            "Label", parent=base["Normal"], fontName="Times-Bold", fontSize=9,
            leading=12, textColor=navy, spaceBefore=6, spaceAfter=1,
        ),
    }


def _p(text, style):
    from reportlab.platypus import Paragraph

    safe = escape(text or "").replace("\n", "<br/>")
    return Paragraph(safe, style)


def _footer(canvas, doc):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch

    canvas.saveState()
    canvas.setStrokeColor(HexColor("#16324f"))
    canvas.setLineWidth(0.6)
    canvas.line(0.75 * inch, 0.52 * inch, letter[0] - 0.75 * inch, 0.52 * inch)
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(HexColor("#5c6b7a"))
    canvas.drawString(
        0.75 * inch,
        0.34 * inch,
        "Brittco Capital Inc  ·  Missouri working form  ·  Attorney review before recording",
    )
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.34 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _doc(title):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.7 * inch,
        title=title,
        author="Brittco Capital Inc",
        pageCompression=0,
    )
    return buf, doc


def _header(story, styles, title, subtitle, logo_path):
    import os

    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, Image, Spacer

    if logo_path and os.path.exists(logo_path):
        story.append(Image(logo_path, width=1.35 * inch, height=0.52 * inch, hAlign="CENTER"))
        story.append(Spacer(1, 6))
    story.append(_p("BRITTCO CAPITAL INC", styles["kicker"]))
    story.append(_p(title, styles["title"]))
    story.append(_p(subtitle, styles["sub"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#16324f"), spaceAfter=8))


def _signature_block(story, styles, role, person, title, meta):
    from reportlab.platypus import Spacer

    story.append(_p(role, styles["label"]))
    story.append(_p(meta.get("typed_name") or person or "", styles["sig"]))
    story.append(_p(
        "Electronic signature — legal name typed on the loan application under penalties of perjury.",
        styles["small"],
    ))
    bits = [f"Printed name: {person or meta.get('typed_name') or ''}"]
    if title:
        bits.append(title)
    if meta.get("signed_at"):
        bits.append("Signed " + long_date(meta.get("signed_at")[:10]) + " at " + meta.get("signed_at"))
    story.append(_p("  ·  ".join(b for b in bits if b), styles["small"]))
    story.append(Spacer(1, 6))


def _notary(story, styles, county, who, state="Missouri"):
    story.append(_p("NOTARY ACKNOWLEDGMENT  (to be completed at signing before a notary)", styles["h"]))
    story.append(_p(
        f"State of {state or 'Missouri'}\nCounty of {county or '____________________'}\n\n"
        "On this ______ day of ____________________, 20____, before me, the undersigned notary public, "
        f"personally appeared {who or '____________________'}, who proved identity by satisfactory evidence "
        "and acknowledged that they executed the foregoing instrument for the purposes stated in it.",
        styles["body"],
    ))
    story.append(_p(
        "Notary signature: ________________________________    Commission expires: ______________\n"
        "Notary printed name: ______________________________    Seal:",
        styles["body"],
    ))


def _parties(data):
    return (
        f"This instrument is made as of {long_date(data.get('effective_date'))}, by "
        f"{data.get('borrower_legal_name') or '____________________'}, "
        f"a {data.get('borrower_entity_type') or '____________________'} organized under the laws of "
        f"{data.get('borrower_formation_state') or '____________________'} "
        f"(“Borrower,” and for recording, “Grantor”), whose notice address is "
        f"{data.get('borrower_notice_address') or '____________________'}, "
        f"in favor of {data.get('lender_name') or 'Brittco Capital Inc'}, "
        f"a {data.get('lender_entity') or 'Florida corporation'} "
        f"(“Lender,” and for recording, “Beneficiary” and “Grantee”), whose notice address is "
        f"{data.get('lender_notice_address') or '____________________'}. "
        f"The trustee is {data.get('trustee_name') or 'a trustee to be named by Lender before recording'}"
        + (
            f", {data.get('trustee_address')}"
            if (data.get("trustee_address") or "").strip()
            else ""
        )
        + " (“Trustee”)."
    )


def _economics(data):
    kind = data.get("loan_type") or "Loan"
    lines = [
        f"Loan type: {kind}.",
        f"Principal: ${data.get('note_principal') or '0.00'} ({data.get('note_principal_words') or ''} United States Dollars).",
        f"Price: {rate_label(data)}.",
        f"Term: {term_label(data)}. Maturity date: {long_date(data.get('maturity_date'))}.",
    ]
    ext = int(_num(data.get("extension_count")))
    if ext:
        lines.append(
            f"Extensions: up to {ext} extension(s) of one month each. Each extension, if Lender accepts it, "
            f"requires a payment of {clean_num(data.get('extension_rate'))}% of outstanding principal "
            f"(${data.get('extension_payment')}), due on or before the then-current maturity date. "
            f"The outside date if every extension is used is {long_date(data.get('outside_date'))}."
        )
    else:
        lines.append("Extensions: none are pre-agreed. Any extension must be in a writing signed by Lender.")
    points = _num(data.get("points"))
    if points:
        lines.append(
            f"Fee: {clean_num(points)}% of principal, ${data.get('profit_fee')} "
            f"({data.get('profit_fee_words')} United States Dollars), earned under this Note and payable "
            "from the loan proceeds or at sale, refinance, or other payoff, as the settlement statement shows."
        )
    lines.append(
        f"When a payment is required before payoff, it is due on day {data.get('payment_day') or '1'} "
        "of the month by ACH. A late charge of "
        f"{clean_num(data.get('late_charge_rate'))}% per day of the missed installment "
        f"(${data.get('late_charge_per_day')} per day) applies in addition to the amount unpaid."
    )
    return " ".join(lines)


def build_deed_pdf(data, signatures, logo_path=None):
    from reportlab.platypus import Spacer

    data = derive(data)
    signatures = signatures or {}
    styles = _styles()
    buf, doc = _doc("Missouri Deed of Trust")
    story = []
    _header(
        story,
        styles,
        "DEED OF TRUST",
        "With power of sale  ·  State of Missouri",
        logo_path,
    )
    story.append(_p(_parties(data), styles["body"]))
    story.append(_p("1.  Obligations secured", styles["h"]))
    story.append(_p(
        "This Deed of Trust secures (a) a Secured Promissory Note of even date in the principal sum of "
        f"${data.get('note_principal')} ({data.get('note_principal_words')} United States Dollars), "
        "together with interest, fees, and extensions described in that Note; "
        "(b) the Personal Guaranty of even date, including the joinder of a spouse when the guaranty so provides; "
        "(c) future advances Lender makes to protect this security, complete rehabilitation, pay taxes or insurance, "
        "or cure a default, with interest; and (d) performance of every covenant in this Deed of Trust. "
        f"The principal amount stated above, ${data.get('secured_amount')} "
        f"({data.get('secured_amount_words')} United States Dollars), is the principal secured, "
        "exclusive of interest, protective advances, and costs.",
        styles["body"],
    ))
    story.append(_p("2.  Grant in trust", styles["h"]))
    state = data.get("state") or "Missouri"
    story.append(_p(
        f"For that purpose, Borrower irrevocably grants, bargains, sells, and conveys to Trustee, in trust, "
        f"WITH POWER OF SALE, the real property in the County of {data.get('county') or '____________________'}, "
        f"State of {state}, described as follows:",
        styles["body"],
    ))
    story.append(_p(data.get("legal_description") or "[Legal description to be inserted.]", styles["body"]))
    story.append(_p(
        f"Commonly known as {data.get('property') or '____________________'} (the “Property”). "
        "The conveyance includes all buildings and improvements now or later located on the Property; "
        "easements, hereditaments, and appurtenances; rents, issues, and profits; and fixtures.",
        styles["body"],
    ))
    story.append(_p(
        "Subject to building lines, easements, reservations, restrictions, covenants, and conditions of record, "
        "and to zoning and other laws affecting the Property. Borrower covenants that Borrower is lawfully seized "
        "of the estate conveyed, has the right to convey it, and will warrant and defend title against claims "
        "arising by or through Borrower, subject to exceptions in any title policy issued for this loan.",
        styles["body"],
    ))
    story.append(_p("3.  Borrower covenants", styles["h"]))
    story.append(_p(
        "Borrower shall pay the Note and perform the obligations secured. Borrower shall pay taxes, assessments, "
        "and charges that can gain priority over this Deed of Trust, except amounts contested in good faith "
        "with reserves Lender reasonably requires. Borrower shall keep the Property insured against fire and "
        f"other hazards customarily insured, in an amount not less than ${data.get('insurance_amount') or data.get('note_principal')}, "
        f"with a carrier authorized to write that insurance, and with a mortgagee clause in favor of Lender. "
        "Borrower shall keep the Property in good repair, shall not commit waste, and shall not permit a nuisance "
        "or an uncured code violation that materially impairs Lender’s security. Borrower shall not transfer, "
        "further encumber, or permit a lien subordinate or superior to this Deed of Trust without Lender’s prior "
        "written consent. A transfer without that consent is a default, and Lender may enforce this instrument "
        "after notice of not less than thirty (30) days.",
        styles["body"],
    ))
    story.append(_p(
        "Lender may inspect the Property on reasonable notice. If Borrower fails to perform a covenant, "
        "Lender may pay the amount or perform the act, and the cost, with interest, is added to the debt "
        "and secured by this Deed of Trust. Condemnation and insurance proceeds are assigned to Lender and "
        "applied to restoration if that is economically feasible and this security is not impaired; otherwise "
        "to the debt. Borrower assigns the rents of the Property to Lender as further security. After default "
        "or abandonment, Lender or a receiver may collect the rents and apply them to the cost of collection "
        "and then to the debt. Extensions or forbearance are not a waiver. Remedies are cumulative. "
        "Covenants bind successors and, where more than one person is Borrower, are joint and several.",
        styles["body"],
    ))
    story.append(_p("4.  Power of sale", styles["h"]))
    story.append(_p(
        "If Borrower defaults and the default continues after any notice and cure period required by this "
        "Deed of Trust or by law, Lender may declare the secured obligations immediately due and direct Trustee "
        "to sell the Property. Before acceleration, Lender shall mail notice to Borrower’s notice address "
        "specifying the default, the action required to cure it, and a date not less than thirty (30) days "
        "from mailing by which it must be cured. If the default is not cured, Lender may accelerate and invoke "
        "the power of sale.",
        styles["body"],
    ))
    if (state or "").strip().casefold() in ("missouri", "mo"):
        sale = (
            f"Trustee shall give notice of sale, and shall advertise the sale, in the manner required by the "
            f"laws of the State of Missouri for a deed of trust with power of sale, including Chapter 443 of "
            f"the Revised Statutes of Missouri, as amended. Trustee shall sell the Property at public vendue "
            f"to the highest bidder for cash at the usual place of foreclosure sale in {data.get('county') or 'the'} County, "
            "Missouri, or at the front door of the county courthouse, during usual sale hours. "
        )
    else:
        sale = (
            f"The Property is located in {state}. Foreclosure, recording, and perfection of the lien follow "
            f"the law of that jurisdiction. To the extent a power of sale is available there, Trustee shall "
            f"give the notice and conduct the sale that law requires. The Note, the Guaranty, and the contractual "
            "covenants of this instrument are governed by Missouri law. "
        )
    story.append(_p(
        sale
        + "Trustee may postpone the sale by public announcement. Trustee shall apply the proceeds to the costs "
        "of the sale, including Trustee’s fee and reasonable attorney’s fees, then to the obligations secured, "
        "and shall pay any surplus to the persons entitled to it. Lender may appoint a successor trustee by "
        "an instrument recorded in the land records. The recitals in the trustee’s deed are prima facie evidence "
        "of the facts recited, to the extent Missouri law gives them that effect.",
        styles["body"],
    ))
    story.append(_p("5.  Release, notices, and governing law", styles["h"]))
    story.append(_p(
        "When the obligations secured have been paid and performed, Lender shall request a release or deed of "
        "reconveyance. The party seeking the release pays the recording cost. Notices to Borrower are effective "
        "when mailed to the notice address. Notices to Lender are effective when mailed to Lender’s notice address. "
        "This Deed of Trust is governed by the laws of the State of Missouri, except where the law of the state "
        "in which the Property is located must control a foreclosure or the validity of the lien. Invalid provisions "
        "are severed. Borrower will receive a copy of this instrument.",
        styles["body"],
    ))
    story.append(_p(
        "Borrower has read this Deed of Trust and signed it electronically on the loan application, under "
        "penalties of perjury, by typing the legal name shown below. A notary acknowledgment is completed "
        "when the instrument is prepared for recording.",
        styles["body"],
    ))
    who = data.get("signatory_name") or ""
    capacity = data.get("signatory_title") or "Authorized signatory"
    _signature_block(
        story,
        styles,
        f"BORROWER: {data.get('borrower_legal_name') or ''}",
        who,
        capacity,
        signatures.get("borrower") or {},
    )
    _notary(story, styles, data.get("county"), who)
    story.append(Spacer(1, 8))
    story.append(_p(ADMIN_DISCLAIMER, styles["small"]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _note_payment_paragraph(data):
    kind = data.get("loan_type") or ""
    principal = f"${data.get('note_principal')} ({data.get('note_principal_words')} United States Dollars)"
    if kind == "Transactional Loan":
        return (
            f"Borrower promises to pay {principal} together with a flat fee of {clean_num(data.get('points'))}% "
            f"of that principal, which is ${data.get('profit_fee')} ({data.get('profit_fee_words')} United States Dollars). "
            f"The entire unpaid principal and fee are due on {long_date(data.get('maturity_date'))} "
            f"({term_label(data)} after {long_date(data.get('effective_date'))}). "
            "The fee is earned at funding. There is no pre-agreed interest rate beyond that fee unless a written "
            "extension signed by Lender states one."
        )
    if kind == "Gap Loan":
        return (
            f"Borrower promises to pay {principal}. The charge for this gap loan is "
            f"{clean_num(data.get('interest_rate'))}% of principal for the term, due in full on "
            f"{long_date(data.get('maturity_date'))}. Principal is advanced for the purpose described in the "
            "loan file and is used for the Property named above."
        )
    return (
        f"Borrower promises to pay {principal}, with interest at {clean_num(data.get('interest_rate'))}% "
        f"for {term_label(data)}. Interest is charged on principal advanced and outstanding. "
        f"All unpaid principal, interest, and fees are due on {long_date(data.get('maturity_date'))}. "
        "The loan proceeds are for acquisition, rehabilitation, holding costs, taxes, and fees for the Property, "
        "as the settlement statement and loan file describe."
    )


def build_note_pdf(data, signatures, logo_path=None):
    from reportlab.platypus import Spacer

    data = derive(data)
    signatures = signatures or {}
    styles = _styles()
    buf, doc = _doc("Missouri Secured Promissory Note and Personal Guaranty")
    story = []
    _header(
        story,
        styles,
        "SECURED PROMISSORY NOTE",
        "and Personal Guaranty  ·  State of Missouri",
        logo_path,
    )
    story.append(_p(
        f"Date: {long_date(data.get('effective_date'))}. "
        f"Loan type: {data.get('loan_type') or ''}. "
        f"Borrower: {data.get('borrower_legal_name') or ''}, a {data.get('borrower_entity_type') or ''}. "
        f"Collateral: {data.get('property') or ''}, {data.get('county') or ''} County, {data.get('state') or 'Missouri'}.",
        styles["body"],
    ))
    story.append(_p("1.  Promise to pay", styles["h"]))
    story.append(_p(
        f"For value received, Borrower promises to pay to the order of {data.get('lender_name') or 'Brittco Capital Inc'}, "
        f"{data.get('lender_notice_address') or ''}"
        + (f", {data.get('lender_phone')}" if (data.get("lender_phone") or "").strip() else "")
        + ", or any later holder (“Lender”), the amounts in this Note.",
        styles["body"],
    ))
    story.append(_p(_note_payment_paragraph(data), styles["body"]))
    story.append(_p(_economics(data), styles["body"]))
    ext = int(_num(data.get("extension_count")))
    if ext:
        story.append(_p(
            f"If the Property is not sold and the Note is not paid by {long_date(data.get('maturity_date'))}, "
            f"Borrower may request up to {ext} extension(s) of one month. Lender has no duty to grant an extension "
            f"unless the extension payment of ${data.get('extension_payment')} is received when due and Borrower is "
            "otherwise in compliance. If the Note remains unpaid after the outside date of "
            f"{long_date(data.get('outside_date'))}, Borrower shall, on Lender’s written request, execute a deed "
            "conveying Borrower’s interest in the Property to Lender or Lender’s designee, subject to liens of record "
            "and to applicable law.",
            styles["body"],
        ))
    story.append(_p("2.  Prepayment, default, and security", styles["h"]))
    story.append(_p(
        "This Note may be prepaid in whole or in part at any time without premium, unless the fee described above "
        "has been earned and remains unpaid, in which case that fee is paid with the prepayment. "
        "At the option of any holder, the unpaid balance is immediately due upon: (1) failure to pay any amount when due; "
        "(2) breach of this Note, the Deed of Trust, or the Guaranty; (3) breach of a senior loan document secured by "
        "the Property; (4) death, incapacity, dissolution, or liquidation of an obligor or guarantor; "
        "(5) an assignment for creditors, bankruptcy, or a receivership not dismissed within thirty (30) days; "
        "(6) failure to pay real estate taxes when due or to keep the insurance this loan requires. "
        "If this Note is placed for collection, Borrower shall pay reasonable attorney’s fees and costs to the extent "
        "Missouri law allows.",
        styles["body"],
    ))
    story.append(_p(
        f"This Note is secured by a Deed of Trust, with power of sale, on {data.get('property') or 'the Property'}, "
        f"in {data.get('county') or ''} County, {data.get('state') or 'Missouri'}. "
        "Borrower and every guarantor stay bound until this Note is paid, and waive demand, presentment, and protest, "
        "to the extent that waiver is permitted. A modification must be in writing signed by Lender. "
        "This Note is governed by the laws of the State of Missouri. "
        "To the extent permitted by law, Borrower waives trial by jury in a suit arising out of this Note, "
        "the Deed of Trust, or the Guaranty.",
        styles["body"],
    ))
    story.append(_p("3.  Personal guaranty", styles["h"]))
    story.append(_p(
        f"To induce Lender to make the loan, {data.get('guarantor_name') or '____________________'}, "
        f"whose address is {data.get('guarantor_address') or '____________________'} (“Guarantor”), "
        "unconditionally guarantees full payment and performance of this Note and of the Deed of Trust. "
        "Guarantor represents that Guarantor has authority to sign and that this Guaranty is a valid obligation. "
        "This Guaranty continues despite bankruptcy, reorganization, insolvency, or abandonment of the Property. "
        "Lender will first pursue its remedies against the Property and against Borrower before demanding payment "
        "from Guarantor, except where delay would materially impair the collateral or Borrower is in bankruptcy. "
        "Guarantor shall pay Lender’s reasonable attorney’s fees and collection costs to the extent Missouri law allows. "
        "If more than one person is Guarantor, liability is joint and several. Release of one Guarantor does not release "
        "the others. This Guaranty binds Guarantor’s heirs and assigns and benefits Lender and its successors. "
        "It is governed by the laws of the State of Missouri.",
        styles["body"],
    ))
    if spouse_required(data):
        last4 = ssn_last4(data.get("spouse_ssn"))
        tin = f" The spouse’s taxpayer identification number ends in {last4}." if last4 else ""
        dob = long_date(data.get("spouse_dob")) if (data.get("spouse_dob") or "").strip() else ""
        dob_bit = f" Spouse’s date of birth is {dob}." if dob and dob != "____________________" else ""
        story.append(_p(
            f"Guarantor’s spouse, {data.get('spouse_name')}, joins this Guaranty and signs as an additional Guarantor, "
            "jointly and severally. The spouse signs only as a guarantor and not as the borrower, unless the spouse "
            f"is also named as Borrower.{dob_bit}{tin}",
            styles["body"],
        ))
    story.append(_p(
        "The persons named below signed this Note and Guaranty electronically on the loan application, under "
        "penalties of perjury, by typing their legal names. A notary acknowledgment is completed when a wet-ink "
        "or notarized original is required for recording or for a title file.",
        styles["body"],
    ))
    _signature_block(
        story,
        styles,
        f"BORROWER: {data.get('borrower_legal_name') or ''}",
        data.get("signatory_name"),
        data.get("signatory_title") or "Authorized signatory",
        signatures.get("borrower") or {},
    )
    _signature_block(
        story,
        styles,
        "GUARANTOR",
        data.get("guarantor_name"),
        "Individually, as guarantor",
        signatures.get("borrower") or {},
    )
    if spouse_required(data):
        _signature_block(
            story,
            styles,
            "SPOUSE — ADDITIONAL GUARANTOR",
            data.get("spouse_name"),
            "Spouse, as guarantor only",
            signatures.get("spouse") or {},
        )
    _notary(
        story,
        styles,
        data.get("county"),
        data.get("signatory_name"),
    )
    story.append(Spacer(1, 8))
    story.append(_p(ADMIN_DISCLAIMER, styles["small"]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def package_zip(security_pdf, note_pdf, security_name="Deed-of-Trust.pdf", note_name="Note-and-Guaranty.pdf"):
    import zipfile

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(security_name, security_pdf)
        zf.writestr(note_name, note_pdf)
    return buf.getvalue()


_STATE_NAMES = {
    "AL": "AL", "ALABAMA": "AL",
    "AK": "AK", "ALASKA": "AK",
    "AZ": "AZ", "ARIZONA": "AZ",
    "AR": "AR", "ARKANSAS": "AR",
    "CA": "CA", "CALIFORNIA": "CA",
    "CO": "CO", "COLORADO": "CO",
    "CT": "CT", "CONNECTICUT": "CT",
    "DE": "DE", "DELAWARE": "DE",
    "FL": "FL", "FLORIDA": "FL",
    "GA": "GA", "GEORGIA": "GA",
    "HI": "HI", "HAWAII": "HI",
    "ID": "ID", "IDAHO": "ID",
    "IL": "IL", "ILLINOIS": "IL",
    "IN": "IN", "INDIANA": "IN",
    "IA": "IA", "IOWA": "IA",
    "KS": "KS", "KANSAS": "KS",
    "KY": "KY", "KENTUCKY": "KY",
    "LA": "LA", "LOUISIANA": "LA",
    "ME": "ME", "MAINE": "ME",
    "MD": "MD", "MARYLAND": "MD",
    "MA": "MA", "MASSACHUSETTS": "MA",
    "MI": "MI", "MICHIGAN": "MI",
    "MN": "MN", "MINNESOTA": "MN",
    "MS": "MS", "MISSISSIPPI": "MS",
    "MO": "MO", "MISSOURI": "MO",
    "MT": "MT", "MONTANA": "MT",
    "NE": "NE", "NEBRASKA": "NE",
    "NV": "NV", "NEVADA": "NV",
    "NH": "NH", "NEW HAMPSHIRE": "NH",
    "NJ": "NJ", "NEW JERSEY": "NJ",
    "NM": "NM", "NEW MEXICO": "NM",
    "NY": "NY", "NEW YORK": "NY",
    "NC": "NC", "NORTH CAROLINA": "NC",
    "ND": "ND", "NORTH DAKOTA": "ND",
    "OH": "OH", "OHIO": "OH",
    "OK": "OK", "OKLAHOMA": "OK",
    "OR": "OR", "OREGON": "OR",
    "PA": "PA", "PENNSYLVANIA": "PA",
    "RI": "RI", "RHODE ISLAND": "RI",
    "SC": "SC", "SOUTH CAROLINA": "SC",
    "SD": "SD", "SOUTH DAKOTA": "SD",
    "TN": "TN", "TENNESSEE": "TN",
    "TX": "TX", "TEXAS": "TX",
    "UT": "UT", "UTAH": "UT",
    "VT": "VT", "VERMONT": "VT",
    "VA": "VA", "VIRGINIA": "VA",
    "WA": "WA", "WASHINGTON": "WA",
    "WV": "WV", "WEST VIRGINIA": "WV",
    "WI": "WI", "WISCONSIN": "WI",
    "WY": "WY", "WYOMING": "WY",
    "DC": "DC", "DISTRICT OF COLUMBIA": "DC",
}
_STATE_CODES = {code for code in _STATE_NAMES.values()}
_CODE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def normalize_state(value):
    raw = " ".join((value or "").split()).upper()
    return _STATE_NAMES.get(raw, "")


def state_name(code):
    return _CODE_NAMES.get(code or "", "")


def state_from_text(value):
    """Pull a property state from an address without treating 'Kansas City' as Kansas."""
    text = (value or "").strip()
    if not text:
        return ""
    direct = normalize_state(text)
    if direct:
        return direct
    parts = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
    for part in reversed(parts):
        tokens = [tok.strip(".").strip() for tok in part.split() if tok.strip(".").strip()]
        for token in tokens:
            upper = token.upper()
            if len(upper) == 2 and upper in _STATE_CODES:
                return upper
        for index, token in enumerate(tokens):
            upper = token.upper()
            if upper not in _STATE_NAMES or len(upper) == 2:
                continue
            nxt = tokens[index + 1].upper() if index + 1 < len(tokens) else ""
            if nxt in ("CITY", "COUNTY"):
                continue
            return _STATE_NAMES[upper]
    return ""


def resolve_state_code(data):
    """Property state wins. Unknown explicit states do not fall back to Missouri."""
    data = data or {}
    explicit = (data.get("state") or "").strip()
    named = normalize_state(explicit)
    if named:
        return named
    if explicit:
        parsed = state_from_text(explicit)
        if parsed:
            return parsed
        if " " not in explicit:
            return ""
    parsed_prop = state_from_text(data.get("property") or data.get("property_address"))
    if parsed_prop:
        return parsed_prop
    coded = normalize_state(data.get("state_code"))
    if coded:
        return coded
    return "MO"


def _state_code(state):
    if state in ("MO", "KS"):
        return state
    return normalize_state(state)


def instruments_for_state(state, conn=None):
    """Pick the security instrument plus the note. Stored templates win over the MO/KS defaults."""
    code = _state_code(state)
    if conn is not None and code:
        rows = conn.execute(
            """SELECT instrument_type FROM state_document_templates
               WHERE state_code=? AND active=1""",
            (code,),
        ).fetchall()
        kinds = {row["instrument_type"] for row in rows}
        security = None
        if "mortgage" in kinds and "deed_of_trust" not in kinds:
            security = "mortgage"
        elif "deed_of_trust" in kinds and "mortgage" not in kinds:
            security = "deed_of_trust"
        elif code == "KS" and "mortgage" in kinds:
            security = "mortgage"
        elif code == "MO" and "deed_of_trust" in kinds:
            security = "deed_of_trust"
        elif "mortgage" in kinds:
            security = "mortgage"
        elif "deed_of_trust" in kinds:
            security = "deed_of_trust"
        if security and "note_guaranty" in kinds:
            return (security, "note_guaranty")
        return ()
    if code == "KS":
        return ("mortgage", "note_guaranty")
    if code == "MO":
        return ("deed_of_trust", "note_guaranty")
    return ()


def security_label(state):
    pair = instruments_for_state(state if state in ("MO", "KS") else resolve_state_code({"state": state}))
    if pair and pair[0] == "mortgage":
        return "Mortgage"
    if pair and pair[0] == "deed_of_trust":
        return "Deed of Trust"
    return "Security instrument"


def perjury_for(data):
    code = resolve_state_code(data)
    if code == "KS":
        instrument = "Kansas Mortgage"
        state = "Kansas"
    elif code == "MO":
        instrument = "Deed of Trust"
        state = "Missouri"
    else:
        instrument = "security instrument"
        state = (data or {}).get("state") or "the applicable state"
    return (
        "Under penalties of perjury, I declare that the information in this application "
        "is true, correct, and complete to the best of my knowledge, and that typing my "
        f"legal name is my electronic signature on the {state} {instrument} and the "
        f"{state} Secured Promissory Note and Personal Guaranty prepared from this application."
    )


def _now():
    return datetime.now().isoformat(timespec="minutes")


_MO_DEED = """DEED OF TRUST
With power of sale. State of Missouri.

This Deed of Trust is made as of {{execution_date}} by {{borrower_legal_name}}, a {{borrower_entity_type}} organized under the laws of {{borrower_formation_state}} ("Borrower" and "Grantor"), whose notice address is {{borrower_mailing_address}}, in favor of {{lender_legal_name}} ("Lender" and "Beneficiary"), whose notice address is {{lender_address}}. The trustee is a trustee to be named by Lender before recording ("Trustee").

1. Obligations secured

This Deed of Trust secures (a) a Secured Promissory Note of even date in the principal sum of ${{note_principal}} ({{note_principal_words}} United States Dollars), together with interest and fees; (b) the Personal Guaranty of even date; (c) future advances Lender makes to protect this security, pay taxes or insurance, or cure a default; and (d) every covenant in this Deed of Trust.

2. Grant in trust

Borrower irrevocably grants, bargains, sells, and conveys to Trustee, in trust, WITH POWER OF SALE, the real property in the County of {{property_county}}, State of Missouri, described as follows:

{{property_legal_description}}

Commonly known as {{property_address}} (the "Property"). The conveyance includes buildings, improvements, easements, rents, issues, profits, and fixtures.

Borrower covenants that Borrower is lawfully seized of the estate conveyed, has the right to convey it, and will warrant and defend title against claims arising by or through Borrower, subject to exceptions in any title policy issued for this loan.

3. Borrower covenants

Borrower shall pay the Note and perform the obligations secured. Borrower shall pay taxes, assessments, and charges that can gain priority, except amounts contested in good faith with reserves Lender reasonably requires. Borrower shall keep the Property insured against fire and other customary hazards for not less than ${{note_principal}}, with a mortgagee clause in favor of Lender. Borrower shall keep the Property in good repair, shall not commit waste, and shall not transfer or further encumber the Property without Lender's prior written consent. A transfer without that consent is a default, and Lender may enforce this instrument after notice of not less than thirty (30) days.

Lender may inspect the Property on reasonable notice. Insurance and condemnation proceeds are assigned to Lender. Borrower assigns rents as further security. After default, Lender or a receiver may collect the rents and apply them to the debt.

4. Power of sale

If Borrower defaults and the default continues after the notice and cure period required by this Deed of Trust or by law, Lender may declare the secured obligations immediately due and direct Trustee to sell the Property. Before acceleration, Lender shall mail notice specifying the default, the action required to cure it, and a date not less than thirty (30) days from mailing by which it must be cured.

Trustee shall give notice of sale, and shall advertise the sale, in the manner required by the laws of the State of Missouri for a deed of trust with power of sale, including Chapter 443 of the Revised Statutes of Missouri, as amended. Trustee shall sell the Property at public vendue to the highest bidder for cash in {{property_county}} County, Missouri. Trustee applies the proceeds to the costs of sale, then to the obligations secured, and pays any surplus to the persons entitled to it. Lender may appoint a successor trustee by a recorded instrument.

5. Release and governing law

When the obligations secured have been paid and performed, Lender shall request a release or deed of reconveyance. Notices mailed to the addresses above are effective. This Deed of Trust is governed by the laws of the State of Missouri. Invalid provisions are severed.

Borrower signed this Deed of Trust electronically on the loan application, under penalties of perjury, by typing the legal name shown below.

WORKING FORM FOR ATTORNEY REVIEW BEFORE RECORDING. This is not legal advice.
"""

_MO_NOTE = """SECURED PROMISSORY NOTE AND PERSONAL GUARANTY
State of Missouri.

Date: {{execution_date}}. Loan type: {{loan_type}}. Borrower: {{borrower_legal_name}}, a {{borrower_entity_type}}. Phone: {{borrower_phone}}. Collateral: {{property_address}}, {{property_county}} County, Missouri.

1. Promise to pay

For value received, Borrower promises to pay to the order of {{lender_legal_name}}, {{lender_address}}, the principal sum of ${{note_principal}} ({{note_principal_words}} United States Dollars). Price: {{interest_rate_percent}}. Term: {{term_label}}. Maturity date: {{maturity_date}}. The loan proceeds are for the Property named above. This Note may be prepaid in whole or in part at any time without premium, except fees already earned. If this Note is placed for collection, Borrower shall pay reasonable attorney's fees to the extent Missouri law allows.

2. Security

This Note is secured by a Deed of Trust, with power of sale, on {{property_address}}, in {{property_county}} County, Missouri. Borrower stays bound until this Note is paid and waives demand, presentment, and protest to the extent that waiver is permitted. This Note is governed by the laws of the State of Missouri.

3. Personal guaranty

To induce Lender to make the loan, {{guarantor_legal_name}}, whose address is {{guarantor_address}} ("Guarantor"), unconditionally guarantees full payment and performance of this Note and of the Deed of Trust. This Guaranty continues despite bankruptcy or abandonment of the Property. Lender will first pursue its remedies against the Property and against Borrower before demanding payment from Guarantor, except where delay would materially impair the collateral or Borrower is in bankruptcy. If more than one person is Guarantor, liability is joint and several. This Guaranty is governed by the laws of the State of Missouri.

{{#spouse}}
Guarantor's spouse, {{spouse_legal_name}}, joins this Guaranty and signs as an additional guarantor, jointly and severally. The spouse signs as a guarantor. Spouse taxpayer identification ends in {{spouse_ssn_last4}}.
{{/spouse}}

The persons named below signed this Note and Guaranty electronically on the loan application, under penalties of perjury, by typing their legal names.

WORKING FORM FOR ATTORNEY REVIEW BEFORE RECORDING. This is not legal advice.
"""

_KS_MORTGAGE = """MORTGAGE
State of Kansas. Brittco Capital Inc.

WORKING TEMPLATE FOR ATTORNEY REVIEW. NOT LEGAL ADVICE. Do not record or rely on this form without counsel sign-off.

1. Parties

This Mortgage is made as of {{execution_date}} by {{borrower_legal_name}}, a {{borrower_entity_type}} ("Mortgagor"), in favor of {{lender_legal_name}} ("Mortgagee"). Notice address for Mortgagor: {{borrower_mailing_address}}. Notice address for Mortgagee: {{lender_address}}. Phone: {{borrower_phone}}.

{{#spouse}}
Spouse {{spouse_legal_name}} joins this Mortgage for homestead and marital interest as required under Kansas law.
{{/spouse}}

2. Secured obligation

Mortgagor owes Mortgagee the principal sum of ${{note_principal}} ({{note_principal_words}} United States Dollars) under the Promissory Note dated {{execution_date}} (the "Note"), together with interest at {{interest_rate_percent}}, maturity {{maturity_date}}, and the guaranty obligations of {{guarantor_legal_name}}. Loan type: {{loan_type}}. Term: {{term_label}}.

3. Grant of mortgage

To secure the Note and this Mortgage, Mortgagor mortgages, grants, and conveys to Mortgagee the real property located in {{property_county}} County, Kansas, commonly known as {{property_address}}, and more particularly described as:

{{property_legal_description}}

The Property includes fixtures, appurtenances, and proceeds. This instrument is a mortgage for recording in {{property_county}} County, Kansas. It is not a deed of trust and it does not name a trustee.

4. Title covenants

Mortgagor covenants good title, authority to encumber, and quiet enjoyment, and will defend title against adverse claims, subject to permitted exceptions of record.

5. Payment and performance

Mortgagor shall pay the Note when due, keep property taxes and assessments current, maintain casualty insurance naming Mortgagee as mortgagee and loss payee for not less than ${{note_principal}}, and comply with laws affecting the Property.

6. Insurance, condemnation, and application of proceeds

Casualty proceeds and condemnation awards are assigned to Mortgagee and applied to restoration if that is economically feasible and this security is not impaired; otherwise to the debt, at Mortgagee's election.

7. Maintenance, waste, and inspection

Mortgagor shall maintain the Property, shall not commit waste, and shall permit Mortgagee to inspect on reasonable notice.

8. Due on sale and further encumbrance

A transfer of the Property, or a junior lien, without Mortgagee's prior written consent is a default and Mortgagee may accelerate the debt. Counsel should confirm the Kansas enforceability of this due-on-sale covenant before reliance.

9. Default and remedies

Events of default include nonpayment, breach of this Mortgage or the Note, insolvency, and material false statements. Remedies include acceleration, foreclosure of this Mortgage under Kansas law, appointment of a receiver, and other rights at law or in equity. Kansas foreclosure and redemption language must be confirmed by counsel before this Mortgage is recorded. This Mortgage does not grant a power of sale unless Kansas law and counsel expressly authorize it.

10. Assignment of rents

As additional security, Mortgagor assigns the rents, issues, and profits of the Property. After default, Mortgagee may collect them and apply them to the debt.

11. Hazardous materials

Mortgagor represents that Mortgagor will not release hazardous materials on the Property in violation of law and will indemnify Mortgagee from a breach of that representation, to the extent Kansas law allows.

12. Notices

Notices to Mortgagor are effective when mailed to {{borrower_mailing_address}}. Notices to Mortgagee are effective when mailed to {{lender_address}}.

13. Governing law

This Mortgage is governed by the laws of the State of Kansas. Venue lies in {{property_county}} County, Kansas, unless counsel specifies another Kansas venue.

14. Miscellaneous

Invalid provisions are severed. Covenants bind successors and assigns. This Mortgage, the Note, and the guaranty are the agreement for this loan. Counterparts are permitted.

15. Signatures

Mortgagor signed this Mortgage electronically on the loan application, under penalties of perjury, by typing the legal name shown below. A notary acknowledgment is completed when the instrument is prepared for recording in {{property_county}} County, Kansas.
"""

_KS_NOTE = """SECURED PROMISSORY NOTE AND PERSONAL GUARANTY
State of Kansas.

Date: {{execution_date}}. Loan type: {{loan_type}}. Borrower: {{borrower_legal_name}}, a {{borrower_entity_type}}. Collateral: {{property_address}}, {{property_county}} County, Kansas.

1. Promise to pay

For value received, Borrower promises to pay to the order of {{lender_legal_name}}, {{lender_address}}, the principal sum of ${{note_principal}} ({{note_principal_words}} United States Dollars). Interest: {{interest_rate_percent}}. Term: {{term_label}}. Maturity date: {{maturity_date}}. This Note may be prepaid in whole or in part without premium, except fees already earned. If this Note is placed for collection, Borrower shall pay reasonable attorney's fees to the extent Kansas law allows.

2. Security

This Note is secured by a Mortgage on {{property_address}}, in {{property_county}} County, Kansas, in favor of {{lender_legal_name}}. The security instrument is a mortgage. It is not a deed of trust. This Note is governed by the laws of the State of Kansas.

3. Personal guaranty

To induce Lender to make the loan, {{guarantor_legal_name}}, whose address is {{guarantor_address}} ("Guarantor"), unconditionally guarantees full payment and performance of this Note and of the Mortgage. This Guaranty continues despite bankruptcy or abandonment of the Property. Lender will first pursue the Property and Borrower before demanding payment from Guarantor, except where delay would materially impair the collateral or Borrower is in bankruptcy. Liability of more than one guarantor is joint and several. This Guaranty is governed by the laws of the State of Kansas.

{{#spouse}}
Guarantor's spouse, {{spouse_legal_name}}, joins this Guaranty and signs as an additional guarantor, jointly and severally.
{{/spouse}}

The persons named below signed this Note and Guaranty electronically on the loan application, under penalties of perjury, by typing their legal names.

WORKING TEMPLATE FOR ATTORNEY REVIEW. NOT LEGAL ADVICE.
"""

SEED_TEMPLATES = (
    ("MO", "deed_of_trust", "Missouri Deed of Trust", _MO_DEED),
    ("MO", "note_guaranty", "Missouri Secured Promissory Note and Personal Guaranty", _MO_NOTE),
    ("KS", "mortgage", "Kansas Mortgage", _KS_MORTGAGE),
    ("KS", "note_guaranty", "Kansas Secured Promissory Note and Personal Guaranty", _KS_NOTE),
)


def seed_state_templates(conn):
    now = _now()
    inserted = False
    for state_code, instrument_type, title, body in SEED_TEMPLATES:
        exists = conn.execute(
            "SELECT id FROM state_document_templates WHERE state_code=? AND instrument_type=?",
            (state_code, instrument_type),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO state_document_templates
               (state_code, instrument_type, title, body, version, active, created_at, updated_at)
               VALUES (?,?,?,?,1,1,?,?)""",
            (state_code, instrument_type, title, body.strip() + "\n", now, now),
        )
        inserted = True
    if inserted:
        conn.commit()


def active_template(conn, state_code, instrument_type):
    return conn.execute(
        """SELECT * FROM state_document_templates
           WHERE state_code=? AND instrument_type=? AND active=1
           ORDER BY version DESC LIMIT 1""",
        (state_code, instrument_type),
    ).fetchone()


def publish_template(conn, state_code, instrument_type, title, body):
    """Store a new active version and deactivate the prior active row."""
    state_code = normalize_state(state_code) or (state_code or "").strip().upper()
    current = conn.execute(
        """SELECT MAX(version) AS version FROM state_document_templates
           WHERE state_code=? AND instrument_type=?""",
        (state_code, instrument_type),
    ).fetchone()
    version = int((current["version"] if current and current["version"] else 0) or 0) + 1
    now = _now()
    conn.execute(
        """UPDATE state_document_templates SET active=0, updated_at=?
           WHERE state_code=? AND instrument_type=? AND active=1""",
        (now, state_code, instrument_type),
    )
    conn.execute(
        """INSERT INTO state_document_templates
           (state_code, instrument_type, title, body, version, active, created_at, updated_at)
           VALUES (?,?,?,?,?,1,?,?)""",
        (state_code, instrument_type, (title or "").strip(), body or "", version, now, now),
    )
    conn.commit()
    return version


def merge_map(data):
    data = derive(data or {})
    code = resolve_state_code(data)
    mailing = (data.get("mailing_address") or "").strip()
    if data.get("mailing_city"):
        mailing = ", ".join(
            part for part in (
                data.get("mailing_address"),
                data.get("mailing_city"),
                data.get("mailing_state"),
                data.get("mailing_zip"),
            ) if (part or "").strip()
        )
    if not mailing:
        mailing = data.get("borrower_notice_address") or ""
    return {
        "borrower_legal_name": data.get("borrower_legal_name") or "",
        "borrower_entity_type": data.get("borrower_entity_type") or "",
        "borrower_entity_name": data.get("borrower_legal_name") or "",
        "borrower_formation_state": data.get("borrower_formation_state") or "",
        "borrower_mailing_address": mailing,
        "borrower_notice_address": data.get("borrower_notice_address") or mailing,
        "borrower_phone": data.get("borrower_phone") or "",
        "borrower_email": data.get("borrower_email") or "",
        "borrower_ssn_last4": ssn_last4(data.get("borrower_ssn")),
        "borrower_ein": data.get("borrower_ein") or "",
        "borrower_dob": data.get("borrower_dob") or "",
        "signatory_name": data.get("signatory_name") or "",
        "spouse_legal_name": data.get("spouse_name") or "",
        "spouse_ssn_last4": ssn_last4(data.get("spouse_ssn")),
        "guarantor_legal_name": data.get("guarantor_name") or "",
        "guarantor_address": data.get("guarantor_address") or "",
        "lender_legal_name": data.get("lender_name") or "Brittco Capital Inc",
        "lender_address": data.get("lender_notice_address") or "",
        "property_address": data.get("property") or "",
        "property_county": data.get("county") or "",
        "property_state": state_name(code) or data.get("state") or "",
        "property_legal_description": data.get("legal_description") or "",
        "note_principal": data.get("note_principal") or "",
        "note_principal_words": data.get("note_principal_words") or "",
        "interest_rate_percent": rate_label(data),
        "maturity_date": long_date(data.get("maturity_date")),
        "execution_date": long_date(data.get("effective_date")),
        "origination_date": long_date(data.get("effective_date")),
        "outside_date": long_date(data.get("outside_date")),
        "first_payment_date": (
            long_date(data.get("first_payment_date"))
            if parse_iso(data.get("first_payment_date"))
            else ""
        ),
        "loan_type": data.get("loan_type") or "",
        "term_label": term_label(data),
        "recording_county": data.get("county") or "",
        "notary_state": state_name(code) or data.get("state") or "",
    }


def apply_merge(body, data):
    text = body or ""
    if spouse_required(data):
        text = text.replace("{{#spouse}}", "").replace("{{/spouse}}", "")
    else:
        text = re.sub(r"\{\{#spouse\}\}.*?\{\{/spouse\}\}", "", text, flags=re.S)
    tokens = merge_map(data)

    def repl(match):
        return tokens.get(match.group(1), "")

    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", repl, text)


def redact_tax_ids(data):
    out = dict(data or {})
    for key in ("borrower_ssn", "spouse_ssn"):
        raw = (out.get(key) or "").strip()
        last4 = ssn_last4(raw)
        if raw and "*" not in raw and last4:
            out[key] = "***-**-" + last4
    return out


def _footer_for(label):
    def paint(canvas, doc):
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch

        canvas.saveState()
        canvas.setStrokeColor(HexColor("#16324f"))
        canvas.setLineWidth(0.6)
        canvas.line(0.75 * inch, 0.52 * inch, letter[0] - 0.75 * inch, 0.52 * inch)
        canvas.setFont("Times-Roman", 8)
        canvas.setFillColor(HexColor("#5c6b7a"))
        canvas.drawString(0.75 * inch, 0.34 * inch, label)
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.34 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return paint


def _is_heading(text):
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 90:
        return False
    if re.match(r"^\d+\.\s+\S", stripped):
        return True
    letters = [ch for ch in stripped if ch.isalpha()]
    return bool(letters) and stripped.upper() == stripped and len(stripped) < 70


def render_text_pdf(title, subtitle, body_text, data, signatures, logo_path=None):
    from reportlab.platypus import Spacer

    data = derive(data or {})
    signatures = signatures or {}
    code = resolve_state_code(data)
    styles = _styles()
    buf, doc = _doc(title or "Loan document")
    story = []
    _header(story, styles, title or "LOAN DOCUMENT", subtitle or "", logo_path)
    for block in re.split(r"\n\s*\n", body_text or ""):
        paragraph = block.strip()
        if not paragraph:
            continue
        story.append(_p(paragraph, styles["h"] if _is_heading(paragraph) else styles["body"]))
    who = data.get("signatory_name") or ""
    _signature_block(
        story,
        styles,
        f"BORROWER: {data.get('borrower_legal_name') or ''}",
        who,
        data.get("signatory_title") or "Authorized signatory",
        signatures.get("borrower") or {},
    )
    if (title or "").lower().find("note") >= 0 or (title or "").lower().find("guaranty") >= 0:
        _signature_block(
            story,
            styles,
            "GUARANTOR",
            data.get("guarantor_name"),
            "Individually, as guarantor",
            signatures.get("borrower") or {},
        )
        if spouse_required(data):
            _signature_block(
                story,
                styles,
                "SPOUSE — ADDITIONAL GUARANTOR",
                data.get("spouse_name"),
                "Spouse, as guarantor only",
                signatures.get("spouse") or {},
            )
    _notary(story, styles, data.get("county"), who, state_name(code) or "Missouri")
    story.append(Spacer(1, 8))
    story.append(_p(ADMIN_DISCLAIMER, styles["small"]))
    label = "Brittco Capital Inc  ·  Working form  ·  Attorney review before recording"
    doc.build(story, onFirstPage=_footer_for(label), onLaterPages=_footer_for(label))
    return buf.getvalue()


def render_closing_pdfs(data, signatures, conn, logo_path=None):
    """Build the security instrument and note from the active state templates."""
    ensure_schema(conn)
    data = derive(data or {})
    code = resolve_state_code(data)
    if not code:
        state_label = (data.get("state") or "this state").strip()
        return {"error": f"No document templates are stored for {state_label}."}
    pair = instruments_for_state(code, conn)
    if not pair:
        return {"error": f"No document templates are stored for {state_name(code) or code}."}
    security_type, note_type = pair
    security = active_template(conn, code, security_type)
    note = active_template(conn, code, note_type)
    if not security or not note:
        return {"error": f"No document templates are stored for {state_name(code)}."}
    data["state_code"] = code
    data["state"] = state_name(code)
    data["security_instrument"] = security_type
    filled_security = apply_merge(security["body"], data)
    filled_note = apply_merge(note["body"], data)
    place = state_name(code)
    return {
        "security_pdf": render_text_pdf(security["title"], f"State of {place}", filled_security, data, signatures, logo_path),
        "note_pdf": render_text_pdf(note["title"], f"State of {place}", filled_note, data, signatures, logo_path),
        "security_type": security_type,
        "security_title": security["title"],
        "note_title": note["title"],
        "state_code": code,
        "data": data,
    }


def package_names(state_code, security_type):
    if security_type == "mortgage":
        security_name = "Kansas-Mortgage.pdf"
    else:
        security_name = "Deed-of-Trust.pdf"
    note_name = f"{state_name(state_code) or 'Loan'}-Note-and-Guaranty.pdf"
    return security_name, note_name
