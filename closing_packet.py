"""Missouri closing applications: typed e-sign, DOT, and note with guaranty.

Staff choose the loan type, rate, and term, then send a magic link. The borrower
finishes the missing facts and types a legal name. That typed name is the
signature. Generated PDFs are working forms for Brittco and should be reviewed
by counsel before recording.
"""

import calendar
import json
from datetime import date, datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

LOAN_TYPES = ("Fix and Flip", "Bridge", "Transactional Loan", "Gap Loan")

ADMIN_DISCLAIMER = (
    "Generated Missouri documents are working forms for Brittco Capital’s use. "
    "Have a licensed attorney review them before recording or reliance. "
    "This tool does not provide legal advice and is not a claim of bar admission."
)

PERJURY_NOTICE = (
    "Under penalties of perjury, I declare that the information in this application "
    "is true, correct, and complete to the best of my knowledge, and that typing my "
    "legal name is my electronic signature on the Missouri Deed of Trust and the "
    "Missouri Secured Promissory Note and Personal Guaranty prepared from this application."
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
    if (out.get("loan_type") == "Transactional Loan" and days) or (days and not months):
        maturity = add_business_days(start, days)
    elif months:
        maturity = add_months(start, months)
    else:
        maturity = parse_iso(out.get("maturity_date")) or start
    out["maturity_date"] = maturity.isoformat()
    ext_n = int(_num(out.get("extension_count")))
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
        "notice": PERJURY_NOTICE,
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


def _notary(story, styles, county, who):
    story.append(_p("NOTARY ACKNOWLEDGMENT  (to be completed at signing before a notary)", styles["h"]))
    story.append(_p(
        f"State of Missouri\nCounty of {county or '____________________'}\n\n"
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


def package_zip(deed_pdf, note_pdf):
    import zipfile

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Missouri-Deed-of-Trust.pdf", deed_pdf)
        zf.writestr("Missouri-Note-and-Guaranty.pdf", note_pdf)
    return buf.getvalue()
