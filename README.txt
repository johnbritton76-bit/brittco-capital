BRITTCO CAPITAL INC
Loan Origination · Underwriting · CRM · Borrower Portal
========================================================

This package is ready for Render hosting.

QUICK START ON RENDER
1. Read the file RENDER_SETUP.txt — it has the step-by-step instructions.
2. The important files for hosting are already included:
   - app.py
   - requirements.txt
   - Procfile
   - templates/ and static/ (includes your logo)

LOCAL TEST (optional, on a computer)
  python3 -m pip install -r requirements.txt
  python3 app.py
  Then open http://127.0.0.1:5050

DEMO LOGINS
  Staff:    admin@brittcocapital.com  /  brittco
  Borrower: marcus@example.com        /  borrower

Your logo is in static/logo.jpg and appears on every screen.

DWOLLA ACH (SANDBOX)
---------------------
Stripe stays in the app for old rows. New collections use Dwolla when
DWOLLA_KEY and DWOLLA_SECRET are set (override with PAYMENTS_PROVIDER).

Env vars (Render → Environment). Never commit the values. See .env.example.
  DWOLLA_ENV=sandbox
  DWOLLA_KEY
  DWOLLA_SECRET
  DWOLLA_WEBHOOK_SECRET
  PLATFORM_MODE=brittco_existing
  DWOLLA_MASTER_CUSTOMER_URL   (Brittco's existing Dwolla customer — skips onboarding)
  DWOLLA_PLATFORM_FUNDING_SOURCE_URL   (optional verified company bank)

Existing Brittco (PLATFORM_MODE=brittco_existing, the default) does not show
the company onboarding screen. New white-label companies set
PLATFORM_MODE=whitelabel. Staff then get a prompt to enter the legal business
details. Dwolla webhooks: https://app.brittcocapital.com/webhooks/dwolla
Topics: transfer_completed, transfer_failed, transfer_cancelled.

TEST INVESTORS
--------------
Staff → Tools → "Create two test investors".
Or on a machine with the app: python app.py seed-test-investors
The command prints two random passwords once. They are not stored in git.
Portal: /investor/login
Emails: sandbox.investor.a@example.com and sandbox.investor.b@example.com
