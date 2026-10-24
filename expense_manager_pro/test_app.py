import http.cookiejar
import os
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import app


class ExpenseAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = app.DB
        cls.temp_dir = tempfile.TemporaryDirectory()
        app.DB = os.path.join(cls.temp_dir.name, 'test_expenses.db')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = 'http://127.0.0.1:{}'.format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)
        app.DB = cls.original_db
        cls.temp_dir.cleanup()

    def setUp(self):
        cookies = http.cookiejar.CookieJar()
        self.client = build_opener(HTTPCookieProcessor(cookies))

    def request(self, path, method='GET', data=None):
        body = urlencode(data).encode('utf-8') if data is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={'Content-Type': 'application/x-www-form-urlencoded'} if body is not None else {},
        )
        try:
            response = self.client.open(request)
        except HTTPError as error:
            response = error
        result = response.code, response.read().decode('utf-8'), response.geturl()
        response.close()
        return result

    def csrf_token(self, html):
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(match, 'Form should contain a CSRF token')
        return match.group(1)

    def new_expense_form(self):
        status, body, _ = self.request('/expenses/new')
        self.assertEqual(status, 200)
        return self.csrf_token(body)

    def expense_data(self, token, **overrides):
        data = {
            'csrf_token': token,
            'title': 'Lunch',
            'amount': '250.00',
            'category': 'Food',
            'payment': 'UPI',
            'expense_date': '2026-10-19',
            'notes': 'Test expense',
        }
        data.update(overrides)
        return data

    def create_expense(self, **overrides):
        token = self.new_expense_form()
        status, body, url = self.request(
            '/expenses/new',
            method='POST',
            data=self.expense_data(token, **overrides),
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(url.endswith('/expenses'))
        with app.database() as con:
            return con.execute('SELECT id FROM expenses ORDER BY id DESC LIMIT 1').fetchone()['id']

    def test_dashboard_and_expense_list_load(self):
        status, body, _ = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('ExpensePro', body)
        status, body, _ = self.request('/expenses')
        self.assertEqual(status, 200)
        self.assertIn('Transactions', body)

    def test_create_filter_edit_and_delete_expense(self):
        expense_id = self.create_expense()
        status, body, _ = self.request('/expenses?q=Lunch&category=Food')
        self.assertEqual(status, 200)
        self.assertIn('Lunch', body)
        self.assertIn('250.00', body)

        status, body, _ = self.request('/expenses/{}/edit'.format(expense_id))
        self.assertEqual(status, 200)
        token = self.csrf_token(body)
        status, body, url = self.request(
            '/expenses/{}/edit'.format(expense_id),
            method='POST',
            data=self.expense_data(token, title='Dinner', amount='310.50'),
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(url.endswith('/expenses'))

        status, body, _ = self.request('/export')
        self.assertEqual(status, 200)
        self.assertIn('Dinner', body)
        self.assertIn('310.5', body)

        status, body, _ = self.request('/expenses')
        token = self.csrf_token(body)
        status, body, url = self.request(
            '/expenses/{}/delete'.format(expense_id),
            method='POST',
            data={'csrf_token': token},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(url.endswith('/expenses'))
        with app.database() as con:
            self.assertIsNone(con.execute('SELECT id FROM expenses WHERE id=?', (expense_id,)).fetchone())

    def test_rejects_missing_or_invalid_csrf_token(self):
        self.new_expense_form()
        status, body, _ = self.request(
            '/expenses/new',
            method='POST',
            data=self.expense_data('invalid-token'),
        )
        self.assertEqual(status, 403)
        self.assertIn('security token', body)

    def test_rejects_invalid_amount_and_preserves_form_values(self):
        token = self.new_expense_form()
        status, body, _ = self.request(
            '/expenses/new',
            method='POST',
            data=self.expense_data(token, amount='NaN', title='Invalid attempt'),
        )
        self.assertEqual(status, 400)
        self.assertIn('Enter an amount', body)
        self.assertIn('Invalid attempt', body)
        with app.database() as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM expenses WHERE title='Invalid attempt'").fetchone()[0],
                0,
            )

    def test_rejects_invalid_expense_date_and_amount_rounding_to_zero(self):
        cases = [
            ('0.001', '2026-10-19', 'amount'),
            ('10', '2026-02-30', 'valid expense date'),
        ]
        for amount, expense_date, expected_error in cases:
            token = self.new_expense_form()
            status, body, _ = self.request(
                '/expenses/new',
                method='POST',
                data=self.expense_data(token, amount=amount, expense_date=expense_date),
            )
            self.assertEqual(status, 400)
            self.assertIn(expected_error, body.lower())

    def test_rejects_invalid_filter_dates_and_reversed_range(self):
        for query in (
            '/expenses?start=not-a-date',
            '/expenses?start=2026-10-20&end=2026-10-19',
        ):
            status, body, _ = self.request(query)
            self.assertEqual(status, 400)
            self.assertIn('Invalid request', body)

    def test_escapes_user_content_in_html(self):
        self.create_expense(title='<script>alert(1)</script>')
        status, body, _ = self.request('/expenses')
        self.assertEqual(status, 200)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', body)
        self.assertNotIn('<script>alert(1)</script>', body)

    def test_invalid_record_ids_return_not_found(self):
        status, _, _ = self.request('/expenses/not-an-id/edit')
        self.assertEqual(status, 404)
        status, _, _ = self.request('/expenses/0/edit')
        self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
