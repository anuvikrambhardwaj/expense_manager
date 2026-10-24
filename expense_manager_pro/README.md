# ExpensePro — Expense Manager

A local expense-tracking web app built with the **Python standard library + SQLite**. It requires no third-party packages.

## Features
- Modern responsive dashboard
- Monthly and all-time spending totals
- Category analytics with visual bars
- Expense add / edit / delete
- Search and date/category/payment filters
- CSV export
- SQLite persistence
- Server-side input validation and friendly error responses
- CSRF protection for create, edit, and delete forms
- Threaded local web server
- Zero dependency installation

## Run
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Start the application with either:
   - `python app.py`
   - `python run.py`
4. Open `http://127.0.0.1:8000`

## Test
Run `python test_app.py` to test the dashboard, expense create/edit/delete, search, CSV export, CSRF checks, HTML escaping, and invalid input handling.

The database is automatically created at `data/expenses.db`.

## Scope and safety
This is a single-user local application; it does not provide accounts, authentication, or multi-user data separation. It binds to `127.0.0.1` and is intended for local use, not direct exposure to the internet.
