"""Default Brittco legal form templates. Staff can edit copies in the database."""

DEED_FIELDS = [
    ("effective_date", "Effective date"),
    ("secured_amount", "Amount secured (figures)"),
    ("secured_amount_words", "Amount secured (words)"),
    ("borrower_legal_name", "Borrower legal name"),
    ("borrower_entity_type", "Borrower entity type"),
    ("borrower_formation_state", "Borrower state of formation"),
    ("borrower_notice_address", "Borrower notice address"),
    ("lender_notice_address", "Lender notice address"),
    ("trustee_name", "Trustee name"),
    ("trustee_address", "Trustee address"),
    ("note_principal", "Note principal (figures)"),
    ("note_principal_words", "Note principal (words)"),
    ("county", "County where property sits"),
    ("state", "State where property sits"),
    ("legal_description", "Legal description"),
    ("property", "Common property address"),
    ("insurance_amount", "Required insurance amount"),
    ("signatory_name", "Borrower signatory name"),
    ("signatory_title", "Borrower signatory title"),
    ("notary_state", "Notary state"),
    ("notary_county", "Notary county"),
]

NOTE_FIELDS = [
    ("effective_date", "Effective date"),
    ("borrower_legal_name", "Borrower legal name"),
    ("borrower_entity_type", "Borrower entity type"),
    ("property", "Collateral / property address"),
    ("note_principal", "Loan / note amount (figures)"),
    ("note_principal_words", "Loan / note amount (words)"),
    ("lender_notice_address", "Lender notice address"),
    ("lender_phone", "Lender phone"),
    ("profit_fee", "Additional payment at sale/refi (figures)"),
    ("profit_fee_words", "Additional payment at sale/refi (words)"),
    ("maturity_date", "Maturity / first extension date"),
    ("extension_rate", "Extension interest rate %"),
    ("extension_payment", "One-month extension payment"),
    ("outside_date", "Outside date (quitclaim if unpaid)"),
    ("payment_day", "ACH / payment day of month"),
    ("late_charge_rate", "Late charge % per day"),
    ("late_charge_per_day", "Late charge dollars per day"),
    ("state", "Governing law / property state"),
    ("signatory_name", "Borrower signatory name"),
    ("signatory_title", "Borrower signatory title"),
    ("guarantor_name", "Guarantor name"),
    ("guarantor_address", "Guarantor address"),
    ("notary_state", "Notary state"),
    ("notary_county", "Notary county"),
]

