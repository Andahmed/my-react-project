#!/usr/bin/env python3
"""اختبار الـ API: دورة الدليفري كاملة + إن كل تاريخ تشغيلي مصدره جدول BusinessDay مش ساعة الجهاز.
التشغيل:  python3 tests/api_tests.py [مسار pos_data.db اختياري - بيتنسخ ومش بيتعدّل]"""
import glob, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(ROOT, 'app', 'server.py'); PORT = 18082
D = tempfile.mkdtemp(); FAKE = '2030-05-17'     # يوم عمل بعيد عن تاريخ الجهاز عمداً
if len(sys.argv) > 1: shutil.copy(sys.argv[1], D + '/pos_data.db')
env = dict(os.environ, ISTAKOZA_DATA=D, ISTAKOZA_PORT=str(PORT), ISTAKOZA_MODE='full')
procs = []
def start():
    p = subprocess.Popen([sys.executable, SRV, '--no-browser'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.dirname(SRV)); procs.append(p)
    for _ in range(60):
        try: urllib.request.urlopen('http://127.0.0.1:%d/api/site' % PORT, timeout=1); return p
        except Exception: time.sleep(.2)
    raise SystemExit('السيرفر مقامش')
def req(m, u, b=None, t=''):
    r = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, u), method=m, data=json.dumps(b).encode() if b is not None else None, headers={'Content-Type': 'application/json', 'X-Token': t})
    try:
        with urllib.request.urlopen(r, timeout=15) as x: return x.status, json.loads(x.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}
