#!/usr/bin/env python3
"""ExpensePro: zero-dependency professional expense manager.
Run: python app.py or python run.py
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import date, datetime, timezone
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import csv, hashlib, hmac, html, io, logging, os, secrets, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'data', 'expenses.db')
PORT = int(os.environ.get('PORT', '8000'))
MAX_REQUEST_SIZE = 64 * 1024
MAX_AMOUNT = Decimal('999999999.99')
CATEGORIES = ['Food','Transport','Shopping','Bills','Health','Entertainment','Education','Travel','Other']
PAYMENTS = ['Cash','UPI','Credit Card','Debit Card','Bank Transfer','Other']
CSRF_SECRET = secrets.token_bytes(32)
LOGGER = logging.getLogger(__name__)

CSS = '''*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:#f4f7fb;color:#172033}a{text-decoration:none;color:inherit}.nav{background:#111827;color:white;padding:16px 5%;display:flex;justify-content:space-between;align-items:center}.brand{font-weight:800;font-size:21px}.navlinks{display:flex;gap:10px;align-items:center}.wrap{max-width:1180px;margin:32px auto;padding:0 18px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:24px}.muted{color:#6b7280}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.card{background:white;border-radius:18px;padding:20px;box-shadow:0 8px 28px #1720330d;border:1px solid #e8edf4}.stat{font-size:28px;font-weight:800;margin-top:8px}.tablewrap{overflow:auto}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:14px 10px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}.table th{font-size:13px;color:#6b7280}.btn{display:inline-block;border:0;border-radius:10px;padding:10px 14px;background:#2563eb;color:white;cursor:pointer;font-weight:650}.btn.secondary{background:#eef2f7;color:#172033}.btn.danger{background:#fee2e2;color:#b91c1c}.badge{background:#eef2ff;color:#4338ca;border-radius:999px;padding:5px 9px;font-size:12px}.actions{display:flex;gap:7px}.filters{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr auto;gap:10px}.input,.select,.textarea{width:100%;padding:11px 12px;border:1px solid #dce2ea;border-radius:10px;background:white;font:inherit}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.full{grid-column:1/-1}.label{display:block;font-size:13px;font-weight:700;margin-bottom:7px}.flash{padding:12px 14px;border-radius:10px;background:#dcfce7;color:#166534;margin-bottom:15px}.empty{text-align:center;padding:50px;color:#6b7280}.bar{height:10px;background:#edf1f7;border-radius:20px;overflow:hidden}.bar>span{display:block;height:100%;background:#2563eb}.footer{text-align:center;color:#8a94a6;padding:30px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.filters{grid-template-columns:1fr 1fr}.top{align-items:flex-start}.formgrid{grid-template-columns:1fr}}@media(max-width:520px){.grid{grid-template-columns:1fr}.navlinks .hide-sm{display:none}.filters{grid-template-columns:1fr}.top{flex-direction:column}}
'''

def esc(v): return html.escape(str('' if v is None else v))

def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con=sqlite3.connect(DB)
    con.row_factory=sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,amount REAL NOT NULL CHECK(amount>0),category TEXT NOT NULL,payment TEXT NOT NULL,expense_date TEXT NOT NULL,notes TEXT DEFAULT '',created_at TEXT NOT NULL)''')
    return con

@contextmanager
def database():
    con = db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def _csrf_signature(value):
    return hmac.new(CSRF_SECRET, value.encode('utf-8'), hashlib.sha256).hexdigest()

def _valid_date(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False
    return parsed.isoformat() == value

def layout(title, body, flash=''):
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · ExpensePro</title><style>{CSS}</style></head><body><nav class="nav"><a class="brand" href="/">ExpensePro</a><div class="navlinks"><a class="btn secondary hide-sm" href="/expenses">Expenses</a><a class="btn" href="/expenses/new">+ Add Expense</a></div></nav><main class="wrap">{('<div class="flash">'+esc(flash)+'</div>') if flash else ''}{body}</main><footer class="footer">ExpensePro · Simple, fast, private expense tracking</footer></body></html>'''

def money(x): return f'₹{x:,.2f}'

def dashboard():
    today=date.today().isoformat(); month=today[:7]
    with database() as con:
        month_total=con.execute("SELECT COALESCE(SUM(amount),0) x FROM expenses WHERE substr(expense_date,1,7)=?",(month,)).fetchone()['x']
        total=con.execute('SELECT COALESCE(SUM(amount),0) x FROM expenses').fetchone()['x']
        count=con.execute('SELECT COUNT(*) x FROM expenses').fetchone()['x']
        cats=con.execute('SELECT category,SUM(amount) total FROM expenses GROUP BY category ORDER BY total DESC').fetchall()
        recent=con.execute('SELECT * FROM expenses ORDER BY expense_date DESC,id DESC LIMIT 8').fetchall()
    maxcat=max([r['total'] for r in cats] or [1])
    rows=''.join(f'<tr><td>{esc(r["expense_date"])}</td><td><b>{esc(r["title"])}</b></td><td><span class="badge">{esc(r["category"])}</span></td><td>{esc(r["payment"])}</td><td><b>{money(r["amount"])}</b></td></tr>' for r in recent) or '<tr><td colspan="5" class="empty">No expenses yet. Add your first expense.</td></tr>'
    cathtml=''.join(f'<div style="margin:14px 0"><div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>{esc(r["category"])}</span><b>{money(r["total"])}</b></div><div class="bar"><span style="width:{r["total"]/maxcat*100:.1f}%"></span></div></div>' for r in cats) or '<p class="muted">No category data yet.</p>'
    body=f'''<div class="top"><div><h1 style="margin:0">Financial Dashboard</h1><p class="muted">Your spending, organized at a glance.</p></div><a class="btn secondary" href="/export">Export CSV</a></div><section class="grid"><div class="card"><div class="muted">This month</div><div class="stat">{money(month_total)}</div></div><div class="card"><div class="muted">All time</div><div class="stat">{money(total)}</div></div><div class="card"><div class="muted">Transactions</div><div class="stat">{count}</div></div><div class="card"><div class="muted">Top category</div><div class="stat" style="font-size:21px">{esc(cats[0]["category"]) if cats else '—'}</div></div></section><div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-top:16px"><section class="card"><h2 style="margin-top:0">Recent expenses</h2><div class="tablewrap"><table class="table"><tr><th>Date</th><th>Title</th><th>Category</th><th>Payment</th><th>Amount</th></tr>{rows}</table></div></section><section class="card"><h2 style="margin-top:0">By category</h2>{cathtml}</section></div>'''
    return layout('Dashboard',body)

def expense_list(params, csrf_token):
    where=[]; args=[]
    q=params.get('q',[''])[0].strip(); cat=params.get('category',[''])[0]; pay=params.get('payment',[''])[0]; start=params.get('start',[''])[0]; end=params.get('end',[''])[0]
    if len(q) > 120:
        raise ValueError('Search text must be 120 characters or fewer.')
    if start and not _valid_date(start):
        raise ValueError('Enter a valid start date.')
    if end and not _valid_date(end):
        raise ValueError('Enter a valid end date.')
    if start and end and start > end:
        raise ValueError('The start date must not be after the end date.')
    if q: where.append('title LIKE ?'); args.append('%'+q+'%')
    if cat in CATEGORIES: where.append('category=?'); args.append(cat)
    if pay in PAYMENTS: where.append('payment=?'); args.append(pay)
    if start: where.append('expense_date>=?'); args.append(start)
    if end: where.append('expense_date<=?'); args.append(end)
    sql='SELECT * FROM expenses'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY expense_date DESC,id DESC'
    with database() as con:
        rows=con.execute(sql,args).fetchall()
    total=sum(r['amount'] for r in rows)
    options=lambda vals,sel:'<option value="">All</option>'+''.join(f'<option value="{esc(v)}" {"selected" if v==sel else ""}>{esc(v)}</option>' for v in vals)
    trs=''.join(f'''<tr><td>{esc(r['expense_date'])}</td><td><b>{esc(r['title'])}</b><small class="muted" style="display:block">{esc(r['notes'])}</small></td><td><span class="badge">{esc(r['category'])}</span></td><td>{esc(r['payment'])}</td><td><b>{money(r['amount'])}</b></td><td><div class="actions"><a class="btn secondary" href="/expenses/{r['id']}/edit">Edit</a><form method="post" action="/expenses/{r['id']}/delete" onsubmit="return confirm('Delete this expense?')"><input type="hidden" name="csrf_token" value="{esc(csrf_token)}"><button class="btn danger">Delete</button></form></div></td></tr>''' for r in rows) or '<tr><td colspan="6" class="empty">No expenses match your filters.</td></tr>'
    body=f'''<div class="top"><div><h1 style="margin:0">Expenses</h1><p class="muted">Search, filter and manage every transaction.</p></div><a class="btn" href="/expenses/new">+ Add Expense</a></div><form class="card filters" method="get"><input class="input" name="q" placeholder="Search title..." value="{esc(q)}"><select class="select" name="category">{options(CATEGORIES,cat)}</select><select class="select" name="payment">{options(PAYMENTS,pay)}</select><input class="input" type="date" name="start" value="{esc(start)}"><input class="input" type="date" name="end" value="{esc(end)}"><button class="btn">Filter</button></form><section class="card" style="margin-top:16px"><div class="top" style="margin-bottom:8px"><h2 style="margin:0">Transactions</h2><b>Total: {money(total)}</b></div><div class="tablewrap"><table class="table"><tr><th>Date</th><th>Title</th><th>Category</th><th>Payment</th><th>Amount</th><th></th></tr>{trs}</table></div></section>'''
    return layout('Expenses',body)

def form_page(row=None, error='', csrf_token='', editing=False):
    title='Edit Expense' if editing else 'Add Expense'; r=row or {'title':'','amount':'','category':'Food','payment':'UPI','expense_date':date.today().isoformat(),'notes':''}
    def opts(vals,sel): return ''.join(f'<option value="{esc(v)}" {"selected" if v==sel else ""}>{esc(v)}</option>' for v in vals)
    body=f'''<div class="top"><div><h1 style="margin:0">{title}</h1><p class="muted">Enter accurate details to keep your reports useful.</p></div></div>{('<div class="flash" style="background:#fee2e2;color:#991b1b">'+esc(error)+'</div>') if error else ''}<section class="card"><form method="post"><input type="hidden" name="csrf_token" value="{esc(csrf_token)}"><div class="formgrid"><div><label class="label">Title</label><input class="input" name="title" maxlength="120" required value="{esc(r['title'])}" placeholder="e.g. Grocery shopping"></div><div><label class="label">Amount (₹)</label><input class="input" name="amount" type="number" min="0.01" step="0.01" required value="{esc(r['amount'])}"></div><div><label class="label">Category</label><select class="select" name="category">{opts(CATEGORIES,r['category'])}</select></div><div><label class="label">Payment method</label><select class="select" name="payment">{opts(PAYMENTS,r['payment'])}</select></div><div><label class="label">Date</label><input class="input" name="expense_date" type="date" required value="{esc(r['expense_date'])}"></div><div><label class="label">Notes</label><input class="input" name="notes" maxlength="1000" value="{esc(r['notes'])}" placeholder="Optional"></div></div><div style="margin-top:20px;display:flex;gap:10px"><button class="btn">Save Expense</button><a class="btn secondary" href="/expenses">Cancel</a></div></form></section>'''
    return layout(title,body)

def validate_expense(data):
    title=data.get('title',[''])[0].strip()
    amount_text=data.get('amount',[''])[0].strip()
    category=data.get('category',[''])[0]
    payment=data.get('payment',[''])[0]
    expense_date=data.get('expense_date',[''])[0]
    notes=data.get('notes',[''])[0].strip()
    values={'title':title,'amount':amount_text,'category':category,'payment':payment,'expense_date':expense_date,'notes':notes}

    if not title or len(title)>120:
        return values, None, 'Title is required and must be 120 characters or fewer.'
    if len(notes)>1000:
        return values, None, 'Notes must be 1,000 characters or fewer.'
    try:
        amount=Decimal(amount_text)
        if not amount.is_finite() or amount<=0 or amount>MAX_AMOUNT:
            raise InvalidOperation
        amount=amount.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        if amount<=0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return values, None, 'Enter an amount greater than ₹0 and no more than ₹999,999,999.99.'
    if category not in CATEGORIES:
        return values, None, 'Choose a valid expense category.'
    if payment not in PAYMENTS:
        return values, None, 'Choose a valid payment method.'
    if not _valid_date(expense_date):
        return values, None, 'Enter a valid expense date.'
    values['amount']=str(amount)
    return values, amount, ''

class Handler(BaseHTTPRequestHandler):
    def send(self,status=200,content='',ctype='text/html; charset=utf-8',headers=None):
        b=content.encode(); self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b)))
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def redirect(self,path): self.send(303,'',headers={'Location':path})
    def data(self):
        if self.headers.get_content_type()!='application/x-www-form-urlencoded':
            raise ValueError('Expected form-encoded data.')
        try:
            size=int(self.headers.get('Content-Length','-1'))
        except ValueError:
            raise ValueError('Invalid request length.')
        if size<0 or size>MAX_REQUEST_SIZE:
            raise ValueError('Request body is too large or has no valid length.')
        try:
            body=self.rfile.read(size).decode('utf-8')
            return parse_qs(body,keep_blank_values=True,max_num_fields=50,strict_parsing=False)
        except (UnicodeDecodeError,ValueError):
            raise ValueError('Invalid form data.')
    def _cookie_session(self):
        for item in self.headers.get('Cookie','').split(';'):
            name,separator,value=item.strip().partition('=')
            if separator and name=='expensepro_session':
                nonce,dot,signature=value.partition('.')
                if dot and nonce and hmac.compare_digest(signature,_csrf_signature('session:'+nonce)):
                    return nonce
        return None
    def csrf_token(self):
        nonce=self._cookie_session()
        if nonce is None:
            nonce=secrets.token_urlsafe(32)
            cookie_value=nonce+'.'+_csrf_signature('session:'+nonce)
            self._csrf_cookie='expensepro_session='+cookie_value+'; Path=/; HttpOnly; SameSite=Strict'
        else:
            self._csrf_cookie=None
        return _csrf_signature('csrf:'+nonce)
    def csrf_is_valid(self, token):
        nonce=self._cookie_session()
        return bool(nonce and token and hmac.compare_digest(token,_csrf_signature('csrf:'+nonce)))
    def page_headers(self):
        return {'Set-Cookie':self._csrf_cookie} if getattr(self,'_csrf_cookie',None) else None
    def do_GET(self):
        u=urlparse(self.path); p=u.path; qs=parse_qs(u.query)
        try:
            if p=='/': return self.send(content=dashboard())
            if p=='/expenses':
                token=self.csrf_token()
                return self.send(content=expense_list(qs,token),headers=self.page_headers())
            if p=='/expenses/new':
                token=self.csrf_token()
                return self.send(content=form_page(csrf_token=token),headers=self.page_headers())
            if p=='/export':
                with database() as con:
                    rows=con.execute('SELECT expense_date,title,category,payment,amount,notes FROM expenses ORDER BY expense_date DESC').fetchall()
                s=io.StringIO(); w=csv.writer(s); w.writerow(['Date','Title','Category','Payment','Amount','Notes']); w.writerows(rows)
                return self.send(200,s.getvalue(),'text/csv',{'Content-Disposition':'attachment; filename="expense-report.csv"'})
            parts=p.strip('/').split('/')
            if len(parts)==3 and parts[0]=='expenses' and parts[2]=='edit':
                rid=self._expense_id(parts[1])
                if rid is None: return self.send(404,'Page not found','text/plain')
                token=self.csrf_token()
                with database() as con:
                    row=con.execute('SELECT * FROM expenses WHERE id=?',(rid,)).fetchone()
                return self.send(content=form_page(row,csrf_token=token,editing=True),headers=self.page_headers()) if row else self.send(404,'Expense not found','text/plain')
            return self.send(404,'Page not found','text/plain')
        except ValueError as error:
            return self.send(400,layout('Invalid request','<section class="card"><h1>Invalid request</h1><p>Please correct the request and try again.</p></section>',str(error)))
        except Exception:
            LOGGER.exception('Unexpected error while handling GET %s',p)
            return self.send(500,'Internal server error','text/plain')
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            d=self.data()
            if not self.csrf_is_valid(d.get('csrf_token',[''])[0]):
                return self.send(403,'Invalid or missing form security token. Reload the page and try again.','text/plain')
            parts=p.strip('/').split('/')
            if p=='/expenses/new' or (len(parts)==3 and parts[0]=='expenses' and parts[2]=='edit'):
                values,amount,error=validate_expense(d)
                if error:
                    token=self.csrf_token()
                    editing=p!='/expenses/new'
                    return self.send(400,form_page(values,error,token,editing),headers=self.page_headers())
                now=datetime.now(timezone.utc).isoformat()
                if p=='/expenses/new':
                    with database() as con:
                        con.execute('INSERT INTO expenses(title,amount,category,payment,expense_date,notes,created_at) VALUES(?,?,?,?,?,?,?)',(values['title'],float(amount),values['category'],values['payment'],values['expense_date'],values['notes'],now))
                else:
                    rid=self._expense_id(parts[1])
                    if rid is None: return self.send(404,'Expense not found','text/plain')
                    with database() as con:
                        result=con.execute('UPDATE expenses SET title=?,amount=?,category=?,payment=?,expense_date=?,notes=? WHERE id=?',(values['title'],float(amount),values['category'],values['payment'],values['expense_date'],values['notes'],rid))
                        if result.rowcount==0: return self.send(404,'Expense not found','text/plain')
                return self.redirect('/expenses')
            if len(parts)==3 and parts[0]=='expenses' and parts[2]=='delete':
                rid=self._expense_id(parts[1])
                if rid is None: return self.send(404,'Expense not found','text/plain')
                with database() as con:
                    result=con.execute('DELETE FROM expenses WHERE id=?',(rid,))
                    if result.rowcount==0: return self.send(404,'Expense not found','text/plain')
                return self.redirect('/expenses')
            return self.send(404,'Not found','text/plain')
        except ValueError as error:
            return self.send(400,esc(error),'text/plain')
        except Exception:
            LOGGER.exception('Unexpected error while handling POST %s',p)
            return self.send(500,'Internal server error','text/plain')
    @staticmethod
    def _expense_id(value):
        try:
            rid=int(value)
        except ValueError:
            return None
        return rid if rid>0 else None
    def log_message(self,fmt,*args): pass

def main():
    with database():
        pass
    print(f'ExpensePro running at http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
if __name__=='__main__': main()