DEED_BODY = """THIS DEED OF TRUST is made on or about {{effective_date}} (the “Deed of Trust”), by {{borrower_legal_name}}, a {{borrower_entity_type}} organized under the laws of {{borrower_formation_state}} (the “Borrower” and, for indexing, the “Grantor”), with a notice and mailing address of {{borrower_notice_address}} (the “Notice Address”), in favor of Brittco Capital Inc (the “Beneficiary,” “Lender,” and, for indexing, the “Grantee”), with a notice and mailing address of {{lender_notice_address}}. The Trustee under this Deed of Trust is {{trustee_name}}, with an address of {{trustee_address}} (the “Trustee”). The loan amount may include future advances toward rehabilitation and other costs described in the Note.

This Deed of Trust secures a Secured Promissory Note signed concurrently herewith, together with any Personal Guaranty, providing for the principal sum of {{note_principal}} ({{note_principal_words}} and 00/100 United States Dollars), all as more particularly described in that Note.

TO SECURE PAYMENT OF {{secured_amount}} ({{secured_amount_words}} and 00/100 United States Dollars).

BORROWER irrevocably grants, bargains, sells, and conveys to Trustee, in trust, with Power of Sale, the following described property in the County of {{county}}, State of {{state}}:

LEGAL DESCRIPTION: {{legal_description}}

Commonly referred to as: {{property}} (the “Property”).

Subject to building lines, easements, reservations, restrictions, covenants, and conditions of record, if any, and to zoning laws affecting the Property.

TOGETHER with all improvements now or hereafter erected on the Property, and all easements, rights, appurtenances, rents, royalties, mineral rights, water rights, and fixtures; all of the foregoing are the “Property.”

Borrower’s Obligations include: paying all taxes and assessments; keeping the Property free of nuisance and maintenance violations; and repaying sums Lender advances to protect this security.

Borrower covenants that Borrower is lawfully seized of the estate conveyed, has the right to grant and convey the Property, and will warrant and defend title subject to exceptions in any contemporaneous title policy.

UNIFORM COVENANTS. 1. Borrower shall perform Borrower’s Obligations and pay principal and interest on sums Lender advances to protect this security. 2. Borrower shall obtain property insurance naming Lender as mortgagee and loss payee in an amount not less than {{insurance_amount}}, and shall pay taxes and insurance after closing. 3. Payments received by Lender may be applied first to tax liens, then to Borrower’s Obligations, then to interest and principal on protective advances. 4. Borrower shall pay charges that may attain priority over this Deed of Trust, except liens being paid under an agreement acceptable to Lender or contested in good faith. 5. Borrower shall keep improvements insured against fire and extended coverage with a carrier authorized in {{state}}, with a standard mortgage clause in favor of Lender. Proceeds shall be applied to restoration if economically feasible and if this security is not impaired; otherwise to the sums secured hereby. 6. Borrower shall keep the Property in good repair and not commit waste. 7. If Borrower defaults or a proceeding materially affects Lender’s interest, Lender may appear, disburse sums, enter to repair, and add those amounts, with interest, to the indebtedness. 8. Lender may inspect the Property after notice stating reasonable cause. 9. Condemnation proceeds are assigned to Lender, subject to any senior lien. 10. Extensions granted to a successor do not release the original Borrower. 11. Forbearance is not a waiver. 12. Remedies are cumulative. 13. Covenants bind successors and are joint and several. 14. Notices to Borrower are by certified mail to the Notice Address; notices to Lender are by certified mail, return receipt requested. 15. This Deed of Trust is governed by the law of the jurisdiction where the Property is located. Invalid provisions are severable. 16. Borrower shall receive a conformed copy. 17. Transfer without Lender’s prior written consent may result in enforcement after not less than 30 days’ notice.

NON-UNIFORM COVENANTS. 18. Upon breach, Lender shall mail notice specifying the breach, the cure, a date not less than 30 days after mailing, and that failure to cure may result in acceleration and sale. If uncured, Lender may accelerate, invoke the power of sale, appoint a successor trustee, and collect reasonable costs including attorney’s fees. Trustee shall advertise the sale in a newspaper published in the County of {{county}}, {{state}}, as required by applicable law, and sell at the usual place of foreclosure sale in that County to the highest bidder for cash. Proceeds: costs of sale; sums secured; surplus to those entitled. 19. Borrower assigns rents to Lender. After acceleration or abandonment, Lender or a receiver may collect rents and apply them first to management costs then to the debt. 20. Upon payment in full Lender shall release this Deed of Trust; the record owner pays recording costs. 21. Lender may substitute the Trustee by recorded instrument. 22. Trustee leases the Property to Borrower until satisfaction or default; upon default Borrower shall surrender possession. 23. These covenants run with the land. 24. No subordinate lien without Lender’s written consent. 25. Borrower waives trial by jury.

WE HAVE READ THIS DEED OF TRUST AND ARE IN COMPLETE AGREEMENT WITH THE TERMS CONTAINED HEREIN. THIS IS AN IMPORTANT LEGAL DOCUMENT. IF YOU HAVE QUESTIONS, CONSULT AN ATTORNEY."""