res = []
def chk(n, ok, x=''): res.append(bool(ok)); print('PASS' if ok else 'FAIL', n, str(x)[:150])
try:
    p = start(); p.terminate(); p.wait()
    c = sqlite3.connect(D + '/pos_data.db'); c.execute("UPDATE BusinessDay SET CurrentDay=? WHERE ID=1", (FAKE,)); c.commit(); c.close()
    start()
    T = req('POST', '/api/login', {'u': 'admin', 'p': 'admin'})[1]['token']
    chk('favicon بدون توكن = 204', urllib.request.urlopen('http://127.0.0.1:%d/favicon.ico' % PORT).status == 204)
    chk('/api/businessday = يوم العمل من الجدول', req('GET', '/api/businessday', None, T)[1].get('day') == FAKE)
    pl = req('GET', '/api/products', None, T)[1]; prods = pl.get('products', pl) if isinstance(pl, dict) else pl
    if not prods:
        st, r = req('POST', '/api/product/save', {'name': 'برجر', 'cat': 'ساندوتشات', 'unit': 'pc', 'price': 50, 'cost': 20}, T); prods = [{'id': r['id'], 'name': 'برجر', 'cat': 'ساندوتشات', 'unit': 'pc', 'price': 50}]
    pr = [x for x in prods if x.get('price', 0) > 0][0]
    def sale(qty=1, typ='delivery', fee=20):
        sub = pr['price'] * qty
        d = {'id': uuid.uuid4().hex[:16], 'type': typ, 'items': [{'id': pr['id'], 'name': pr['name'], 'cat': pr.get('cat', ''), 'unit': pr.get('unit', 'pc'), 'qty': qty, 'price': pr['price']}], 'sub': sub, 'disc': 0, 'tax': 0, 'fee': fee if typ == 'delivery' else 0, 'total': sub + (fee if typ == 'delivery' else 0), 'cust': {'name': 'عميل', 'phone': '0100', 'addr': 'شارع 1'}}
        st, r = req('POST', '/api/sale', d, T); assert st == 200, (st, r); return d
    s1, s2, s3, pk = sale(), sale(2), sale(), sale(typ='pickup')
    o = req('GET', '/api/delivery/orders', None, T)[1]; ids = {x['id']: x for x in o}
    chk('أوردرات الدليفري (من غير الاستلام) + الأصناف', {s1['id'], s2['id'], s3['id']} <= set(ids) and pk['id'] not in ids and ids[s1['id']]['items'] and 'state' in ids[s1['id']])
    chk('يوم الأوردر = يوم العمل', ids[s1['id']]['day'] == FAKE, ids[s1['id']]['day'])
    req('POST', '/api/delivery', {'id': 0, 'name': 'أحمد', 'phone': '011'}, T)
    rid = req('GET', '/api/delivery', None, T)[1]['riders'][0]['id']
    chk('تحميل قبل التحضير مرفوض', req('POST', '/api/delivery/dispatch', {'rid': rid, 'sids': [s1['id']]}, T)[0] != 200)
    chk('تحضير', req('POST', '/api/delivery/checkin', {'rid': rid, 'cash': 100}, T)[0] == 200)
    chk('تحضير مرتين مرفوض', req('POST', '/api/delivery/checkin', {'rid': rid, 'cash': 1}, T)[0] != 200)
    chk('تحميل 3', req('POST', '/api/delivery/dispatch', {'rid': rid, 'sids': [s1['id'], s2['id'], s3['id']]}, T)[0] == 200)
    chk('إعادة تحميل نفس الأوردر مرفوضة', req('POST', '/api/delivery/dispatch', {'rid': rid, 'sids': [s1['id']]}, T)[0] != 200)
    dv = req('GET', '/api/delivery', None, T)[1]; out = {d['sid']: d['id'] for d in dv['dispatch']}
    chk('الشفت المفتوح مختوم بيوم العمل', req('GET', '/api/delivery/shifts?from=%s&to=%s' % (FAKE, FAKE), None, T)[0] == 200)
    chk('اتسلّم', req('POST', '/api/dispatch/deliver', {'dispid': out[s1['id']]}, T)[0] == 200)
    chk('تسليم مرتين مرفوض', req('POST', '/api/dispatch/deliver', {'dispid': out[s1['id']]}, T)[0] != 200)
    chk('رفض', req('POST', '/api/dispatch/refuse', {'dispid': out[s2['id']]}, T)[0] == 200)
    chk('المحاسبة مرفوضة وفيه أوردر بره', req('POST', '/api/delivery/settle', {'rid': rid, 'actual': 1}, T)[0] != 200)
    chk('سحب', req('POST', '/api/dispatch/pull', {'dispid': out[s3['id']]}, T)[0] == 200)
    # الكاشير يسلّم أوردر وهو على المندوب
    chk('أوردر الكاشير المتسلّم بيتحمّل مرفوض', (lambda: (req('POST', '/api/sale/deliver', {'sid': s2['id']}, T), req('POST', '/api/delivery/dispatch', {'rid': rid, 'sids': [s2['id']]}, T)[0] != 200)[1])())
    exp = 100 + s1['total']
    st, r = req('POST', '/api/delivery/settle', {'rid': rid, 'actual': exp - 5}, T)
    chk('محاسبة: متوقع/فرق', st == 200 and r['shift']['expected'] == exp and r['shift']['diff'] == -5, r)
    sh = req('GET', '/api/delivery/shifts?from=%s&to=%s' % (FAKE, FAKE), None, T)[1]
    chk('الشفت في السجل بيوم العمل مش بيوم الجهاز', len(sh) == 1 and sh[0]['day'] == FAKE, [x.get('day') for x in sh])
    today = time.strftime('%Y-%m-%d')
    chk('مفيش شفتات بتاريخ الجهاز', req('GET', '/api/delivery/shifts?from=%s&to=%s' % (today, today), None, T)[1] == [])
    # التقارير: الافتراضيات من يوم العمل
    r = req('GET', '/api/report/advanced?type=monthly', None, T); chk('التقرير الشهري الافتراضي = شهر يوم العمل', r[0] == 200 and '2030-05' in json.dumps(r[1]) and today[:7] not in json.dumps(r[1]).replace(FAKE, ''), r[0])
    r = req('GET', '/api/report/advanced?type=summary', None, T); chk('بيان الملخص الافتراضي = يوم العمل', r[0] == 200 and FAKE in json.dumps(r[1]), r[0])
    r = req('GET', '/api/report/advanced?type=riders&from=%s&to=%s' % (today, today), None, T)
    chk('تقرير المناديب بتاريخ الجهاز: صفر أوردرات وشفتات', r[0] == 200 and r[1]['riders'][0]['orders'] == 0 and r[1]['riders'][0]['shifts'] == 0, r[1]['riders'])
    r2 = req('GET', '/api/report/advanced?type=riders&from=2030-05-01&to=2030-05-31', None, T)
    chk('تقرير المناديب بيوم العمل: 3 أوردرات وشفت واحد', r2[0] == 200 and r2[1]['riders'][0]['orders'] == 3 and r2[1]['riders'][0]['shifts'] == 1 and r2[1]['riders'][0]['diff'] == -5, r2[1]['riders'])
    # قفل اليوم
    st, sl = req('GET', '/api/state', None, T)
    chk('قفل اليوم', req('POST', '/api/close', {'ids': [x['id'] for x in sl['sales']]}, T)[0] == 200)
    chk('اليوم اتحرّك للتالي', req('GET', '/api/businessday', None, T)[1]['day'] == '2030-05-18')
    chk('حذف مندوب له شفتات = إيقاف بس', (req('POST', '/api/delivery', {'id': rid, 'del': 1}, T), len(req('GET', '/api/delivery', None, T)[1]['riders']) == 1)[1])
    chk('من غير توكن = 401', req('POST', '/api/delivery', {'id': 0, 'name': 'x'}, '')[0] == 401)
finally:
    for p in procs:
        try: p.terminate()
        except Exception: pass
print('TOTAL', sum(res), '/', len(res)); sys.exit(0 if all(res) else 1)
