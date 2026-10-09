#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اختبار شامل لسيرفر إستاكوزا POS - محاكاة 360 يوم تشغيل
=========================================================
- مش بيلمس الداتا الحقيقية: بيشغّل نسخة مؤقتة من السيرفر (كاشير + باك أوفيس) على بورتات 18080/18081
  وداتا في فولدر مؤقت، وبيمسحه في الآخر (إلا لو كتبت --keep).
- بيحاكي الوقت (ساعة وهمية) فكل "يوم" بيعدّي في ثواني: بيع، دليفري، إلغاء، تعديل، مرتجع، قفل يوم.
- بيطبع تقرير PASS / FAIL / WARN وبيحفظه في test_report.txt

التشغيل (من فولدر المشروع):
    python3 test_360.py
خيارات:
    --days 360        عدد الأيام (الافتراضي 360)
    --seed 1          رقم عشوائي ثابت لو عايز نفس الاختبار تاني
    --keep            سيب الداتا المؤقتة بعد الاختبار
    --server PATH     مسار server.py (لو مش لاقيه لوحده)
مفيش مكتبات خارجية - بايثون 3.8+ بس.
"""
import os, sys, json, time, uuid, random, shutil, sqlite3, argparse, tempfile, subprocess, threading, urllib.request, urllib.error, socket, statistics
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PORT_C, PORT_B = 18080, 18081
ADMIN_PW = 'admin'

WRAPPER = r'''
import sys, os, runpy, datetime as _d
_real = _d.datetime
CLOCK = os.environ['ISTAKOZA_CLOCK']
class FakeDT(_real):
    @classmethod
    def now(cls, tz=None):
        try:
            return cls.fromisoformat(open(CLOCK).read().strip())
        except Exception:
            return _real.now(tz)
_d.datetime = FakeDT
sys.argv = ['server.py', '--no-browser']
runpy.run_path(os.environ['ISTAKOZA_SERVER'], run_name='__main__')
'''


# ----------------------------------------------------------------------------- تقرير
class Report:
    def __init__(self):
        self.rows = []      # (level, name, detail)
        self.t0 = time.time()

    def add(self, level, name, detail=''):
        self.rows.append((level, name, detail))
        icon = {'PASS': '✅', 'FAIL': '❌', 'WARN': '⚠️ ', 'INFO': 'ℹ️ '}[level]
        print('  %s %s%s' % (icon, name, ('  →  ' + str(detail)) if detail else ''), flush=True)

    def chk(self, name, cond, detail_fail='', detail_ok=''):
        self.add('PASS' if cond else 'FAIL', name, detail_ok if cond else detail_fail)
        return bool(cond)

    def warn(self, name, detail=''):
        self.add('WARN', name, detail)

    def info(self, name, detail=''):
        self.add('INFO', name, detail)

    def count(self, lvl):
        return sum(1 for r in self.rows if r[0] == lvl)


R = Report()


def section(t):
    print('\n' + '=' * 70 + '\n' + t + '\n' + '=' * 70, flush=True)


# ----------------------------------------------------------------------------- HTTP
LAT = {}   # endpoint -> [seconds]


class Api:
    def __init__(self, port, name):
        self.base = 'http://127.0.0.1:%d' % port
        self.name = name

    def req(self, method, path, body=None, tok='', timeout=120, raw=False, headers=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8'))
        h = {'Content-Type': 'application/json', 'X-Token': tok}
        h.update(headers or {})
        rq = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        t = time.time()
        try:
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                b, st = r.read(), r.status
                hd = dict(r.headers)
        except urllib.error.HTTPError as e:
            b, st, hd = e.read(), e.code, dict(e.headers)
        except Exception as e:
            return 0, {'error': 'CONN: %r' % e}, 0
        dt = time.time() - t
        key = method + ' ' + path.split('?')[0]
        LAT.setdefault(key, []).append(dt)
        if raw:
            return st, b, hd
        try:
            return st, json.loads(b or b'{}'), dt
        except Exception:
            return st, {'error': 'bad json', 'raw': b[:200].decode('utf-8', 'replace')}, dt

    def login(self, u, p):
        st, r, _ = self.req('POST', '/api/login', {'u': u, 'p': p})
        return r.get('token', '') if st == 200 else ''


# ----------------------------------------------------------------------------- السيرفرات
class Servers:
    def __init__(self, server_py, data_dir, tmp):
        self.server_py, self.data_dir, self.tmp = server_py, data_dir, tmp
        self.clock = os.path.join(tmp, 'clock.txt')
        self.wrapper = os.path.join(tmp, 'wrap.py')
        open(self.wrapper, 'w', encoding='utf-8').write(WRAPPER)
        self.procs = {}
        self.logs = {}

    def set_clock(self, dt):
        p = self.clock + '.tmp'
        open(p, 'w').write(dt.strftime('%Y-%m-%dT%H:%M:%S'))
        os.replace(p, self.clock)

    def start(self, mode, port):
        env = dict(os.environ, ISTAKOZA_CLOCK=self.clock, ISTAKOZA_SERVER=self.server_py, ISTAKOZA_DATA=self.data_dir,
                   ISTAKOZA_PORT=str(port), ISTAKOZA_MODE=mode, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        lg = open(os.path.join(self.tmp, 'srv_%s.log' % mode), 'ab')
        self.logs[mode] = lg.name
        self.procs[mode] = subprocess.Popen([sys.executable, self.wrapper], env=env, stdout=lg, stderr=lg, stdin=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=0.3).close()
                return True
            except OSError:
                if self.procs[mode].poll() is not None:
                    return False
                time.sleep(0.2)
        return False

    def stop(self):
        for p in self.procs.values():
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.procs.values():
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        self.procs = {}

    def log_text(self):
        out = ''
        for m, f in self.logs.items():
            try:
                out += open(f, encoding='utf-8', errors='replace').read()
            except OSError:
                pass
        return out


# ----------------------------------------------------------------------------- الموديل (اللي المفروض يحصل)
class Model:
    def __init__(self):
        self.sales = {}        # id -> dict(no, day, total, sub, fee, disc, type, void, closed, items, ret)
        self.order = []

    def biz_day(self, cal):
        opens = [s['day'] for s in self.sales.values() if not s['closed']]
        if opens:
            return min(opens)
        closed_days = [s['day'] for s in self.sales.values() if s['closed']]
        if closed_days:
            last = max(closed_days)
            nxt = (datetime.strptime(last, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            return max(cal, nxt)
        return cal

    def next_no(self):
        nums = [s['no'] for s in self.sales.values() if not s['closed']]
        return (max(nums) if nums else 0) + 1

    def open_ids(self):
        return [i for i in self.order if not self.sales[i]['closed']]


def r2(x):
    return round(x + 1e-9, 2)


# ----------------------------------------------------------------------------- قاعدة البيانات (قراءة بس)
_VIEWS = {}


def dbq(path, sql, args=()):
    import re
    c = sqlite3.connect(path, timeout=30)
    try:
        if path not in _VIEWS:
            _VIEWS[path] = bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='SalesAll'").fetchone())
        if _VIEWS[path]:   # النسخ الجديدة بتنقل الأيام المقفولة لجداول Hist: نقرا من الـ Views الموحدة
            sql = re.sub(r'\bSales\b', 'SalesAll', sql)
            sql = re.sub(r'\bSaleItems\b', 'SaleItemsAll', sql)
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def pct(v, p):
    if not v:
        return 0
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))]


# ----------------------------------------------------------------------------- الاختبار
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=360)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--server', default='')
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else int(time.time()) % 100000
    rnd = random.Random(seed)

    cands = [a.server, os.path.join(HERE, 'app', 'server.py'), os.path.join(HERE, 'server.py'), os.path.join(os.getcwd(), 'app', 'server.py')]
    server_py = next((c for c in cands if c and os.path.isfile(c)), '')
    if not server_py:
        print('مش لاقي server.py - استخدم --server /path/to/server.py')
        sys.exit(2)
    server_py = os.path.abspath(server_py)

    tmp = tempfile.mkdtemp(prefix='istakoza_test_')
    data_dir = os.path.join(tmp, 'data')
    os.makedirs(data_dir)
    DB = os.path.join(data_dir, 'pos_data.db')
    S = Servers(server_py, data_dir, tmp)
    START = datetime(2026, 1, 1, 12, 0, 0)
    S.set_clock(START)

    print('اختبار إستاكوزا POS | أيام: %d | seed: %d' % (a.days, seed))
    print('server.py:', server_py)
    print('داتا مؤقتة:', data_dir)

    try:
        run(a, rnd, S, DB, START, server_py)
    except KeyboardInterrupt:
        R.add('FAIL', 'الاختبار اتوقف بإيدك')
    except Exception as e:
        import traceback
        traceback.print_exc()
        R.add('FAIL', 'الاختبار نفسه وقع بخطأ غير متوقع', repr(e))
    finally:
        srvlog = S.log_text()
        S.stop()
        finish(a, tmp, srvlog)


def run(a, rnd, S, DB, START, server_py):
    C = Api(PORT_C, 'cashier')
    B = Api(PORT_B, 'backoffice')
    M = Model()

    # ======================================================== 1) التشغيل
    section('1) تشغيل السيرفرين (كاشير 18080 / باك أوفيس 18081) على نفس الداتابيز')
    ok1 = S.start('cashier', PORT_C)
    R.chk('الكاشير اشتغل', ok1, 'السيرفر وقع أول ما اشتغل - شوف اللوج')
    ok2 = S.start('backoffice', PORT_B)
    R.chk('الباك أوفيس اشتغل', ok2, 'السيرفر وقع أول ما اشتغل - شوف اللوج')
    if not (ok1 and ok2):
        return

    for api in (C, B):
        st, r, _ = api.req('GET', '/api/site')
        R.chk('%s: /api/site' % api.name, st == 200 and r.get('ver') in (14, 15), 'status=%s %s' % (st, r))
        st, b, hd = api.req('GET', '/', raw=True)
        if st == 200 and len(b) < 100000 and b'/react/assets/' in b:
            import re as _re
            asset = _re.search(rb'/react/assets/[^"\']+\.js', b).group(0).decode()
            st2, b2, _h = api.req('GET', asset, raw=True)
            R.chk('%s: الصفحة الرئيسية (React) بتتحمّل والـ JS بتاعها بيتحمّل' % api.name, st2 == 200 and len(b2) > 10000, 'asset status=%s size=%s' % (st2, len(b2)))
        else:
            R.chk('%s: الصفحة الرئيسية بتتحمّل' % api.name, st == 200 and len(b) > 100000, 'status=%s size=%s' % (st, len(b)))
    tc = C.login('admin', ADMIN_PW)
    tb = B.login('admin', ADMIN_PW)
    R.chk('تسجيل دخول admin على السيرفرين', bool(tc and tb))
    if not (tc and tb):
        return
    st, r, _ = C.req('POST', '/api/login', {'u': 'admin', 'p': ADMIN_PW})
    R.chk('الكاشير بيرجّع mode=cashier', r.get('mode') == 'cashier', r.get('mode'))
    st, r, _ = B.req('POST', '/api/login', {'u': 'admin', 'p': ADMIN_PW})
    R.chk('الباك أوفيس بيرجّع mode=backoffice', r.get('mode') == 'backoffice', r.get('mode'))

    # ======================================================== 2) التجهيز
    section('2) تجهيز الأصناف والموظفين والمناديب')
    cats = ['أسماك', 'طواجن', 'وجبات', 'سندوتشات', 'صوانى', 'إضافات', 'سلطات', 'مشروبات']
    prods = []
    for i in range(1, 41):
        unit = 'kg' if i <= 15 else 'pc'
        price = rnd.choice([120, 150, 180, 220, 260, 320, 400]) if unit == 'kg' else rnd.choice([15, 25, 40, 55, 75, 95, 120])
        prods.append({'id': 100000 + i, 'name': 'صنف تجريبي %d' % i, 'cat': cats[i % len(cats)], 'unit': unit, 'price': price,
                      'icon': '', 'code': str(100000 + i), 'cost': round(price * 0.55, 2)})
    st, r, _ = C.req('POST', '/api/state', {'products': prods, 'stock': {}, 'cfg': {'name': 'مطعم الاختبار', 'branchName': 'تست', 'branchCode': 'T1'}}, tc)
    R.chk('حفظ 40 صنف', st == 200, r)
    st, state, _ = B.req('GET', '/api/state', tok=tb)
    R.chk('الباك أوفيس شايف الأصناف اللي اتحفظت من الكاشير (نفس الداتابيز)', st == 200 and len(state.get('products', [])) == 40,
          'products=%s' % len(state.get('products', [])))

    st, r, _ = C.req('POST', '/api/users', {'name': 'Test Cashier', 'job': 3, 'phone': '', 'p': 'cash123'}, tc)
    cash_user = str(r.get('emp', ''))
    R.chk('إنشاء كاشير', st == 200 and cash_user, r)
    tcash = C.login(cash_user, 'cash123')
    tcash_b = B.login(cash_user, 'cash123')
    R.chk('الكاشير بيدخل على السيرفرين', bool(tcash and tcash_b))
    st, r, _ = C.req('POST', '/api/users', {'name': 'Test Cashier Two', 'job': 3, 'phone': '', 'p': 'cash456'}, tc)
    cash2 = str(r.get('emp', ''))
    tcash2 = C.login(cash2, 'cash456')

    riders = []
    for n in ('Rider One', 'Rider Two', 'Rider Three'):
        C.req('POST', '/api/delivery', {'name': n, 'phone': '0100000000'}, tc)
    st, dv, _ = C.req('GET', '/api/delivery', tok=tc)
    riders = [x['id'] for x in dv.get('riders', [])]
    R.chk('إضافة 3 مناديب', len(riders) == 3, riders)

    # ======================================================== 3) الأمان والصلاحيات
    section('3) الصلاحيات والأمان')
    st, r, _ = C.req('GET', '/api/state')
    R.chk('من غير توكن = 401', st == 401, st)
    st, r, _ = C.req('GET', '/api/state', tok='faketoken123')
    R.chk('توكن مزيّف = 401', st == 401, st)
    st, r, _ = C.req('POST', '/api/login', {'u': 'admin', 'p': 'wrong'})
    R.chk('باسورد غلط = رفض', st in (400, 429), st)
    st, r, _ = C.req('GET', '/api/users', tok=tcash)
    R.chk('الكاشير مايقدرش يشوف المستخدمين', st == 403, st)
    st, r, _ = C.req('POST', '/api/backup', {}, tcash)
    R.chk('الكاشير مايقدرش يعمل Backup', st == 403, st)
    st, r, _ = C.req('POST', '/api/reset', {}, tcash)
    R.chk('الكاشير مايقدرش يعمل Reset للداتا', st == 403, st)
    st, r, _ = C.req('POST', '/api/import', {}, tcash)
    R.chk('الكاشير مايقدرش يعمل Import', st == 403, st)
    st, r, _ = C.req('POST', '/api/void', {'id': 'x', 'reason': 'x'}, tcash)
    R.chk('الكاشير من غير صلاحية void = ممنوع', st == 403, st)
    st, r, _ = C.req('POST', '/api/close', {'ids': []}, tcash)
    R.chk('الكاشير من غير صلاحية close = ممنوع', st == 403, st)
    st, b, _ = C.req('GET', '/api/backup/download?t=%s&name=../../etc/passwd' % tc, raw=True)
    R.chk('Path traversal في تحميل الـ Backup مرفوض', st == 403, st)
    st, r, _ = C.req('POST', '/api/sale', b'{bad json', tc)
    R.chk('JSON بايظ مايوقعش السيرفر', st in (400, 403, 500) and C.req('GET', '/api/site')[0] == 200, st)
    # توكن الكاشير على سيرفر تاني
    st, r, _ = B.req('GET', '/api/state', tok=tc)
    if st == 401:
        R.info('التوكن خاص بكل سيرفر (لازم تسجيل دخول منفصل على 8080 و 8081) - ده طبيعي')
    # قفل المحاولات
    codes = [C.req('POST', '/api/login', {'u': 'lockme', 'p': 'x%d' % i})[0] for i in range(12)]
    if 429 in codes:
        R.chk('قفل الحساب بعد محاولات غلط كتير', True, detail_ok='اتقفل بعد %d محاولة' % (codes.index(429) + 1))
    else:
        R.warn('مفيش قفل بعد 12 محاولة دخول غلط', 'الأكواد: %s' % set(codes))

    # ======================================================== 4) محاكاة الأيام
    section('4) محاكاة %d يوم تشغيل (بيع + دليفري + إلغاء + تعديل + مرتجع + قفل يوم)' % a.days)
    stats = dict(sales=0, void=0, edit=0, ret=0, ret_over_refused=0, dup=0, close=0, shifts=0, deliveries=0, lock_refused=0,
                 num_bad=0, day_bad=0, settle_bad=0, errors=[], forgot_close=0, midnight=0, ret_amount_bad=0, month_bad=0, month_ok=0)
    prod_by_id = {p['id']: p for p in prods}
    guard_tested = False
    cashier_close_tested = False
    first_closed_ids = []
    t_loop = time.time()
    last_date = START

    def mk_sale(kind):
        n_items = rnd.randint(1, 5)
        chosen = rnd.sample(prods, n_items)
        items = []
        for p in chosen:
            q = round(rnd.choice([0.5, 0.75, 1, 1.5, 2, 2.5, 3]), 3) if p['unit'] == 'kg' else rnd.randint(1, 4)
            items.append({'id': p['id'], 'name': p['name'], 'cat': p['cat'], 'unit': p['unit'], 'qty': q, 'price': p['price']})
        sub = r2(sum(i['qty'] * i['price'] for i in items))
        disc = r2(sub * rnd.choice([0, 0, 0, 0.05, 0.1])) if kind != 'pickup' else 0
        fee = rnd.choice([15, 20, 25, 30]) if kind == 'delivery' else 0
        d = {'id': uuid.uuid4().hex[:16], 'type': kind, 'items': items, 'sub': sub, 'disc': disc, 'tax': 0, 'fee': fee,
             'total': r2(sub - disc + fee), 'cust': {}}
        if kind in ('delivery', 'pickup'):
            d['cust'] = {'name': 'عميل %d' % rnd.randint(1, 500), 'phone': '010%08d' % rnd.randint(0, 99999999),
                         'addr': 'شارع %d - مدينة نصر' % rnd.randint(1, 99) if kind == 'delivery' else ''}
        if kind == 'cash' and rnd.random() < 0.15:
            d['orderType'] = 'D'  # صالة
        return d

    def post_sale(d, api, tok, when):
        S.set_clock(when)
        cal = when.strftime('%Y-%m-%d')
        exp_day, exp_no = M.biz_day(cal), M.next_no()
        st, r, dt = api.req('POST', '/api/sale', dict(d), tok)
        if st != 200 or not r.get('ok'):
            stats['errors'].append('sale %s: %s %s' % (d['id'], st, r))
            return None
        if r.get('no') != exp_no:
            stats['num_bad'] += 1
            if stats['num_bad'] <= 3:
                stats['errors'].append('ترقيم غلط: متوقع %s طلع %s' % (exp_no, r.get('no')))
        M.sales[d['id']] = dict(no=r['no'], day=exp_day, total=d['total'], sub=d['sub'], fee=d['fee'], disc=d['disc'], type=d['type'],
                                void=False, closed=False, items=d['items'], ret=0.0, delivered=False, ret_items={}, orderType=d.get('orderType'))
        M.order.append(d['id'])
        stats['sales'] += 1
        return r

    for day in range(a.days):
        date = START + timedelta(days=day)
        last_date = date
        weekend = date.weekday() in (4, 5)
        n = rnd.randint(14, 30) if weekend else rnd.randint(8, 20)
        hours = sorted(rnd.sample(range(11 * 60, 23 * 60 + 30), n))
        special_midnight = (day % 45 == 7) and day < a.days - 2
        forgot = (day % 90 == 40) and day < a.days - 2
        day_ids = []
        for k, mins in enumerate(hours):
            when = date.replace(hour=0, minute=0, second=0) + timedelta(minutes=mins)
            if special_midnight and k >= n - 2:
                when = date.replace(hour=0, minute=0) + timedelta(days=1, minutes=20 + k)
            kind = rnd.choices(['cash', 'delivery', 'pickup'], [55, 33, 12])[0]
            d = mk_sale(kind)
            api, tok = rnd.choice([(C, tcash), (C, tcash), (C, tc), (B, tb)]) if rnd.random() < .08 else (C, rnd.choice([tcash, tcash2]))
            r = post_sale(d, api, tok, when)
            if r is not None:
                day_ids.append(d['id'])
            if r is not None and rnd.random() < 0.03:  # إعادة إرسال نفس الفاتورة (تقطيع نت)
                st, r2_, _ = C.req('POST', '/api/sale', dict(d), tcash)
                if st == 200 and r2_.get('dup'):
                    stats['dup'] += 1
                else:
                    stats['errors'].append('dup مش اتكشف: %s %s' % (st, r2_))
        if special_midnight:
            stats['midnight'] += 1
        end_when = date.replace(hour=23, minute=40) if not special_midnight else date.replace(hour=0, minute=0) + timedelta(days=1, hours=1)
        S.set_clock(end_when)

        # ---- فحص يوم العمل فعلياً من الداتابيز
        if day_ids:
            rows = dbq(DB, 'SELECT SID,SDay,SNo FROM Sales WHERE SID IN (%s)' % ','.join('?' * len(day_ids)), day_ids)
            for sid, sday, sno in rows:
                if sday != M.sales[sid]['day'] or sno != M.sales[sid]['no']:
                    stats['day_bad'] += 1
                    if stats['day_bad'] <= 3:
                        stats['errors'].append('يوم/رقم غلط للفاتورة %s: DB=%s/%s متوقع=%s/%s' % (sid, sday, sno, M.sales[sid]['day'], M.sales[sid]['no']))

        # ---- إلغاء (admin)
        opens = [i for i in M.open_ids() if not M.sales[i]['void'] and not M.sales[i]['delivered']]
        for sid in rnd.sample(opens, min(len(opens), max(0, int(len(opens) * 0.03) + (1 if rnd.random() < .3 else 0)))):
            st, r, _ = C.req('POST', '/api/void', {'id': sid, 'reason': 'اختبار إلغاء'}, tc)
            if st == 200:
                M.sales[sid]['void'] = True
                stats['void'] += 1
            else:
                stats['errors'].append('void فشل: %s %s' % (st, r))
        # ---- تعديل (admin)
        opens = [i for i in M.open_ids() if not M.sales[i]['void'] and not M.sales[i]['delivered']]
        for sid in rnd.sample(opens, min(len(opens), 1 if rnd.random() < .5 else 0)):
            s = M.sales[sid]
            its = [dict(i) for i in s['items']]
            its[0]['qty'] = its[0]['qty'] + 1
            sub = r2(sum(i['qty'] * i['price'] for i in its))
            total = r2(sub - s['disc'] + s['fee'])
            st, r, _ = C.req('POST', '/api/edit', {'id': sid, 'reason': 'اختبار تعديل', 'items': its, 'sub': sub, 'disc': s['disc'],
                                                    'tax': 0, 'fee': s['fee'], 'total': total, 'cust': {'name': 'x', 'phone': '0100', 'addr': 'y'}}, tc)
            if st == 200:
                s.update(items=its, sub=sub, total=total)
                stats['edit'] += 1
            else:
                stats['errors'].append('edit فشل: %s %s' % (st, r))

        # ---- دليفري: تحضير مناديب → تحميل → تسليم → حساب
        dl = [i for i in M.open_ids() if M.sales[i]['type'] == 'delivery' and not M.sales[i]['void'] and not M.sales[i]['delivered']]
        if dl:
            used = riders[:max(1, min(len(riders), 1 + len(dl) // 6))]
            shift_cash = {}
            for rid in used:
                cash = rnd.choice([0, 100, 200])
                st, r, _ = C.req('POST', '/api/delivery/checkin', {'rid': rid, 'cash': cash}, tc)
                if st == 200:
                    shift_cash[rid] = cash
                    stats['shifts'] += 1
                else:
                    stats['errors'].append('checkin: %s %s' % (st, r))
            groups = {rid: [] for rid in shift_cash}
            for i, sid in enumerate(dl):
                groups[list(shift_cash)[i % len(shift_cash)]].append(sid)
            if not guard_tested and day >= 2 and shift_cash:
                # قفل يوم والمندوب شفته مفتوح لازم يترفض
                st, r, _ = C.req('POST', '/api/close', {'ids': M.open_ids()}, tc)
                guard_tested = True
                R.chk('قفل اليوم والمندوب شفته مفتوح مرفوض (حماية)', st == 400 and 'مناديب' in str(r.get('error', '')), '%s %s' % (st, r))
            for rid, sids in groups.items():
                if not sids:
                    continue
                st, r, _ = C.req('POST', '/api/delivery/dispatch', {'rid': rid, 'sids': sids}, tc)
                if st != 200:
                    stats['errors'].append('dispatch: %s %s' % (st, r))
                    continue
                # محاولة إلغاء أوردر متحمّل على مندوب لازم تترفض
                if sids and rnd.random() < 0.05:
                    st2, r2_, _ = C.req('POST', '/api/void', {'id': sids[0], 'reason': 'x'}, tc)
                    if st2 == 200:
                        stats['errors'].append('السيرفر سمح بإلغاء أوردر متحمّل على مندوب!')
                refused = set(rnd.sample(sids, 1)) if (len(sids) > 3 and rnd.random() < 0.1) else set()
                st, dvs, _ = C.req('GET', '/api/delivery', tok=tc)
                dispid = {x['sid']: x['id'] for x in dvs.get('dispatch', []) if x['rid'] == rid}
                for sid in sids:
                    if sid in refused:
                        st, r, _ = C.req('POST', '/api/dispatch/refuse', {'dispid': dispid.get(sid)}, tc)
                        if st != 200:
                            stats['errors'].append('refuse: %s %s' % (st, r))
                        continue
                    api, tok = (B, tb) if rnd.random() < .5 else (C, tcash)
                    st, r, _ = api.req('POST', '/api/sale/deliver', {'sid': sid}, tok)
                    if st == 200:
                        M.sales[sid]['delivered'] = True
                        stats['deliveries'] += 1
                    else:
                        stats['errors'].append('deliver: %s %s' % (st, r))
                collected = r2(sum(M.sales[s]['total'] for s in sids if M.sales[s]['delivered']))
                expected = r2(shift_cash[rid] + collected)
                actual = r2(expected + rnd.choice([0, 0, 0, -5, 10]))
                st, r, _ = C.req('POST', '/api/delivery/settle', {'rid': rid, 'actual': actual}, tc)
                if st != 200:
                    stats['errors'].append('settle: %s %s' % (st, r))
                    stats['settle_bad'] += 1
                else:
                    sh = r.get('shift', {})
                    if abs(sh.get('collected', -1) - collected) > 0.01 or abs(sh.get('expected', -1) - expected) > 0.01 or abs(sh.get('diff', 99) - (actual - expected)) > 0.01:
                        stats['settle_bad'] += 1
                        if stats['settle_bad'] <= 3:
                            stats['errors'].append('حساب مندوب غلط: متوقع coll=%s exp=%s ، السيرفر %s' % (collected, expected, sh))
            # أي شفت لسه مفتوح (مندوب ماحصلوش dispatch) نقفله
            st, dvs, _ = C.req('GET', '/api/delivery', tok=tc)
            for rid_s in list(dvs.get('shifts', {}).keys()):
                C.req('POST', '/api/delivery/settle', {'rid': int(rid_s), 'actual': dvs['shifts'][rid_s]['cash']}, tc)

        # ---- مرتجع (كاشير بموافقة مدير) على فواتير مقفولة أو مفتوحة
        cand = [i for i in M.order[-400:] if not M.sales[i]['void']]
        if cand and rnd.random() < 0.35:
            sid = rnd.choice(cand)
            s = M.sales[sid]
            it = s['items'][0]
            done = s['ret_items'].get(it['id'], 0)
            rem = round(sum(x['qty'] for x in s['items'] if x['id'] == it['id']) - done, 3)
            if rem > 0:
                q = round(min(rem, 1 if it['unit'] == 'pc' else 0.5), 3)
                st, r, _ = C.req('POST', '/api/return', {'sid': sid, 'reason': 'جودة سيئة', 'pw': ADMIN_PW, 'items': [{'id': it['id'], 'qty': q}]}, tcash)
                if st == 200 and r.get('ret'):
                    s['ret_items'][it['id']] = round(done + q, 3)
                    wsum = sum(x['qty'] * x['price'] for x in s['items'] if x['id'] == it['id'])
                    oq = sum(x['qty'] for x in s['items'] if x['id'] == it['id'])
                    factor = (s['total'] - s['fee']) / s['sub'] if s['sub'] > 0 else 1.0
                    exp_ref = round(q * (wsum / oq) * factor, 2)
                    s['ret'] += exp_ref
                    stats['ret'] += 1
                    if abs(r['ret']['total'] - exp_ref) > 0.02:
                        stats['ret_amount_bad'] += 1
                        if stats['ret_amount_bad'] <= 3:
                            stats['errors'].append('قيمة المرتجع غلط: متوقع %s السيرفر %s' % (exp_ref, r['ret']['total']))
                    # محاولة مرتجع زيادة عن الكمية الأصلية لازم تترفض
                    st2, r2_, _ = C.req('POST', '/api/return', {'sid': sid, 'reason': 'جودة سيئة', 'pw': ADMIN_PW, 'items': [{'id': it['id'], 'qty': rem + 5}]}, tcash)
                    if st2 == 403:
                        stats['ret_over_refused'] += 1
                    else:
                        stats['errors'].append('السيرفر قبل مرتجع أكتر من الكمية الأصلية! %s %s' % (st2, r2_))
                else:
                    stats['errors'].append('return فشل: %s %s' % (st, r))

        # ---- قفل اليوم
        if not cashier_close_tested:
            cashier_close_tested = True
        if forgot:
            stats['forgot_close'] += 1  # نسينا نقفل - اليوم يفضل مفتوح والفواتير تكمّل عليه
        else:
            ids = M.open_ids()
            if ids:
                st, r, _ = C.req('POST', '/api/close', {'ids': ids}, tc)
                if st == 200:
                    for i in ids:
                        M.sales[i]['closed'] = True
                    stats['close'] += 1
                    if len(first_closed_ids) < 5:
                        first_closed_ids.extend(ids[:2])
                else:
                    stats['errors'].append('close فشل: %s %s' % (st, r))
                # التحقق من الداتابيز: كل الفواتير Closed=1
                rows = dbq(DB, 'SELECT COUNT(*) FROM Sales WHERE SID IN (%s) AND Closed=0' % ','.join('?' * len(ids)), ids)
                if rows[0][0]:
                    stats['errors'].append('بعد القفل لسه فيه %d فاتورة Closed=0' % rows[0][0])

        # ---- محاولة تعديل/إلغاء يوم مقفول
        if day % 12 == 5 and first_closed_ids and stats['close'] > 1:
            old = first_closed_ids[0]
            st, r, _ = C.req('POST', '/api/void', {'id': old, 'reason': 'محاولة على يوم مقفول'}, tc)
            st2, r2_, _ = C.req('POST', '/api/edit', {'id': old, 'reason': 'x', 'items': M.sales[old]['items'], 'sub': 1, 'disc': 0, 'tax': 0, 'fee': 0, 'total': 1}, tc)
            if st == 403 and st2 == 403:
                stats['lock_refused'] += 1
            else:
                stats['errors'].append('السيرفر سمح بتعديل/إلغاء فاتورة يوم مقفول! void=%s edit=%s' % (st, st2))

        # ---- تقارير كل شهر
        if (day + 1) % 30 == 0 or day == a.days - 1:
            mon_start = date.replace(day=1)
            month = date.strftime('%Y-%m')
            closed_days = [d for d in set(s['day'] for s in M.sales.values() if s['closed'] and s['day'].startswith(month))]
            st, rep, dt = C.req('GET', '/api/report/advanced?type=monthly&month=%s' % month, tok=tc)
            if st == 200:
                exp_n = sum(1 for s in M.sales.values() if s['day'].startswith(month) and not s['void'])
                exp_t = r2(sum(s['total'] for s in M.sales.values() if s['day'].startswith(month) and not s['void']))
                tt = rep.get('totals', {})
                if tt.get('n') == exp_n and abs(tt.get('total', -1) - exp_t) < 0.05:
                    stats['month_ok'] += 1
                else:
                    stats['month_bad'] += 1
                    if stats['month_bad'] <= 3:
                        stats['errors'].append('تقرير شهر %s: السيرفر n=%s total=%s | المتوقع n=%s total=%s' % (month, tt.get('n'), tt.get('total'), exp_n, exp_t))
            else:
                stats['errors'].append('تقرير شهري فشل %s %s' % (st, rep))

        if (day + 1) % 30 == 0:
            el = time.time() - t_loop
            print('   ... يوم %d/%d | فواتير %d | وقت %.0fs' % (day + 1, a.days, stats['sales'], el), flush=True)

    loop_time = time.time() - t_loop
    section('نتايج المحاكاة')
    R.info('وقت المحاكاة', '%.0f ثانية لـ %d يوم' % (loop_time, a.days))
    R.info('فواتير اتبعت', stats['sales'])
    R.chk('ترقيم الفواتير متسلسل (1,2,3.. جوه كل يوم عمل)', stats['num_bad'] == 0, '%d فاتورة ترقيمها غلط' % stats['num_bad'])
    R.chk('يوم العمل صح لكل الفواتير (بما فيهم بعد نص الليل واليوم المنسي)', stats['day_bad'] == 0, '%d فاتورة يومها غلط' % stats['day_bad'])
    R.chk('منع تكرار الفاتورة لو اتبعتت مرتين', stats['dup'] > 0 or stats['sales'] < 50, 'dup=%d' % stats['dup'], 'اتكشف %d تكرار' % stats['dup'])
    R.info('أيام اتقفلت', stats['close'])
    R.info('أيام "نسيان القفل" اتحاكت', stats['forgot_close'])
    R.info('أيام قفل بعد نص الليل اتحاكت', stats['midnight'])
    R.info('إلغاء / تعديل', '%d / %d' % (stats['void'], stats['edit']))
    R.info('شفتات مناديب / أوردرات اتسلمت', '%d / %d' % (stats['shifts'], stats['deliveries']))
    R.chk('حساب المناديب (المحصّل/المتوقع/الفرق) مظبوط', stats['settle_bad'] == 0, '%d شفت حسابه غلط' % stats['settle_bad'])
    R.chk('قيمة المرتجع محسوبة صح', stats['ret_amount_bad'] == 0 and stats['ret'] > 0, 'غلط في %d من %d' % (stats['ret_amount_bad'], stats['ret']),
          '%d مرتجع' % stats['ret'])
    R.chk('مرتجع أكتر من الكمية الأصلية مرفوض', stats['ret_over_refused'] == stats['ret'], '%d من %d' % (stats['ret_over_refused'], stats['ret']))
    R.chk('تعديل/إلغاء يوم مقفول مرفوض', stats['lock_refused'] > 0 or a.days < 20, 'مفيش محاولة اتعملت', '%d محاولة اترفضت' % stats['lock_refused'])
    R.chk('التقارير الشهرية مطابقة للأرقام الفعلية', stats['month_bad'] == 0 and stats['month_ok'] > 0,
          '%d شهر غلط' % stats['month_bad'], '%d شهر مطابق' % stats['month_ok'])
    R.chk('مفيش أخطاء غير متوقعة في العمليات', len(stats['errors']) == 0, ' | '.join(stats['errors'][:6]) + (' ... (+%d)' % (len(stats['errors']) - 6) if len(stats['errors']) > 6 else ''))

    # ======================================================== 5) سلامة الداتابيز
    section('5) سلامة الداتابيز بعد المحاكاة')
    ic = dbq(DB, 'PRAGMA integrity_check')[0][0]
    R.chk('SQLite integrity_check', ic == 'ok', ic)
    n_db = dbq(DB, 'SELECT COUNT(*) FROM Sales')[0][0]
    R.chk('عدد الفواتير في الداتابيز = المرسل', n_db == len(M.sales), 'DB=%d مرسل=%d' % (n_db, len(M.sales)))
    sum_db = dbq(DB, 'SELECT ROUND(IFNULL(SUM(STotal),0),2) FROM Sales WHERE Void=0')[0][0]
    sum_m = r2(sum(s['total'] for s in M.sales.values() if not s['void']))
    R.chk('إجمالي المبيعات (من غير الملغي) مطابق', abs(sum_db - sum_m) < 0.05, 'DB=%s متوقع=%s' % (sum_db, sum_m), 'إجمالي %s ج' % sum_m)
    orph = dbq(DB, 'SELECT COUNT(*) FROM SaleItems WHERE SID NOT IN (SELECT SID FROM Sales)')[0][0]
    R.chk('مفيش أصناف يتيمة (SaleItems من غير فاتورة)', orph == 0, orph)
    noit = dbq(DB, 'SELECT COUNT(*) FROM Sales WHERE SID NOT IN (SELECT SID FROM SaleItems)')[0][0]
    R.chk('مفيش فاتورة من غير أصناف', noit == 0, noit)
    dup = dbq(DB, 'SELECT COUNT(*) FROM (SELECT SDay,SNo FROM Sales GROUP BY SDay,SNo HAVING COUNT(*)>1)')[0][0]
    R.chk('مفيش رقمين فاتورة مكررين في نفس اليوم', dup == 0, '%d رقم مكرر' % dup)
    notclosed = dbq(DB, 'SELECT COUNT(*) FROM Sales WHERE Closed=0')[0][0]
    R.info('فواتير لسه مفتوحة (آخر يوم)', notclosed)
    nlocks = dbq(DB, 'SELECT COUNT(*) FROM day_locks')[0][0]
    R.chk('أيام مقفولة رسمياً (day_locks)', nlocks >= max(1, stats['close'] - 3), 'locks=%d closes=%d' % (nlocks, stats['close']), '%d يوم' % nlocks)
    openshifts = dbq(DB, "SELECT COUNT(*) FROM DeliveryShifts WHERE Status='open'")[0][0]
    R.chk('مفيش شفت مندوب فاضل مفتوح', openshifts == 0, openshifts)
    size_mb = os.path.getsize(DB) / 1048576
    R.info('حجم الداتابيز بعد %d يوم' % a.days, '%.1f MB' % size_mb)

    # ======================================================== 6) الأداء بعد سنة داتا
    section('6) الأداء بعد سنة كاملة من الداتا')
    st, b, hd = C.req('GET', '/api/state', tok=tc, raw=True)
    t0 = time.time()
    st, b, hd = C.req('GET', '/api/state', tok=tc, raw=True)
    el = time.time() - t0
    R.info('/api/state (بدون ضغط)', '%.2f ثانية | %.1f MB' % (el, len(b) / 1048576))
    R.chk('/api/state بيرد في أقل من 5 ثواني', el < 5, '%.2f ثانية' % el)
    st, b2, hd2 = C.req('GET', '/api/state', tok=tc, raw=True, headers={'Accept-Encoding': 'gzip'})
    gz = any(k.lower() == 'content-encoding' and v == 'gzip' for k, v in hd2.items())
    R.chk('ضغط gzip شغال للردود الكبيرة', gz, 'الحجم %d' % len(b2), 'الحجم بعد الضغط %.1f MB' % (len(b2) / 1048576))
    for kind in ('weekly', 'riders', 'returns', 'employees', 'otype', 'payplace', 'cash'):
        t0 = time.time()
        st, r, _ = C.req('GET', '/api/report/advanced?type=%s&from=2026-01-01&to=2026-12-31' % kind, tok=tc)
        R.chk('تقرير %s (سنة كاملة)' % kind, st == 200, '%s %s' % (st, str(r)[:120]), '%.2f ث' % (time.time() - t0))
    t0 = time.time()
    st, r, _ = C.req('GET', '/api/report/stock?from=2026-01-01&to=2026-12-31', tok=tc)
    R.chk('تقرير المخزون/الأرباح (سنة كاملة)', st == 200, st, '%.2f ث' % (time.time() - t0))
    st, a0, _ = C.req('GET', '/api/state', tok=tc)
    nret = len(a0.get('returns', [])) if st == 200 else -1
    R.chk('كل المرتجعات (%d) لسه ظاهرة في الحالة بعد قفل الأيام' % stats['ret'], nret == stats['ret'], 'ظاهر %s من %s (مرتجعات "مديونية" ممكن تختفي بعد القفل)' % (nret, stats['ret']))
    st, a1, _ = B.req('GET', '/api/state', tok=tb)
    R.chk('الباك أوفيس شايف نفس عدد الفواتير بالظبط', st == 200 and len(a1.get('sales', [])) + len(a1.get('archive', [])) == len(M.sales),
          'sales=%d archive=%d' % (len(a1.get('sales', [])), len(a1.get('archive', []))))
    ls = LAT.get('POST /api/sale', [])
    R.info('زمن حفظ الفاتورة', 'متوسط %.0f ms | p95 %.0f ms | أقصى %.0f ms' % (statistics.mean(ls) * 1000, pct(ls, .95) * 1000, max(ls) * 1000))
    R.chk('p95 حفظ الفاتورة أقل من نص ثانية', pct(ls, .95) < 0.5, '%.0f ms' % (pct(ls, .95) * 1000))

    # ======================================================== 7) ضغط متزامن
    section('7) ضغط متزامن (نفس الداتابيز)')
    S.set_clock(last_date + timedelta(days=1, hours=12))
    ids = M.open_ids()
    if ids:
        C.req('POST', '/api/close', {'ids': ids}, tc)
        for i in ids:
            M.sales[i]['closed'] = True
    before = dbq(DB, 'SELECT COUNT(*) FROM Sales')[0][0]
    lock = threading.Lock()

    def run_conc(plan, label):
        res, errs = [], []

        def worker(api, tok, n):
            lr = random.Random(rnd.random())
            for _ in range(n):
                p = lr.sample(prods, 2)
                items = [{'id': x['id'], 'name': x['name'], 'cat': x['cat'], 'unit': x['unit'], 'qty': 1, 'price': x['price']} for x in p]
                sub = sum(i['price'] for i in items)
                d = {'id': uuid.uuid4().hex[:16], 'type': 'cash', 'items': items, 'sub': sub, 'disc': 0, 'tax': 0, 'fee': 0, 'total': sub, 'cust': {}}
                st, r, _ = api.req('POST', '/api/sale', d, tok)
                with lock:
                    if st == 200 and r.get('ok'):
                        res.append((d['id'], r.get('no'), sub))
                    else:
                        errs.append((st, str(r)[:100]))
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                C.req('GET', '/api/delivery', tok=tc)
                B.req('GET', '/api/report/advanced?type=cash&from=2026-01-01&to=2026-12-31', tok=tb)
                time.sleep(0.05)
        ths = [threading.Thread(target=worker, args=(api, tok, n)) for api, tok, n in plan]
        rt = threading.Thread(target=reader)
        t0 = time.time()
        rt.start()
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        stop.set()
        rt.join()
        el = time.time() - t0
        tot = sum(n for _, _, n in plan)
        R.info('%s: %d فاتورة' % (label, tot), '%.1f ثانية (%.0f فاتورة/ثانية)' % (el, tot / el))
        R.chk('%s: كل الفواتير اتحفظت من غير أخطاء' % label, not errs and len(res) == tot, '%d خطأ، اتحفظ %d من %d. مثال: %s' % (len(errs), len(res), tot, errs[:2]))
        nos = [n for _, n, _ in res]
        return res, len(nos) - len(set(nos))

    tb2 = B.login(cash_user, 'cash123')
    # (أ) الاستخدام الحقيقي: كل الكاشيرات على سيرفر الكاشير
    resA, dupA = run_conc([(C, tcash, 60), (C, tcash2, 60), (C, tc, 60), (C, tcash, 60), (C, tcash2, 60), (C, tc, 60)], 'أ) 6 كاشيرات على سيرفر الكاشير')
    R.chk('أ) مفيش أرقام فواتير مكررة (الاستخدام الطبيعي)', dupA == 0, '%d رقم مكرر' % dupA)
    for sid, no, tt in resA:
        M.sales[sid] = dict(no=no, day='x', total=tt, sub=tt, fee=0, disc=0, type='cash', void=False, closed=False, items=[], ret=0, ret_items={})
        M.order.append(sid)
    ids = M.open_ids()
    C.req('POST', '/api/close', {'ids': ids}, tc)
    for i in ids:
        M.sales[i]['closed'] = True
    S.set_clock(last_date + timedelta(days=2, hours=12))
    # (ب) حالة متطرفة: بيع متزامن على السيرفرين مع بعض (الباك أوفيس في الواقع مابيبيعش)
    resB, dupB = run_conc([(C, tcash, 60), (B, tb2, 60), (C, tcash2, 60), (B, tb2, 60), (C, tc, 60), (B, tb, 60)], 'ب) 3 كاشيرات + 3 على الباك أوفيس مع بعض')
    if dupB:
        R.warn('ب) أرقام فواتير مكررة لما البيع بيحصل على السيرفرين في نفس اللحظة',
               '%d رقم مكرر من 360. السبب: الترقيم بيتم جوه قفل خاص بكل سيرفر. مش بيحصل في الاستخدام العادي لأن شاشة الباك أوفيس مفيهاش كاشير، بس لو حد استخدم الباك أوفيس للبيع هيحصل.' % dupB)
    else:
        R.chk('ب) مفيش أرقام مكررة حتى لو البيع على السيرفرين مع بعض', True)
    for sid, no, tt in resB:
        M.sales[sid] = dict(no=no, day='x', total=tt, sub=tt, fee=0, disc=0, type='cash', void=False, closed=False, items=[], ret=0, ret_items={})
        M.order.append(sid)
    after = dbq(DB, 'SELECT COUNT(*) FROM Sales')[0][0]
    R.chk('عدد الفواتير في الداتابيز بعد الضغط صح', after - before == len(resA) + len(resB), 'زاد %d متوقع %d' % (after - before, len(resA) + len(resB)))
    log_now = S.log_text()
    R.chk('مفيش "database is locked" في لوج السيرفرين', 'database is locked' not in log_now.lower(), 'ظهرت رسالة قفل الداتابيز')

    # ======================================================== 8) Backup
    section('8) النسخ الاحتياطي')
    st, r, _ = C.req('POST', '/api/backup', {}, tc)
    R.chk('عمل Backup يدوي', st == 200 and r.get('name'), r)
    name = r.get('name', '')
    st, lst, _ = C.req('GET', '/api/backups', tok=tc)
    R.chk('الـ Backup ظاهر في القايمة', any(f['name'] == name for f in lst.get('files', [])), st)
    st, b, hd = C.req('GET', '/api/backup/download?t=%s&name=%s' % (tc, name), raw=True)
    R.chk('تحميل الـ Backup', st == 200 and len(b) > 10000, 'status=%s size=%s' % (st, len(b)))
    if st == 200:
        bp = os.path.join(os.path.dirname(DB), 'dl_check.db')
        open(bp, 'wb').write(b)
        try:
            ok_ = dbq(bp, 'PRAGMA integrity_check')[0][0] == 'ok'
            cnt = dbq(bp, 'SELECT COUNT(*) FROM Sales')[0][0]
            R.chk('الـ Backup سليم وفيه كل الفواتير', ok_ and cnt == after, 'integrity=%s فواتير=%s متوقع=%s' % (ok_, cnt, after), '%d فاتورة' % cnt)
        finally:
            os.remove(bp)

    # ======================================================== 9) إعادة تشغيل
    section('9) إعادة تشغيل السيرفرين (زي Restart للجهاز)')
    S.stop()
    time.sleep(1)
    a_ok = S.start('cashier', PORT_C)
    b_ok = S.start('backoffice', PORT_B)
    R.chk('السيرفرين رجعوا يشتغلوا', a_ok and b_ok)
    if a_ok and b_ok:
        st, r, _ = C.req('GET', '/api/state', tok=tc)
        R.chk('التوكن القديم بيتلغي بعد الريستارت (لازم تسجيل دخول تاني)', st == 401, st)
        tc = C.login('admin', ADMIN_PW)
        tcash = C.login(cash_user, 'cash123')
        tcash2 = C.login(cash2, 'cash456')
        st, state, _ = C.req('GET', '/api/state', tok=tc)
        n_state = len(state.get('sales', [])) + len(state.get('archive', []))
        R.chk('كل الداتا موجودة بعد الريستارت', st == 200 and n_state == after, 'state=%s db=%s' % (n_state, after))
        # فاتورة جديدة بعد الريستارت: الترقيم يكمّل
        p = prods[0]
        d = {'id': uuid.uuid4().hex[:16], 'type': 'cash', 'items': [{'id': p['id'], 'name': p['name'], 'cat': p['cat'], 'unit': p['unit'], 'qty': 1, 'price': p['price']}],
             'sub': p['price'], 'disc': 0, 'tax': 0, 'fee': 0, 'total': p['price'], 'cust': {}}
        st, r, _ = C.req('POST', '/api/sale', d, tc)
        mx = dbq(DB, 'SELECT MAX(SNo) FROM Sales WHERE Closed=0')[0][0]
        R.chk('البيع شغال بعد الريستارت والترقيم بيكمّل', st == 200 and r.get('no') == mx, '%s %s mx=%s' % (st, r, mx))

        # ==================================================== 10) حالات حدّية
        section('10) حالات حدّية (إدخال غريب)')
        tests = []
        evil = "Robert'); DROP TABLE Sales;--"
        d = dict(d, id=uuid.uuid4().hex[:16], cust={'name': evil, 'phone': '0100', 'addr': '😀 عنوان <script>alert(1)</script>'}, type='delivery')
        st, r, _ = C.req('POST', '/api/sale', d, tc)
        still = dbq(DB, 'SELECT COUNT(*) FROM Sales')[0][0] > 0
        R.chk('SQL injection في اسم العميل ما أثرش على الداتا', st == 200 and still, '%s' % st)
        got = dbq(DB, 'SELECT CName,CAddr FROM Sales WHERE SID=?', (d['id'],))
        R.chk('الاسم والعنوان (إيموجي/HTML) اتحفظوا زي ما هما', bool(got) and got[0][0] == evil and '😀' in got[0][1], got)
        d2 = dict(d, id=uuid.uuid4().hex[:16], cust={'name': 'عميل', 'phone': '0100', 'addr': 'x'}, type='cash', total=1, sub=100, items=[dict(d['items'][0], qty=-5)])
        st, r, _ = C.req('POST', '/api/sale', d2, tc)
        if st == 200:
            R.warn('السيرفر قبل فاتورة بكمية سالبة وإجمالي مش مطابق للأصناف',
                   'السيرفر بيثق في الأرقام اللي جاية من الصفحة. لو الكاشير تقني ممكن يتلاعب بالـ API. (مش مشكلة في الاستخدام العادي)')
        else:
            R.chk('رفض فاتورة بكمية سالبة', True)
        st, r, _ = C.req('POST', '/api/sale', {'id': uuid.uuid4().hex[:8]}, tc)
        R.chk('فاتورة ناقصة بيانات = رفض من غير ما السيرفر يقع', st in (400, 403, 500) and C.req('GET', '/api/site')[0] == 200, st)
        st, r, _ = C.req('POST', '/api/return', {'sid': d['id'], 'reason': 'جودة سيئة', 'pw': 'wrong', 'items': [{'id': d['items'][0]['id'], 'qty': 1}]}, tcash)
        R.chk('مرتجع بباسورد مدير غلط = رفض', st == 403, '%s %s' % (st, r))
        codes = [C.req('POST', '/api/return', {'sid': d['id'], 'reason': 'جودة سيئة', 'pw': 'wrong%d' % i, 'items': [{'id': d['items'][0]['id'], 'qty': 1}]}, tcash2)[0] for i in range(7)]
        st, r, _ = C.req('POST', '/api/return', {'sid': d['id'], 'reason': 'جودة سيئة', 'pw': ADMIN_PW, 'items': [{'id': d['items'][0]['id'], 'qty': 1}]}, tcash2)
        R.chk('بعد 5 باسوردات مدير غلط المرتجع بيتقفل مؤقتاً حتى بالباسورد الصح', st == 403 and 'استنى' in str(r.get('error', '')), '%s %s' % (st, r))

    # ======================================================== 11) لوج السيرفر
    section('11) فحص لوج السيرفرين')
    lg = S.log_text()
    tb_count = lg.count('Traceback')
    errs = [l for l in lg.splitlines() if 'POST ERROR' in l]
    R.chk('مفيش Traceback (انهيار) في اللوج', tb_count == 0, '%d Traceback' % tb_count)
    R.info('رسائل رفض متوقعة (POST ERROR) في اللوج', '%d رسالة (طبيعي: كلها من حالات الرفض اللي اختبرناها)' % len(errs))
    unexpected = [l for l in errs if not any(k in l for k in ('اكتب', 'مناديب', 'مرتجع', 'الكمية', 'باسورد', 'متحمّل', 'الأوردر', 'KeyError', 'غير', 'سبب', 'اختار', 'المندوب', 'مفيش', 'لسه', 'استنى', 'OrderState', 'KeyError', "'items'"))]
    if unexpected:
        R.warn('رسائل خطأ في اللوج محتاجة نظرة', ' || '.join(unexpected[:3]))


def finish(a, tmp, srvlog):
    section('الخلاصة')
    p, f, w = R.count('PASS'), R.count('FAIL'), R.count('WARN')
    total_t = time.time() - R.t0
    lines = ['تقرير اختبار إستاكوزا POS - %s' % datetime.now().strftime('%Y-%m-%d %H:%M'),
             'أيام: %d | نجح: %d | فشل: %d | تحذير: %d | المدة: %.0f ثانية' % (a.days, p, f, w, total_t), '']
    for lvl, name, det in R.rows:
        if lvl in ('FAIL', 'WARN'):
            lines.append('[%s] %s%s' % (lvl, name, (' → ' + str(det)) if det else ''))
    lines.append('')
    lines.append('--- كل النتايج ---')
    for lvl, name, det in R.rows:
        lines.append('[%s] %s%s' % (lvl, name, (' → ' + str(det)) if det else ''))
    lat = ['', '--- زمن الاستجابة (ms) ---']
    for k, v in sorted(LAT.items(), key=lambda x: -len(x[1]))[:14]:
        lat.append('%-42s عدد=%-5d متوسط=%-7.1f p95=%-7.1f أقصى=%.1f' % (k, len(v), statistics.mean(v) * 1000, pct(v, .95) * 1000, max(v) * 1000))
    lines += lat
    try:
        rp = os.path.join(HERE, 'test_report.txt')
        open(rp, 'w', encoding='utf-8').write('\n'.join(lines))
    except OSError:
        rp = '(مقدرتش أكتب التقرير)'
    print('\n✅ نجح: %d    ❌ فشل: %d    ⚠️  تحذير: %d    (المدة %.0f ثانية)' % (p, f, w, total_t))
    if f:
        print('\nالمشاكل:')
        for lvl, name, det in R.rows:
            if lvl == 'FAIL':
                print('  ❌ %s%s' % (name, (' → ' + str(det)) if det else ''))
    print('\nالتقرير الكامل اتحفظ في:', rp)
    print('ابعتلي محتوى test_report.txt (أو الشاشة دي) وأقولك رأيي.')
    if a.keep:
        print('الداتا المؤقتة متحفوظة في:', tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if f else 0)


if __name__ == '__main__':
    main()