NOTE_BODY = """SECURED PROMISSORY NOTE

Dated: {{effective_date}}
Borrower: {{borrower_legal_name}}, a {{borrower_entity_type}}
Collateral: {{property}}
Loan / Note amount: {{note_principal}}

For good value, the undersigned jointly and severally promises to pay to the order of Brittco Capital Inc, {{lender_notice_address}}, {{lender_phone}}, as Note Holder and Lender.

THE LOAN AMOUNT OF {{note_principal}} ({{note_principal_words}} and 00/100 United States Dollars) shall be paid as follows.

Borrower will receive {{note_principal}} for the purchase and/or rehabilitation of the Property and other fees as reflected in the final settlement statement, to be used for purchase, rehabilitation, holding, taxes, and fees associated with acquiring and selling {{property}}.

Lender shall at all times be entitled to repayment of its basis loaned in the acquisition and other fees as reflected in the final settlement statement, including advances incorporated into the amount borrowed.

IN ADDITION, upon sale or refinance of the Property, Borrower shall pay Lender {{profit_fee}} ({{profit_fee_words}} and 00/100 United States Dollars) from settlement proceeds.

If the Property is not sold and closed by {{maturity_date}}, Borrower will pay an interest-only payment of {{extension_rate}}% of outstanding principal, due on {{maturity_date}}, in the amount of {{extension_payment}}, for a one-month extension. If the loan is not repaid by {{outside_date}}, Borrower will execute a quitclaim deed to the Property upon Lender’s request.

Payments, when required, shall be set up on automatic ACH and due on the {{payment_day}} of the month. Borrower shall put ACH authorization in place no later than three (3) days before that date if closing has not occurred. A payment is late if not made on the specified date. A late charge of {{late_charge_rate}}% per day on the missed interest payment will then apply, equal to {{late_charge_per_day}} per day, in addition to the original interest payment.

This Note may be prepaid at any time, in whole or in part, without penalty.

THIS NOTE SHALL BE DUE AND PAYABLE, AT THE OPTION OF ANY HOLDER, UPON: (1) failure to make any payment when due; (2) breach of any security instrument or guaranty given for this Note; (3) breach of any senior loan document; (4) death, incapacity, dissolution, or liquidation of any obligor, endorser, or guarantor; (5) assignment for creditors, bankruptcy, or a receivership not vacated within thirty (30) days; (6) collection, in which event the undersigned shall pay reasonable attorney’s fees and costs; (7) failure to pay real estate taxes when due or to insure the Property as required.

THIS NOTE IS SECURED BY A DEED OF TRUST on {{property}}.

The undersigned and all other parties remain bound until this Note is paid, and waive demand, presentment, protest, and related notices, and remain bound notwithstanding extension, modification, waiver, release of an obligor, or substitution of collateral. Modifications must be in writing. This Note is a sealed instrument governed by the laws of {{state}}. BORROWER WAIVES THE RIGHT TO A JURY TRIAL AND AGREES TO PAY REASONABLE COLLECTION COSTS.

PERSONAL GUARANTY

This Guaranty is dated {{effective_date}}.

As an inducement to Lender to make the loan, {{guarantor_name}}, whose address is {{guarantor_address}} (the “Guarantor”), unconditionally guarantees full performance by Borrower of the Secured Promissory Note and the Deed of Trust.

Guarantor represents that Guarantor has full power to enter into this Guaranty and that it is a valid and binding obligation. This Guaranty continues despite bankruptcy, reorganization, insolvency, disaffirmance, or abandonment. Lender shall first attempt to enforce Borrower’s obligations before enforcing this Guaranty against Guarantor. Guarantor shall pay Lender’s reasonable attorney’s fees and collection costs.

If more than one Guarantor signs, liability is joint and several. Release of one Guarantor does not release the others. This Guaranty binds Guarantor’s heirs, successors, and assigns and inures to Lender and its successors and assigns."""

DEFAULTS = {
    "deed_of_trust": {
        "title": "Deed of Trust",
        "blurb": "Security instrument with power of sale. Complete the fields, download or print, and wet-sign in front of a notary.",
        "fields": DEED_FIELDS,
        "body": DEED_BODY,
    },
    "promissory_note_guaranty": {
        "title": "Secured Promissory Note and Personal Guaranty",
        "blurb": "Note plus personal guaranty. Complete the fields, download or print, and wet-sign in front of a notary.",
        "fields": NOTE_FIELDS,
        "body": NOTE_BODY,
    },
}
