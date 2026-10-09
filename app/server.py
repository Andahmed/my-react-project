# -*- coding: utf-8 -*-
# ★ للتعديل: افتح EDIT_MAP.txt - ابحث في الملف ده عن [EDIT-MAP] عشان توصل للأماكن المهمة ★
"""
سيرفر نقطة البيع - إستاكوزا  (Istakoza POS server)
- بايثون فقط (من غير مكتبات خارجية): http.server + sqlite3
- بيقدّم الصفحة pos_istakoza.html وبيحفظ كل البيانات في قاعدة SQLite (pos_data.db)
- مكان البيانات: C:\\ProgramData\\Istakoza  (لما يتشغل كـ exe)  أو فولدر البرنامج (لما يتشغل من السورس)
  ممكن تغييره بمتغير البيئة ISTAKOZA_DATA، والبورت بـ ISTAKOZA_PORT (الافتراضي 8080)
"""
import time
import os, sys, re, json, base64, hashlib, secrets, threading, queue, socket, ipaddress, sqlite3, webbrowser, subprocess, gzip, mimetypes
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ★★★ [EDIT-MAP] رقم إصدار السيرفر (لو غيّرته غيّر معاه pos-ui-version في أول الصفحة) ★★★
VERSION = 15
FROZEN = getattr(sys, 'frozen', False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
# MODE: cashier = كاشير+صالة+استلام فرع فقط | backoffice = مخازن+كل الأوردرات+دليفري (من غير نافذة الكاشير)
MODE = os.environ.get('ISTAKOZA_MODE', 'full').lower().strip()
if MODE not in ('cashier', 'backoffice', 'full'):
    MODE = 'full'


def data_dir():
    d = os.environ.get('ISTAKOZA_DATA')
    if not d and os.name == 'nt':
        d = os.path.join(os.environ.get('PROGRAMDATA', APP_DIR), 'Istakoza')
    d = d or APP_DIR
    os.makedirs(d, exist_ok=True)
    return d


def resource_path(rel):
    # لو شغال كـ exe: الصفحة متحشورة جوه الـ exe (PyInstaller --add-data) وبتتفك في sys._MEIPASS
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


def ui_version(path):
    try:
        m = re.search(r'name="pos-ui-version" content="(\d+)"', open(path, encoding='utf-8').read(65536))
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def find_html():
    # 1) نسخة معدّلة اختيارية في فولدر الداتا (تتجاهل لو أقدم من السيرفر عشان ماتبوّظش التوافق)
    # 2) الصفحة المتحشورة جوه الـ exe  3) ملف جنب البرنامج (للتشغيل من السورس)
    custom = os.path.join(data_dir(), 'pos_istakoza.html')
    if os.path.isfile(custom):
        if ui_version(custom) >= VERSION:
            return custom
        print('تجاهل pos_istakoza.html القديمة في فولدر الداتا (إصدارها أقدم من السيرفر):', custom)
    for p in (resource_path('pos_istakoza.html'), os.path.join(APP_DIR, 'pos_istakoza.html')):
        if os.path.isfile(p):
            return p
    return resource_path('pos_istakoza.html')


DB = os.path.join(data_dir(), 'pos_data.db')
HTML = find_html()
REACT_DIR = os.path.join(APP_DIR, 'react-dist')
PORT = int(os.environ.get('ISTAKOZA_PORT', '8080'))
LOCK = threading.Lock()
TOK = {}
SUBS, SUBS_LOCK = set(), threading.Lock()
SRC = secrets.token_hex(8)  # هوية السيرفر ده (الكاشير والباك أوفيس كل واحد سيرفر منفصل على نفس قاعدة البيانات)


def now_s():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# يوم العمل: جدول BusinessDay. اوردرات اليوم في Sales (SDay=CurrentDay). يتغير عند القفل فقط.
def ensure_business_day(c, dt=None):
    c.execute('CREATE TABLE IF NOT EXISTS BusinessDay (ID INTEGER PRIMARY KEY CHECK (ID=1),CurrentDay TEXT NOT NULL,OpenedAt TEXT,ClosedAt TEXT,ClosedBy TEXT,PrevDay TEXT)')
    row = c.execute('SELECT CurrentDay FROM BusinessDay WHERE ID=1').fetchone()
    if row and row[0]:
        return str(row[0])[:10]
    cal = (dt or datetime.now()).strftime('%Y-%m-%d')
    open_day = c.execute('SELECT MIN(SDay) FROM Sales WHERE Closed=0').fetchone()[0]
    if open_day:
        day = str(open_day)[:10]
    else:
        last = max(
            c.execute('SELECT MAX(SDay) FROM Sales WHERE Closed=1').fetchone()[0] or '',
            c.execute('SELECT MAX(day) FROM day_locks').fetchone()[0] or '',
        )
        if last:
            try:
                day = max(cal, (datetime.strptime(str(last)[:10], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'))
            except ValueError:
                day = cal
        else:
            day = cal
    c.execute(
        'INSERT OR REPLACE INTO BusinessDay (ID,CurrentDay,OpenedAt,ClosedAt,ClosedBy,PrevDay) VALUES (1,?,?,NULL,NULL,NULL)',
        (day, now_s()),
    )
    return day

def biz_day(c, dt=None):
    return ensure_business_day(c, dt)

def cur_biz_day():
    """يوم العمل الحالي من جدول BusinessDay (مش من ساعة الجهاز) — للافتراضيات في التقارير."""
    with db() as c:
        return biz_day(c)

def advance_business_day(c, closed_day, user):
    """Advance only when closing; catch up stale business days without opening a past day."""
    ensure_business_day(c)
    now = datetime.now()
    try:
        day_after_closed = (datetime.strptime(str(closed_day)[:10], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        day_after_closed = now.strftime('%Y-%m-%d')
    # Before 06:00, today's calendar date can still belong to the previous overnight shift.
    calendar_target = now.strftime('%Y-%m-%d')
    if now.hour >= 6:
        calendar_target = (now.date() + timedelta(days=1)).strftime('%Y-%m-%d')
    nxt = max(day_after_closed, calendar_target)
    c.execute(
        'UPDATE BusinessDay SET PrevDay=?, CurrentDay=?, OpenedAt=?, ClosedAt=?, ClosedBy=? WHERE ID=1',
        (str(closed_day)[:10], nxt, now_s(), now_s(), (user or {}).get('u') or ''),
    )
    return nxt


# ------------------------------------------------------------------ live events (SSE)
def broadcast(ev, relay=True):
    with SUBS_LOCK:
        for q in list(SUBS):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
    if relay:
        # بنسجّل الحدث في القاعدة عشان السيرفر التاني (كاشير <-> باك أوفيس) يوصّله لأجهزته
        try:
            with db() as c:
                c.execute('INSERT INTO LiveEvents (Src,At,Ev) VALUES (?,?,?)', (SRC, time.time(), json.dumps(ev, ensure_ascii=False)))
        except Exception as e:
            print('LiveEvents insert failed:', e)


def live_relay_loop():
    # بيقرا الأحداث اللي اتسجلت من سيرفر تاني على نفس القاعدة ويبعتها لأجهزة السيرفر ده (SSE)
    last, n = 0, 0
    try:
        with db() as c:
            last = c.execute('SELECT IFNULL(MAX(ID),0) FROM LiveEvents').fetchone()[0]
    except Exception:
        pass
    while True:
        time.sleep(1)
        try:
            with db() as c:
                rows = c.execute('SELECT ID,Src,Ev FROM LiveEvents WHERE ID>? ORDER BY ID', (last,)).fetchall()
                n += 1
                if n % 600 == 0:
                    c.execute('DELETE FROM LiveEvents WHERE At<?', (time.time() - 3600,))
            for rid, src, ev in rows:
                last = rid
                if src != SRC:
                    broadcast(json.loads(ev), relay=False)
        except Exception as e:
            print('LiveEvents relay error:', e)


# ------------------------------------------------------------------ database
# ★★★ [EDIT-MAP] جداول قاعدة البيانات (SQLite) ★★★
T = {
    'Products': 'PID INTEGER PRIMARY KEY,PName TEXT,Cat TEXT,Unit TEXT,Price REAL,Icon TEXT,Code TEXT,Hidden INTEGER DEFAULT 0,Cost REAL DEFAULT 0',
    # v16f: طابعات الشبكة وربطها بالجروبات والأصناف
    'Printers': 'PrinterID INTEGER PRIMARY KEY AUTOINCREMENT,PrinterNo INTEGER UNIQUE NOT NULL,Name TEXT NOT NULL,IP TEXT,Port INTEGER DEFAULT 9100,Type TEXT DEFAULT \"kitchen\",Enabled INTEGER DEFAULT 1,CreatedAt TEXT',
    'PrinterGroups': 'PrinterID INTEGER NOT NULL,GroupName TEXT NOT NULL,PRIMARY KEY(PrinterID,GroupName)',
    'ProductPrinters': 'PID INTEGER NOT NULL,PrinterID INTEGER NOT NULL,PRIMARY KEY(PID,PrinterID)',
    'Stock': 'PID INTEGER PRIMARY KEY,Qty REAL',
    'Users': 'UName TEXT PRIMARY KEY,PHash TEXT,Role TEXT,FullName TEXT',
    'Settings': 'K TEXT PRIMARY KEY,V TEXT',
    'Sales': 'SID TEXT PRIMARY KEY,SNo INTEGER,SDay TEXT,STime TEXT,SHour INTEGER,SType TEXT,Cashier TEXT,CName TEXT,CPhone TEXT,CAddr TEXT,SSub REAL,SDisc REAL,STax REAL,SFee REAL,STotal REAL,Closed INTEGER DEFAULT 0',
    'SaleItems': 'SID TEXT,PID INTEGER,IName TEXT,Cat TEXT,Unit TEXT,Qty REAL,Price REAL',
    'SaleAdjustments': 'AID INTEGER PRIMARY KEY AUTOINCREMENT,SID TEXT,OpCode INTEGER,OpName TEXT,Scope TEXT,PID INTEGER,Amount REAL,Percent REAL,ByUser TEXT,ByEmpNo INTEGER,At TEXT,Note TEXT',
    'RestaurantTables': "TableNo INTEGER PRIMARY KEY,Area TEXT,Status TEXT DEFAULT 'closed',Waiter TEXT,Guests INTEGER DEFAULT 0,UpdatedAt TEXT,UpdatedBy TEXT",
    'TableOrders': 'TableNo INTEGER PRIMARY KEY,Status TEXT,Waiter TEXT,Guests INTEGER,Items TEXT,SentItems TEXT,CreatedAt TEXT,UpdatedAt TEXT,OpenedBy TEXT',
    'EmployeePermissions': 'EmpNo INTEGER,GroupName TEXT,Perm TEXT,Allowed INTEGER DEFAULT 0,UpdatedAt TEXT,UpdatedBy TEXT,PRIMARY KEY(EmpNo,GroupName,Perm)',
    'PermissionAudit': 'AID INTEGER PRIMARY KEY AUTOINCREMENT,EmpNo INTEGER,GroupName TEXT,Action TEXT,Perms TEXT,ByUser TEXT,At TEXT',
    'TableAudit': 'AID INTEGER PRIMARY KEY AUTOINCREMENT,TableNo INTEGER,Action TEXT,OldStatus TEXT,NewStatus TEXT,Waiter TEXT,Guests INTEGER,ByUser TEXT,ByEmpNo INTEGER,At TEXT,Detail TEXT',
    'SalePayments': 'SID TEXT,Method TEXT,Code INTEGER,Amount REAL',
    # v2: سجل الإلغاء والتعديل
    # v3: قفل الأيام بعد التقفيل النهائي
    'day_locks': 'day TEXT PRIMARY KEY,locked_at TEXT,locked_by TEXT',
    'BusinessDay': 'ID INTEGER PRIMARY KEY CHECK (ID=1),CurrentDay TEXT NOT NULL,OpenedAt TEXT,ClosedAt TEXT,ClosedBy TEXT,PrevDay TEXT',
    # v4: مرتجع + طلبيات الشراء
    'Returns': 'RID TEXT PRIMARY KEY,RNo INTEGER,SID TEXT,SNo INTEGER,RDay TEXT,RTime TEXT,Reason TEXT,Cashier TEXT,Manager TEXT,Total REAL,Status TEXT,PaidBy TEXT,PaidAt TEXT,Log TEXT',
    'ReturnItems': 'RID TEXT,PID INTEGER,IName TEXT,Cat TEXT,Unit TEXT,Qty REAL,Price REAL,Refund REAL',
    'Suppliers': 'SupID INTEGER PRIMARY KEY AUTOINCREMENT,SName TEXT,Phone TEXT,Note TEXT',
    'StoreItems': 'Code TEXT PRIMARY KEY,IName TEXT,Unit TEXT,Cat TEXT,Price REAL,SupID INTEGER',
    'PoTemplates': 'TID INTEGER PRIMARY KEY AUTOINCREMENT,TName TEXT,Items TEXT',
    'POrders': 'POID TEXT PRIMARY KEY,PONo TEXT,PODay TEXT,POTime TEXT,Supplier TEXT,Total REAL,Status TEXT,CreatedBy TEXT,SentAt TEXT,Note TEXT,Err TEXT',
    'POItems': 'POID TEXT,Code TEXT,IName TEXT,Unit TEXT,Qty REAL,Price REAL',
    # v6: مخزن الأكواد (بيزيد لما الطلبية تتسجل "تم الاستلام")
    'StoreStock': 'Code TEXT PRIMARY KEY,Qty REAL',
    # v9: جرد الشهر (رصيد أول المدة + وارد الطلبيات - المبيعات = المتوقع، والمقارنة بالعد الفعلي)
    'Stocktake': 'Month TEXT,PID INTEGER,Opening REAL,Actual REAL,PRIMARY KEY(Month,PID)',
    # v10: الريسبي (مكونات الصنف من الخامات) + تسويات الجرد + عدّ الجرد بالكود
    # v11: الدليفري (مناديب / شفتات / تحميل الأوردرات / سجل)
    # v12: نظام الموظفين + توحيد delivery + أعمدة OrderType/Action/State
    'Jobs': 'JobNo INTEGER PRIMARY KEY,JobName TEXT NOT NULL,Active INTEGER DEFAULT 1',
    'Employees': 'EmpNo INTEGER PRIMARY KEY AUTOINCREMENT,Name TEXT NOT NULL,JobNo INTEGER NOT NULL,Phone TEXT,Password TEXT NOT NULL,Active INTEGER DEFAULT 1,CreatedAt TEXT',
    'Permissions': 'JobNo INTEGER NOT NULL,Perm TEXT NOT NULL,Allowed INTEGER DEFAULT 0,PRIMARY KEY(JobNo,Perm)',
    'Deliveries': 'DID INTEGER PRIMARY KEY AUTOINCREMENT,DName TEXT,Phone TEXT,Active INTEGER DEFAULT 1',
    'DeliveryShifts': 'ShiftID INTEGER PRIMARY KEY AUTOINCREMENT,DID INTEGER,StartAt TEXT,StartCash REAL,EndAt TEXT,Status TEXT,Collected REAL,Expected REAL,Actual REAL,Diff REAL,Orders INTEGER,Fees REAL,ClosedBy TEXT,Note TEXT,ShiftDay TEXT',
    'Dispatch': 'DispID INTEGER PRIMARY KEY AUTOINCREMENT,SaleID TEXT,DID INTEGER,ShiftID INTEGER,OutAt INTEGER,BackAt INTEGER,Status TEXT,ByUser TEXT,Refused INTEGER DEFAULT 0,DeliveredAt TEXT',
    'DeliveryLog': 'LID INTEGER PRIMARY KEY AUTOINCREMENT,DID INTEGER,Act TEXT,At TEXT,ByUser TEXT,Detail TEXT',
    # v13 ph4: سجل محاولات الدخول (للقفل بعد 5 محاولات غلط)
    'LoginLog': 'LID INTEGER PRIMARY KEY AUTOINCREMENT,UKey TEXT,UName TEXT,EmpNo INTEGER,At TEXT,AtMs INTEGER,OK INTEGER,IP TEXT,Note TEXT',
    'Recipe': 'PID INTEGER,Code TEXT,Qty REAL,PRIMARY KEY(PID,Code)',
    'StoreAdj': 'Month TEXT,Code TEXT,Kind TEXT,Day TEXT,Qty REAL,PRIMARY KEY(Month,Code,Kind)',
    'StockMoves': 'MoveID INTEGER PRIMARY KEY AUTOINCREMENT,MoveDay TEXT,PID INTEGER,Code TEXT,MoveType TEXT,Qty REAL,BeforeQty REAL,AfterQty REAL,ReferenceType TEXT,ReferenceID TEXT,ByUser TEXT,ByEmpNo INTEGER,CreatedAt TEXT',
    'CloseBatches': 'BatchID INTEGER PRIMARY KEY AUTOINCREMENT,BusinessDay TEXT,ClosedAt TEXT,ClosedBy TEXT,SalesCount INTEGER,SalesTotal REAL,StockMovesCount INTEGER,Status TEXT,Note TEXT',
    'SalesSummary': 'BusinessDay TEXT PRIMARY KEY,GeneratedAt TEXT,SalesCount INTEGER,GrossSales REAL,NetSales REAL,DiscountTotal REAL,ComplimentaryTotal REAL,VoidTotal REAL,CashTotal REAL,VisaTotal REAL,MastercardTotal REAL,MeezaTotal REAL,WalletTotal REAL,TransferTotal REAL,OtherTotal REAL,CashierTotal REAL,DeliveryTotal REAL,PickupTotal REAL,HallTotal REAL',
    'SalesSummaryHist': 'BusinessDay TEXT PRIMARY KEY,GeneratedAt TEXT,SalesCount INTEGER,GrossSales REAL,NetSales REAL,DiscountTotal REAL,ComplimentaryTotal REAL,VoidTotal REAL,CashTotal REAL,VisaTotal REAL,MastercardTotal REAL,MeezaTotal REAL,WalletTotal REAL,TransferTotal REAL,OtherTotal REAL,CashierTotal REAL,DeliveryTotal REAL,PickupTotal REAL,HallTotal REAL',
    'StockCount': 'Month TEXT,Code TEXT,Opening REAL,Actual REAL,PRIMARY KEY(Month,Code)',
    'StocktakeMeta': 'Month TEXT PRIMARY KEY,Status TEXT,ClosedBy TEXT,ClosedAt TEXT',
    'SaleLog': 'LID INTEGER PRIMARY KEY AUTOINCREMENT,SID TEXT,Act TEXT,ByUser TEXT,ByEmpNo INTEGER,Reason TEXT,At TEXT,Detail TEXT',
    # v14: بث الأحداث بين سيرفر الكاشير وسيرفر الباك أوفيس (أوردر جديد يظهر في شاشة الدليفري فوراً)
    'LiveEvents': 'ID INTEGER PRIMARY KEY AUTOINCREMENT,Src TEXT,At REAL,Ev TEXT',
    # v15: شبكة الفروع + طلبيات التحويل بين الفروع (IP/اسم/كود + إرسال واستلام)
    'Branches': 'Code TEXT PRIMARY KEY,BName TEXT,IP TEXT,Port INTEGER DEFAULT 8080,IsLocal INTEGER DEFAULT 0,BKey TEXT,Active INTEGER DEFAULT 1,Note TEXT,CreatedAt TEXT',
    'BranchOrders': "BOID TEXT PRIMARY KEY,BONo TEXT,BODay TEXT,BOTime TEXT,FromCode TEXT,FromName TEXT,ToCode TEXT,ToName TEXT,Status TEXT,CreatedBy TEXT,SentAt TEXT,RecvAt TEXT,RecvBy TEXT,Note TEXT,Err TEXT",
    'BranchOrderItems': 'BOID TEXT,Code TEXT,IName TEXT,Unit TEXT,Qty REAL,RQty REAL',
}
# v2: أعمدة جديدة في Sales (بتتضاف تلقائياً لقاعدة بيانات قديمة)
SALES_NEW = {'Void': 'INTEGER DEFAULT 0', 'VReason': 'TEXT', 'VBy': 'TEXT', 'VAt': 'TEXT',
             'EBy': 'TEXT', 'EAt': 'TEXT', 'ECount': 'INTEGER DEFAULT 0', 'Comment': 'TEXT', 'Returned': 'INTEGER DEFAULT 0', 'Branch': 'TEXT',
             # v12
             'OrderType': 'TEXT', 'OrderAction': 'TEXT', 'OrderState': 'TEXT', 'PayPlace': 'TEXT', 'EmpNo': 'INTEGER',
             'CashierNo': 'INTEGER', 'ManagerNo': 'INTEGER', 'TableNo': 'INTEGER', 'Waiter': 'TEXT', 'Guests': 'INTEGER', 'PayCode': 'INTEGER'}
# أعمدة جديدة في جداول أخرى
RETURNS_NEW = {'CashierNo': 'INTEGER', 'ManagerNo': 'INTEGER', 'TableNo': 'INTEGER', 'Waiter': 'TEXT', 'Guests': 'INTEGER', 'PayCode': 'INTEGER'}
PORDERS_NEW = {'CreatedByNo': 'INTEGER'}
DISPATCH_NEW = {'Refused': 'INTEGER DEFAULT 0', 'DeliveredAt': 'TEXT'}
# v16b: يوم العمل بتاع شفت المندوب (من جدول BusinessDay مش من ساعة الويندوز)
DELSHIFT_NEW = {'ShiftDay': 'TEXT'}
# v15: ترحيل كود طريقة الدفع في السجلات القديمة
SALEPAY_NEW = {'Code': 'INTEGER'}

# v13: ربط تسجيل الدخول والصلاحيات بجدول Employees / Permissions
EMP_NEW = {'UName': 'TEXT', 'PHash': 'TEXT'}
JOB_ROLE = {1: 'admin', 2: 'admin', 3: 'cashier', 4: 'cashier', 5: 'delivery'}   # JobNo -> role (المناديب ملهمش دخول)
ROLE_JOB = {'admin': 2, 'cashier': 3, 'delivery': 5}
ALL_PERMS = ['disc', 'complimentary', 'invoice_options', 'tables', 'del', 'stock', 'rep', 'close', 'printer', 'reprint', 'price', 'void', 'edit', 'return', 'po', 'dlv', 'dlvPay']


PBKDF2_ITERS = 150000


def hp_legacy(u, p):
    """الهاش القديم SHA-256 (للحسابات القديمة وكـ fallback لو pbkdf2 مش متاح)"""
    return hashlib.sha256(('istakoza|' + u.lower() + '|' + p).encode('utf-8')).hexdigest()


def hp(u, p):
    """هاش الباسورد: PBKDF2-HMAC-SHA256 بملح عشوائي. لو الدالة مش متاحة في البايثون بيرجع للـ SHA-256 القديم."""
    try:
        salt = secrets.token_bytes(16)
        h = hashlib.pbkdf2_hmac('sha256', (u.lower() + '|' + p).encode('utf-8'), salt, PBKDF2_ITERS)
        return 'pbkdf2$sha256$%d$%s$%s' % (PBKDF2_ITERS, salt.hex(), h.hex())
    except (AttributeError, ValueError):
        return hp_legacy(u, p)


def vp(u, p, stored):
    """تحقق من الباسورد مقابل الهاش المخزّن (جديد PBKDF2 أو قديم SHA-256)"""
    if not stored:
        return False
    if stored.startswith('pbkdf2$'):
        try:
            _, alg, it, salt, hx = stored.split('$')
            h = hashlib.pbkdf2_hmac(alg, (u.lower() + '|' + p).encode('utf-8'), bytes.fromhex(salt), int(it))
            return secrets.compare_digest(h.hex(), hx)
        except Exception:
            return False
    return secrets.compare_digest(stored, hp_legacy(u, p))


def hp_is_legacy(stored):
    return bool(stored) and not stored.startswith('pbkdf2$')


@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=30)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        for n, d in T.items():
            c.execute('CREATE TABLE IF NOT EXISTS %s (%s)' % (n, d))
        migrate_printers(c)
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(Products)')}
        if 'hidden' not in have:
            c.execute('ALTER TABLE Products ADD COLUMN Hidden INTEGER DEFAULT 0')
        if 'code' not in have:
            c.execute('ALTER TABLE Products ADD COLUMN Code TEXT')
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(Sales)')}
        for col, typ in SALES_NEW.items():
            if col.lower() not in have:
                c.execute('ALTER TABLE Sales ADD COLUMN %s %s' % (col, typ))
        have_pay = {r[1].lower() for r in c.execute('PRAGMA table_info(SalePayments)')}
        for col, typ in SALEPAY_NEW.items():
            if col.lower() not in have_pay:
                c.execute('ALTER TABLE SalePayments ADD COLUMN %s %s' % (col, typ))
        # v6: حالة الطلبية (جاري التنفيذ / تم الاستلام / ملغاة) + الكمية المستلمة فعلاً
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(POrders)')}
        for col, typ in (('Stage', "TEXT DEFAULT 'progress'"), ('RecvAt', 'TEXT'), ('RecvBy', 'TEXT')):
            if col.lower() not in have:
                c.execute('ALTER TABLE POrders ADD COLUMN %s %s' % (col, typ))
        for col, typ in PORDERS_NEW.items():
            if col.lower() not in have:
                c.execute('ALTER TABLE POrders ADD COLUMN %s %s' % (col, typ))
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(POItems)')}
        if 'rqty' not in have:
            c.execute('ALTER TABLE POItems ADD COLUMN RQty REAL')
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(Products)')}
        if 'cost' not in have:
            c.execute('ALTER TABLE Products ADD COLUMN Cost REAL DEFAULT 0')
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(StoreItems)')}
        if 'src' not in have:
            c.execute('ALTER TABLE StoreItems ADD COLUMN Src TEXT')
        # v12 Returns / Dispatch / SaleLog new cols
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(Returns)')}
        for col, typ in RETURNS_NEW.items():
            if col.lower() not in have:
                c.execute('ALTER TABLE Returns ADD COLUMN %s %s' % (col, typ))
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(Dispatch)')}
        for col, typ in DISPATCH_NEW.items():
            if col.lower() not in have:
                try:
                    c.execute('ALTER TABLE Dispatch ADD COLUMN %s %s' % (col, typ))
                except Exception:
                    pass
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(DeliveryShifts)')}
        for col, typ in DELSHIFT_NEW.items():
            if col.lower() not in have:
                try:
                    c.execute('ALTER TABLE DeliveryShifts ADD COLUMN %s %s' % (col, typ))
                except Exception:
                    pass
        have = {r[1].lower() for r in c.execute('PRAGMA table_info(SaleLog)')}
        if 'byempno' not in have:
            try:
                c.execute('ALTER TABLE SaleLog ADD COLUMN ByEmpNo INTEGER')
            except Exception:
                pass
        c.execute('CREATE INDEX IF NOT EXISTS ix_items_sid ON SaleItems(SID)')
        c.execute('CREATE INDEX IF NOT EXISTS ix_sales_day ON Sales(SDay)')
        c.execute('CREATE INDEX IF NOT EXISTS ix_log_sid ON SaleLog(SID)')
        # StockMoves: ترقية الجدول القديم اللي كان أعمدته ناقصة
        sm_cols = {r[1].lower() for r in c.execute('PRAGMA table_info(StockMoves)')}
        if sm_cols and 'beforeqty' not in sm_cols:
            c.execute('ALTER TABLE StockMoves RENAME TO StockMoves_old')
            c.execute('CREATE TABLE StockMoves (MoveID INTEGER PRIMARY KEY AUTOINCREMENT,MoveDay TEXT,PID INTEGER,Code TEXT,MoveType TEXT,Qty REAL,BeforeQty REAL,AfterQty REAL,ReferenceType TEXT,ReferenceID TEXT,ByUser TEXT,ByEmpNo INTEGER,CreatedAt TEXT)')
            try:
                c.execute('INSERT INTO StockMoves (MoveDay,PID,Code,MoveType,Qty,ByUser,CreatedAt) SELECT MoveDay,PID,Code,MoveType,Qty,ByUser,Note FROM StockMoves_old')
            except Exception:
                pass
            c.execute('DROP TABLE IF EXISTS StockMoves_old')
        # جداول Hist: لا نحذف الأرشيف عند اختلاف الأعمدة. نحفظ نسخة كاملة ثم ننقل الأعمدة المشتركة.
        for src in ('Sales','SaleItems','SalePayments','SaleAdjustments','SaleLog','Returns','ReturnItems','StockMoves','TableAudit','POrders','POItems'):
            hist = src + 'Hist'
            exists = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (hist,)).fetchone()
            src_cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % src)]
            if not exists:
                c.execute('CREATE TABLE "%s" AS SELECT * FROM "%s" WHERE 0' % (hist, src))
                continue
            hist_cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % hist)]
            if src_cols != hist_cols:
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = '__migration_backup_%s_%s' % (hist, stamp)
                suffix = 1
                while c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (backup,)).fetchone():
                    suffix += 1
                    backup = '__migration_backup_%s_%s_%s' % (hist, stamp, suffix)
                c.execute('ALTER TABLE "%s" RENAME TO "%s"' % (hist, backup))
                c.execute('CREATE TABLE "%s" AS SELECT * FROM "%s" WHERE 0' % (hist, src))
                common = [col for col in src_cols if col in hist_cols]
                if common:
                    cols = ','.join('"%s"' % col for col in common)
                    c.execute('INSERT INTO "%s" (%s) SELECT %s FROM "%s"' % (hist, cols, cols, backup))
                print('Hist migration preserved original table as', backup, 'and copied', len(common), 'common columns')
        for v in ('SalesAll','SaleItemsAll','SalePaymentsAll','SaleAdjustmentsAll'):
            c.execute('DROP VIEW IF EXISTS %s' % v)
        c.execute('CREATE VIEW SalesAll AS SELECT * FROM Sales UNION ALL SELECT * FROM SalesHist')
        c.execute('CREATE VIEW SaleItemsAll AS SELECT * FROM SaleItems UNION ALL SELECT * FROM SaleItemsHist')
        c.execute('CREATE VIEW SalePaymentsAll AS SELECT * FROM SalePayments UNION ALL SELECT * FROM SalePaymentsHist')
        c.execute('CREATE VIEW SaleAdjustmentsAll AS SELECT * FROM SaleAdjustments UNION ALL SELECT * FROM SaleAdjustmentsHist')

        c.execute('CREATE INDEX IF NOT EXISTS ix_loginlog_k ON LoginLog(UKey,AtMs)')
        c.execute('DELETE FROM LoginLog WHERE AtMs<?', (int(time.time() * 1000) - 90 * 86400 * 1000,))
        # أيام اتقفلت قبل التحديث ده: نسجّلها في day_locks
        c.execute("INSERT OR IGNORE INTO day_locks (day,locked_at,locked_by) SELECT DISTINCT SDay,?,'migration' FROM Sales WHERE Closed=1", (now_s(),))
        ensure_business_day(c)
        if c.execute('SELECT COUNT(*) FROM Users').fetchone()[0] == 0:
            c.execute('INSERT INTO Users VALUES (?,?,?,?)', ('admin', hp('admin', 'admin'), 'admin', 'المدير'))
        # ===== v12 migration: Jobs seed + Users/Deliveries → Employees + Deliveries→Deliveries =====
        if c.execute('SELECT COUNT(*) FROM Jobs').fetchone()[0] == 0:
            c.executemany('INSERT INTO Jobs (JobNo,JobName,Active) VALUES (?,?,1)', [
                (1, 'IT'), (2, 'manager'), (3, 'cashier'), (4, 'waiter'), (5, 'delivery')])
        # migrate old Deliveries table if exists
        old_tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'Riders' in old_tables and c.execute('SELECT COUNT(*) FROM Deliveries').fetchone()[0] == 0:
            try:
                c.execute('INSERT INTO Deliveries (DID,DName,Phone,Active) SELECT RID,RName,Phone,Active FROM Riders')
            except Exception:
                pass
        if 'RiderShifts' in old_tables and c.execute('SELECT COUNT(*) FROM DeliveryShifts').fetchone()[0] == 0:
            try:
                c.execute('INSERT INTO DeliveryShifts (ShiftID,DID,StartAt,StartCash,EndAt,Status,Collected,Expected,Actual,Diff,Orders,Fees,ClosedBy,Note) SELECT ShiftID,RID,StartAt,StartCash,EndAt,Status,Collected,Expected,Actual,Diff,Orders,Fees,ClosedBy,Note FROM RiderShifts')
            except Exception:
                pass
        if 'RiderLog' in old_tables and c.execute('SELECT COUNT(*) FROM DeliveryLog').fetchone()[0] == 0:
            try:
                c.execute('INSERT INTO DeliveryLog (LID,DID,Act,At,ByUser,Detail) SELECT LID,RID,Act,At,ByUser,Detail FROM RiderLog')
            except Exception:
                pass
        # migrate Users → Employees
        if c.execute('SELECT COUNT(*) FROM Employees').fetchone()[0] == 0:
            for u, role, full in c.execute('SELECT UName,Role,FullName FROM Users'):
                jn = 2 if role == 'admin' else (3 if role == 'cashier' else 1)
                try:
                    c.execute('INSERT INTO Employees (Name,JobNo,Phone,Password,Active,CreatedAt) VALUES (?,?,?,?,1,?)',
                              (full or u, jn, '', u, now_s()))
                except Exception:
                    pass
            for r in c.execute('SELECT DName,Phone FROM Deliveries'):
                try:
                    c.execute('INSERT INTO Employees (Name,JobNo,Phone,Password,Active,CreatedAt) VALUES (?,?,?,?,1,?)',
                              (r[0], 5, r[1] or '', '1234', now_s()))
                except Exception:
                    pass

        # ===== v13: Employees/Permissions هما المصدر الوحيد للدخول والصلاحيات =====
        wire_employees(c)
        seed_permissions(c)
        # 40 طاولة ثابتة: 1-20 داخلي، 21-40 خارجي
        for n in range(1, 41):
            c.execute('INSERT OR IGNORE INTO RestaurantTables (TableNo,Area,Status) VALUES (?,?,?)', (n, 'داخلي' if n <= 20 else 'خارجي', 'closed'))

        # old sales defaults
        c.execute("UPDATE Sales SET OrderType='C', OrderAction='', OrderState='F', PayPlace='C' WHERE OrderType IS NULL OR OrderType=''")


def migrate_printers(c):
    """Create printer routing rows and migrate legacy cfg.printers once."""
    if c.execute('SELECT COUNT(*) FROM Printers').fetchone()[0]:
        return
    row = c.execute("SELECT V FROM Settings WHERE K='cfg'").fetchone()
    try:
        cfg = json.loads(row[0]) if row else {}
    except Exception:
        cfg = {}
    legacy = cfg.get('printers') or []
    used = set()
    for pos, pr in enumerate(legacy, 1):
        try:
            no = int(pr.get('id') or pos)
        except Exception:
            no = pos
        while no in used:
            no += 1
        used.add(no)
        c.execute('INSERT INTO Printers (PrinterNo,Name,IP,Port,Type,Enabled,CreatedAt) VALUES (?,?,?,?,?,?,?)',
                  (no, str(pr.get('name') or ('طابعة %d' % no)), str(pr.get('ip') or ''), int(pr.get('port') or 9100),
                   'kitchen' if pr.get('type') == 'kitchen' else 'customer', 1 if pr.get('on', 1) else 0, now_s()))
        pid = c.execute('SELECT PrinterID FROM Printers WHERE PrinterNo=?', (no,)).fetchone()[0]
        for g in pr.get('cats') or []:
            c.execute('INSERT OR IGNORE INTO PrinterGroups (PrinterID,GroupName) VALUES (?,?)', (pid, str(g)))

def printer_rows(c):
    rows = c.execute('SELECT PrinterID,PrinterNo,Name,IP,Port,Type,Enabled FROM Printers ORDER BY PrinterNo,PrinterID').fetchall()
    groups = {int(pid): [str(g) for (g,) in c.execute('SELECT GroupName FROM PrinterGroups WHERE PrinterID=? ORDER BY GroupName', (pid,)).fetchall()] for pid, *_ in rows}
    products = {}
    for pid, prno in c.execute('SELECT pp.PID,p.PrinterNo FROM ProductPrinters pp JOIN Printers p ON p.PrinterID=pp.PrinterID ORDER BY p.PrinterNo'):
        products.setdefault(int(pid), []).append(int(prno))
    out = []
    for pid, no, name, ip, port, typ, enabled in rows:
        out.append(dict(id=int(pid), no=int(no), name=name or '', ip=ip or '', port=int(port or 9100), type=typ or 'kitchen', enabled=bool(enabled), on=bool(enabled), groups=groups.get(int(pid), [])))
    return out, products

def printers_get():
    with db() as c:
        printers, products = printer_rows(c)
        cats = [str(x[0]) for x in c.execute("SELECT DISTINCT Cat FROM Products WHERE Cat IS NOT NULL AND Cat<>'' ORDER BY Cat").fetchall()]
    return dict(printers=printers, products=products, groups=cats)

def printer_save(d):
    with LOCK, db() as c:
        if d.get('del'):
            pid = int(d.get('id') or 0)
            c.execute('DELETE FROM PrinterGroups WHERE PrinterID=?', (pid,))
            c.execute('DELETE FROM ProductPrinters WHERE PrinterID=?', (pid,))
            c.execute('DELETE FROM Printers WHERE PrinterID=?', (pid,))
            return {'ok': 1}
        name = str(d.get('name') or '').strip()
        if not name:
            raise Exception('اكتب اسم الطابعة')
        ip = str(d.get('ip') or '').strip()
        port = int(d.get('port') or 9100)
        if port < 1 or port > 65535:
            raise Exception('البورت غير صحيح')
        typ = 'customer' if d.get('type') == 'customer' else 'kitchen'
        enabled = 1 if d.get('enabled', d.get('on', 1)) else 0
        pid = int(d.get('id') or 0)
        if pid and c.execute('SELECT 1 FROM Printers WHERE PrinterID=?', (pid,)).fetchone():
            c.execute('UPDATE Printers SET Name=?,IP=?,Port=?,Type=?,Enabled=? WHERE PrinterID=?', (name,ip,port,typ,enabled,pid))
        else:
            no = (c.execute('SELECT COALESCE(MAX(PrinterNo),0)+1 FROM Printers').fetchone()[0] or 1)
            c.execute('INSERT INTO Printers (PrinterNo,Name,IP,Port,Type,Enabled,CreatedAt) VALUES (?,?,?,?,?,?,?)', (no,name,ip,port,typ,enabled,now_s()))
            pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        if 'groups' in d:
            c.execute('DELETE FROM PrinterGroups WHERE PrinterID=?', (pid,))
            c.executemany('INSERT OR IGNORE INTO PrinterGroups (PrinterID,GroupName) VALUES (?,?)', [(pid, str(g)) for g in (d.get('groups') or []) if str(g).strip()])
        return {'ok': 1, 'id': int(pid), 'no': int(c.execute('SELECT PrinterNo FROM Printers WHERE PrinterID=?',(pid,)).fetchone()[0])}

def printer_routing_save(d):
    with LOCK, db() as c:
        group_map = d.get('groups') or {}
        c.execute('DELETE FROM PrinterGroups')
        for no, names in group_map.items():
            row = c.execute('SELECT PrinterID FROM Printers WHERE PrinterNo=?', (int(no),)).fetchone()
            if row:
                c.executemany('INSERT OR IGNORE INTO PrinterGroups (PrinterID,GroupName) VALUES (?,?)', [(row[0], str(g)) for g in names or []])
        for pid, nos in (d.get('products') or {}).items():
            pid = int(pid)
            c.execute('DELETE FROM ProductPrinters WHERE PID=?', (pid,))
            for no in nos or []:
                row = c.execute('SELECT PrinterID FROM Printers WHERE PrinterNo=?', (int(no),)).fetchone()
                if row: c.execute('INSERT OR IGNORE INTO ProductPrinters (PID,PrinterID) VALUES (?,?)', (pid,row[0]))
    return {'ok': 1}


def get_cfg(c):
    row = c.execute("SELECT V FROM Settings WHERE K='cfg'").fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except Exception:
        return {}


# ★★★ [EDIT-MAP] الصلاحيات الافتراضية للكاشير ★★★
PERM_DEF = {'dlv': 1, 'return': 1, 'reprint': 1, 'price': 1, 'del': 1, 'disc': 1, 'invoice_options': 1, 'tables': 1, 'complimentary': 0}


def wire_employees(c):
    """يضيف UName/PHash لـ Employees وينقل Users القديم مرة واحدة (بنفس هاش الباسورد)"""
    have = {r[1].lower() for r in c.execute('PRAGMA table_info(Employees)')}
    for col, typ in EMP_NEW.items():
        if col.lower() not in have:
            c.execute('ALTER TABLE Employees ADD COLUMN %s %s' % (col, typ))
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_emp_uname ON Employees(UName)')
    if c.execute('PRAGMA user_version').fetchone()[0] < 13:   # مرة واحدة بس، عشان اللي يتحذف ما يرجعش
        for u, ph, role, full in c.execute('SELECT UName,PHash,Role,FullName FROM Users').fetchall():
            jn = ROLE_JOB.get(role, 3)
            if c.execute('SELECT 1 FROM Employees WHERE UName=?', (u,)).fetchone():
                continue
            r = c.execute('SELECT EmpNo FROM Employees WHERE UName IS NULL AND (Name=? OR Name=?) ORDER BY EmpNo LIMIT 1',
                          (full or u, u)).fetchone()
            if r:
                c.execute("UPDATE Employees SET UName=?,PHash=?,JobNo=?,Password='' WHERE EmpNo=?", (u, ph, jn, r[0]))
            else:
                c.execute("INSERT INTO Employees (Name,JobNo,Phone,Password,Active,CreatedAt,UName,PHash) VALUES (?,?,?,?,1,?,?,?)",
                          (full or u, jn, '', '', now_s(), u, ph))
        c.execute('PRAGMA user_version=13')
    # لازم يفضل فيه مدير واحد على الأقل يقدر يدخل
    if not c.execute('SELECT 1 FROM Employees WHERE JobNo IN (1,2) AND Active=1 AND UName IS NOT NULL AND PHash IS NOT NULL').fetchone():
        c.execute("DELETE FROM Employees WHERE UName='admin'")
        c.execute("INSERT INTO Employees (Name,JobNo,Phone,Password,Active,CreatedAt,UName,PHash) VALUES (?,?,?,?,1,?,?,?)",
                  ('المدير', 2, '', '', now_s(), 'admin', hp('admin', 'admin')))


def seed_permissions(c):
    """بيملا Permissions لأول مرة (INSERT OR IGNORE - مش بيمسح تعديلات المدير). الكاشير بياخد القيم الحالية من cfg.perm"""
    cfgp = get_cfg(c).get('perm') or {}
    for (jn,) in c.execute('SELECT JobNo FROM Jobs').fetchall():
        for k in ALL_PERMS:
            if jn in (1, 2):
                v = 1
            elif jn == 3:
                v = 1 if cfgp.get(k, PERM_DEF.get(k, 0)) else 0
            else:
                v = 0
            c.execute('INSERT OR IGNORE INTO Permissions (JobNo,Perm,Allowed) VALUES (?,?,?)', (jn, k, v))


def emp_perms(job, emp=None):
    with db() as c:
        rows = dict(c.execute('SELECT Perm,Allowed FROM Permissions WHERE JobNo=?', (job,)).fetchall())
        if emp:
            rows.update({r[0]:r[1] for r in c.execute("SELECT Perm,Allowed FROM EmployeePermissions WHERE EmpNo=? AND GroupName IN ('front','back')", (emp,)).fetchall()})
    return {k: int(rows.get(k, 0) or 0) for k in ALL_PERMS}


def can(user, perm):
    if user['role'] == 'admin': return True
    with db() as c:
        if user.get('emp'):
            r=c.execute("SELECT Allowed FROM EmployeePermissions WHERE EmpNo=? AND Perm=? ORDER BY CASE WHEN GroupName='front' THEN 0 ELSE 1 END LIMIT 1",(user['emp'],perm)).fetchone()
            if r is not None: return bool(r[0])
        r = c.execute('SELECT Allowed FROM Permissions WHERE JobNo=? AND Perm=?', (user.get('job'), perm)).fetchone()
    return bool(r[0]) if r else False

FRONT_PERMS=['invoice_options','disc','complimentary','tables','del','reprint','price','void','edit','return','cash','visa','mastercard','meeza','wallet','transfer','other','send_kitchen']
BACK_PERMS=['tables_manage','stock','rep','close','printer','po','dlv','dlvPay','users_manage','permissions_manage','backup','payment_reports','adjustment_reports']
def permission_rows(emp):
    with db() as c:
        return {'front':dict(c.execute("SELECT Perm,Allowed FROM EmployeePermissions WHERE EmpNo=? AND GroupName='front'",(emp,)).fetchall()),'back':dict(c.execute("SELECT Perm,Allowed FROM EmployeePermissions WHERE EmpNo=? AND GroupName='back'",(emp,)).fetchall())}
def save_employee_permissions(d,user):
    emp=int(d.get('emp')); group=str(d.get('group') or 'front'); allowed=set(d.get('allowed') or [])
    if group not in ('front','back'): raise Exception('مجموعة صلاحيات غير صحيحة')
    valid=set(FRONT_PERMS if group=='front' else BACK_PERMS); allowed &= valid
    with LOCK, db() as c:
        if not c.execute('SELECT 1 FROM Employees WHERE EmpNo=? AND Active=1',(emp,)).fetchone(): raise Exception('الموظف غير موجود')
        for perm in valid:
            c.execute('INSERT OR REPLACE INTO EmployeePermissions (EmpNo,GroupName,Perm,Allowed,UpdatedAt,UpdatedBy) VALUES (?,?,?,?,?,?)',(emp,group,perm,1 if perm in allowed else 0,now_s(),user.get('u','')))
        c.execute('INSERT INTO PermissionAudit (EmpNo,GroupName,Action,Perms,ByUser,At) VALUES (?,?,?,?,?,?)',(emp,group,'save',json.dumps(sorted(allowed),ensure_ascii=False),user.get('u',''),now_s()))
    return permission_rows(emp)
def copy_employee_permissions(d,user):
    src,dst=int(d.get('src')),int(d.get('dst'))
    if src==dst: raise Exception('اختر موظفين مختلفين')
    with LOCK, db() as c:
        if not c.execute('SELECT 1 FROM Employees WHERE EmpNo=? AND Active=1',(src,)).fetchone() or not c.execute('SELECT 1 FROM Employees WHERE EmpNo=? AND Active=1',(dst,)).fetchone(): raise Exception('الموظف غير موجود')
        for group in ('front','back'):
            rows=c.execute('SELECT Perm,Allowed FROM EmployeePermissions WHERE EmpNo=? AND GroupName=?',(src,group)).fetchall()
            for perm,ok in rows: c.execute('INSERT OR REPLACE INTO EmployeePermissions (EmpNo,GroupName,Perm,Allowed,UpdatedAt,UpdatedBy) VALUES (?,?,?,?,?,?)',(dst,group,perm,ok,now_s(),user.get('u','')))
        c.execute('INSERT INTO PermissionAudit (EmpNo,GroupName,Action,Perms,ByUser,At) VALUES (?,?,?,?,?,?)',(dst,'front+back','copy_from_'+str(src),json.dumps(permission_rows(src),ensure_ascii=False),user.get('u',''),now_s()))
    return permission_rows(dst)


SALE_COLS = ('SID,SNo,SDay,STime,SHour,SType,Cashier,CName,CPhone,CAddr,SSub,SDisc,STax,SFee,STotal,Closed,'
             'Void,VReason,VBy,VAt,EBy,EAt,ECount,Comment,Returned,Branch,'
             'OrderType,OrderAction,OrderState,PayPlace,EmpNo,TableNo,Waiter,Guests,PayCode')


def row_to_sale(r, items, payments=None):
    return dict(id=r[0], no=r[1], day=r[2], time=r[3], h=r[4], type=r[5], by=r[6],
                sub=r[10], disc=r[11], tax=r[12], fee=r[13], total=r[14], items=items,
                cust=dict(name=r[7], phone=r[8], addr=r[9]) if r[5] in ('delivery', 'pickup') else None,
                void=int(r[16] or 0), vreason=r[17] or '', vby=r[18] or '', vat=r[19] or '',
                eby=r[20] or '', eat=r[21] or '', ecount=int(r[22] or 0), comment=r[23] or '', returned=int(r[24] or 0), branch=r[25] or '',
                orderType=r[26] or '', orderAction=r[27] or '', orderState=r[28] or '', payPlace=r[29] or '', empNo=r[30],
                tableNo=r[31], waiter=r[32] or '', guests=int(r[33] or 0), payCode=int(r[34] or 1),
                payments=payments or ([dict(method='cash', amount=round(float(r[14] or 0), 2))] if float(r[14] or 0) else []))


# ★★★ [EDIT-MAP] اللي بيتبعت للصفحة (أصناف، فواتير، ريسبي...) ★★★
def get_state():
    with db() as c:
        pr_rows, product_printers = printer_rows(c)
        prods = [dict(id=r[0], name=r[1], cat=r[2], unit=r[3], price=r[4], icon=r[5], code=r[6], hidden=bool(r[7]), cost=r[8] or 0,
                      printer_ids=product_printers.get(int(r[0]), []))
                 for r in c.execute('SELECT PID,PName,Cat,Unit,Price,Icon,Code,Hidden,Cost FROM Products ORDER BY PID')]
        items = {}
        for r in c.execute('SELECT SID,PID,IName,Cat,Unit,Qty,Price FROM SaleItemsAll'):
            items.setdefault(r[0], []).append(dict(id=r[1], name=r[2], cat=r[3], unit=r[4], qty=r[5], price=r[6]))
        locked = {r[0] for r in c.execute('SELECT day FROM day_locks')}
        payments = {}
        for sid, method, code, amount in c.execute('SELECT SID,Method,Code,Amount FROM SalePaymentsAll ORDER BY rowid'):
            payments.setdefault(sid, []).append(dict(method=method, code=int(code or 1), amount=float(amount or 0)))
        S, A = [], []
        for r in c.execute('SELECT %s FROM SalesAll ORDER BY SDay,SID' % SALE_COLS):
            sl = row_to_sale(r, items.get(r[0], []), payments.get(r[0]))
            sl['locked'] = r[2] in locked or bool(r[15])
            (A if r[15] else S).append(sl)
        stock = {str(r[0]): r[1] for r in c.execute('SELECT PID,Qty FROM Stock')}
        sync_fish(c)
        seed_prices(c)
        rc, cs, ad = mat_move(c, '0000-00-00', '9999-99-99', closed_only=True)
        mbase = {}
        for (code,) in c.execute('SELECT Code FROM StoreItems').fetchall():
            k = str(code)
            mbase[k] = round(rc.get(k, 0) - cs.get(k, 0) + ad.get(k, 0), 3)
        recipes = {}
        for pid, code, q in c.execute('SELECT PID,Code,Qty FROM Recipe ORDER BY rowid'):
            recipes.setdefault(str(pid), []).append(dict(code=str(code), qty=q))
        cfg = get_cfg(c)
        cfg['printers'] = pr_rows
        return dict(products=prods, sales=S, archive=A, stock=stock, cfg=cfg, printers=pr_rows, ver=VERSION, businessDay=ensure_business_day(c), returns=list_returns(c),
                    recipes=recipes, mbase=mbase)


# ★★★ [EDIT-MAP] حفظ الأصناف والأسعار والإعدادات (فحص الصلاحيات هنا) ★★★
def save_state(d, role):
    with LOCK, db() as c:
        if role == 'admin':
            c.execute('DELETE FROM Products')
            c.executemany('INSERT INTO Products (PID,PName,Cat,Unit,Price,Icon,Code,Hidden,Cost) VALUES (?,?,?,?,?,?,?,?,?)',
                          [(int(p['id']), p['name'], p.get('cat', ''), p.get('unit', 'pc'), float(p.get('price') or 0),
                            p.get('icon', ''), str(p.get('code') or ''), 1 if p.get('hidden') else 0, float(p.get('cost') or 0))
                           for p in d.get('products', [])])
            c.execute('DELETE FROM Stock')
            c.executemany('INSERT INTO Stock VALUES (?,?)', [(int(k), float(v)) for k, v in (d.get('stock') or {}).items()])
            c.execute("DELETE FROM Settings WHERE K='cfg'")  # FIX: كان بيمسح branchKey و boSeq كمان
            c.execute('INSERT OR REPLACE INTO Settings VALUES (?,?)', ('cfg', json.dumps(d.get('cfg') or {}, ensure_ascii=False)))
            # شاشة "صلاحيات الكاشير" بتكتب في cfg.perm → بننسخها لجدول Permissions (وظيفة الكاشير = 3)
            for k, v in ((d.get('cfg') or {}).get('perm') or {}).items():
                if k in ALL_PERMS:
                    c.execute('INSERT OR REPLACE INTO Permissions (JobNo,Perm,Allowed) VALUES (3,?,?)', (k, 1 if v else 0))
        elif role != 'none':
            for p in d.get('products', []):
                c.execute('UPDATE Products SET Price=? WHERE PID=?', (float(p.get('price') or 0), int(p['id'])))


PAYMENT_CODES = {'cash':1,'visa':2,'mastercard':3,'meeza':4,'wallet':5,'transfer':6,'other':7,'complimentary':8}

def ins_sale(c, s, closed=0):
    cu = s.get('cust') or {}
    stype = s.get('type', 'cash')
    # OrderType: C=كاشير V=دليفري A=استلام فرع D=صالة
    otype = 'A' if stype == 'pickup' else (s.get('orderType') or ('V' if stype == 'delivery' else 'C'))
    if stype == 'pickup' and s.get('fee'):
        # استلام فرع: مفيش رسوم توصيل أبداً (حتى لو الصفحة بعتتها)
        s['total'] = round(float(s['total']) - float(s['fee']), 2)
        s['fee'] = 0
    c.execute('INSERT INTO Sales (SID,SNo,SDay,STime,SHour,SType,Cashier,CName,CPhone,CAddr,SSub,SDisc,STax,SFee,STotal,Closed,'
              'Void,VReason,VBy,VAt,EBy,EAt,ECount,Comment,Returned,Branch,OrderType,OrderAction,OrderState,PayPlace,EmpNo,TableNo,Waiter,Guests,PayCode) '
              'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (s['id'], s['no'], s['day'], s['time'], s.get('h', 0), stype, s.get('by', ''),
               cu.get('name', ''), cu.get('phone', ''), cu.get('addr', ''), s['sub'], s['disc'], s.get('tax', 0),
               s.get('fee', 0), s['total'], closed, 1 if s.get('void') else 0, s.get('vreason', ''), s.get('vby', ''),
               s.get('vat', ''), s.get('eby', ''), s.get('eat', ''), s.get('ecount', 0), s.get('comment', ''), s.get('returned', 0), s.get('branch', ''),
               otype, s.get('orderAction') or '', s.get('orderState') or '', otype, s.get('empNo'), s.get('tableNo'), s.get('waiter') or '', int(s.get('guests') or 0), int(s.get('payCode') or 1)))
    put_items(c, s['id'], s['items'])
    for it in s['items']:
        inventory_adjust(c, it, -float(it.get('qty') or 0), 'sale', s, 'sale', s['id'], s.get('day'))
    payments = s.get('payments') or [dict(method='cash', code=1, amount=float(s.get('total') or 0))]
    pay_rows=[(s['id'], str(x.get('method') or 'cash'), int(x.get('code') or PAYMENT_CODES.get(str(x.get('method') or 'cash'),1)), round(float(x.get('amount') or 0), 2)) for x in payments if float(x.get('amount') or 0) > 0]
    if int((s.get('adjustment') or {}).get('opCode') or 0)==20 and not any(x[1]=='complimentary' for x in pay_rows):
        pay_rows.append((s['id'],'complimentary',PAYMENT_CODES['complimentary'],0.0))
    c.executemany('INSERT INTO SalePayments (SID,Method,Code,Amount) VALUES (?,?,?,?)', pay_rows)
    adj=s.get('adjustment') or {}
    if adj.get('opCode'):
        c.execute('INSERT INTO SaleAdjustments (SID,OpCode,OpName,Scope,PID,Amount,Percent,ByUser,ByEmpNo,At,Note) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (s['id'], int(adj['opCode']), str(adj.get('opName') or ''), str(adj.get('scope') or 'invoice'), adj.get('pid'), float(adj.get('amount') or 0), float(adj.get('percent') or 0), s.get('by') or '', s.get('empNo'), now_s(), str(adj.get('note') or '')))


def put_items(c, sid, items):
    c.executemany('INSERT INTO SaleItems VALUES (?,?,?,?,?,?,?)',
                  [(sid, i['id'], i['name'], i.get('cat') or '', i['unit'], i['qty'], i['price']) for i in items])


def validate_sale(c, d, user):
    """يتحقق السيرفر من بيانات الفاتورة ويستبدل بيانات الصنف القادمة من المتصفح بالمصدر المعتمد."""
    raw_items = d.get('items') or []
    if not raw_items:
        raise Forbidden('لا يمكن حفظ فاتورة فارغة')
    items, subtotal = [], 0.0
    for raw in raw_items:
        try:
            pid, qty = int(raw.get('id')), float(raw.get('qty'))
        except (TypeError, ValueError):
            raise Forbidden('بيانات الصنف غير صحيحة')
        if qty <= 0 or qty > 100000:
            raise Forbidden('الكمية غير صحيحة')
        product = c.execute('SELECT PID,PName,Cat,Unit,Price,Icon,Code,Hidden FROM Products WHERE PID=?', (pid,)).fetchone()
        if not product or int(product[7] or 0):
            raise Forbidden('الصنف غير متاح')
        price = round(float(product[4] or 0), 2)
        line = dict(id=product[0], name=product[1], cat=product[2] or '', unit=product[3] or 'pc',
                    qty=qty, price=price, icon=product[5] or '', code=product[6] or '')
        items.append(line)
        subtotal += qty * price
    subtotal = round(subtotal, 2)
    adj = d.get('adjustment') or {}
    # توافق مع الفواتير القديمة التي كانت ترسل قيمة disc مباشرة بدل adjustment.
    # لا نثق بالقيمة بلا حدود: السيرفر يعيد حصرها في قيمة الفاتورة ويتطلب صلاحية الخصم.
    legacy_disc = float(d.get('disc') or 0)
    if not adj.get('opCode') and legacy_disc > 0:
        if not can(user, 'disc'):
            raise Forbidden('عملية الخصم غير مسموحة')
        legacy_disc = min(subtotal, legacy_disc)
        adj = {'opCode': 10, 'scope': 'invoice', 'amount': legacy_disc, 'percent': 0}
    op = int(adj.get('opCode') or 0)
    discount = 0.0
    if op:
        required = 'complimentary' if op == 20 else 'disc' if op == 10 else None
        if not required or not can(user, required):
            raise Forbidden('عملية الخصم غير مسموحة')
        scope = str(adj.get('scope') or 'invoice')
        base = subtotal
        selected_pids = adj.get('pids') or ([adj.get('pid')] if adj.get('pid') else [])
        try:
            selected_pids = {int(pid) for pid in selected_pids if pid is not None}
        except (TypeError, ValueError):
            raise Forbidden('الأصناف المحددة للخصم غير صحيحة')
        if scope == 'item':
            base = round(sum(i['qty'] * i['price'] for i in items if i['id'] in selected_pids), 2)
            if not base:
                raise Forbidden('الصنف أو الأصناف المحددة للخصم غير موجودة')
        if op == 20:
            discount = base
        else:
            mode_percent = float(adj.get('percent') or 0)
            amount = float(adj.get('amount') or 0)
            if mode_percent < 0 or mode_percent > 100 or amount < 0:
                raise Forbidden('قيمة الخصم غير صحيحة')
            discount = base * mode_percent / 100 if mode_percent else amount
            discount = min(base, discount)
        selected_note = ('الأصناف: ' + ','.join(str(pid) for pid in sorted(selected_pids))) if scope == 'item' else 'الفاتورة كاملة'
        d['adjustment'] = dict(opCode=op, opName='Complimentary' if op == 20 else 'Discount', scope=scope,
                               pid=next(iter(selected_pids), None), pids=sorted(selected_pids), amount=round(discount, 2),
                               percent=float(adj.get('percent') or 0), note=selected_note)
    else:
        d['adjustment'] = {}
    tax, fee = round(float(d.get('tax') or 0), 2), round(float(d.get('fee') or 0), 2)
    if tax < 0 or fee < 0:
        raise Forbidden('الضريبة أو الرسوم غير صحيحة')
    total = round(max(0.0, subtotal - discount + tax + fee), 2)
    payments = d.get('payments') or []
    normalized = []
    for payment in payments:
        method = str(payment.get('method') or 'cash')
        amount = round(float(payment.get('amount') or 0), 2)
        if method not in PAYMENT_CODES or amount < 0:
            raise Forbidden('طريقة الدفع أو قيمتها غير صحيحة')
        if amount > 0:
            normalized.append(dict(method=method, code=PAYMENT_CODES[method], amount=amount))
    if not normalized and total > 0:
        normalized = [dict(method='cash', code=1, amount=total)]
    if round(sum(x['amount'] for x in normalized), 2) != total:
        raise Forbidden('إجمالي طرق الدفع لا يساوي إجمالي الفاتورة')
    table_no = d.get('tableNo')
    if table_no:
        row = c.execute('SELECT Status FROM RestaurantTables WHERE TableNo=?', (int(table_no),)).fetchone()
        if not row or row[0] != 'guest':
            raise Forbidden('الطاولة ليست مفتوحة')
    d.update(items=items, sub=subtotal, disc=round(discount, 2), total=total, payments=normalized)


# ★★★ [EDIT-MAP] حفظ الفاتورة: الترقيم + الوقت من السيرفر + خصم الخامات ★★★
def add_sale(d, user):
    # الترقيم بيتم هنا جوه السيرفر (داخل قفل) عشان جهازين مايطلعوش نفس رقم الفاتورة
    with LOCK, db() as c:
        c.execute('BEGIN IMMEDIATE')  # FIX: قفل الكتابة على مستوى الداتا بيز — السيرفرين (كاشير/باك أوفيس) مايطلعوش نفس رقم الفاتورة
        adj=d.get('adjustment') or {}
        if adj.get('opCode'):
            required='complimentary' if int(adj.get('opCode'))==20 else 'disc'
            if not can(user, required):
                raise Forbidden('لا توجد صلاحية لتنفيذ '+('Complimentary' if required=='complimentary' else 'Discount'))
        ex = c.execute('SELECT SNo FROM SalesAll WHERE SID=?', (d['id'],)).fetchone()
        if ex:
            return {'ok': 1, 'no': ex[0], 'id': d['id'], 'dup': 1}
        validate_sale(c, d, user)
        d['no'] = (c.execute('SELECT MAX(SNo) FROM Sales WHERE Closed=0').fetchone()[0] or 0) + 1
        _n = datetime.now()
        d['day'], d['time'], d['h'] = biz_day(c, _n), _n.strftime('%H:%M:%S'), _n.hour
        d['by'] = user['u']
        d['empNo'] = user.get('emp')
        d['branch'] = str(get_cfg(c).get('branchCode') or '')
        ins_sale(c, d)
    return {'ok': 1, 'no': d['no'], 'id': d['id']}


# ------------------------------------------------------------------ void / edit (v2) + قفل الأيام (v3)
def stock_move(c, pid, qty, kind, user, ref_type='', ref_id='', day=None):
    before=c.execute('SELECT Qty FROM Stock WHERE PID=?',(pid,)).fetchone()
    b=float(before[0] or 0) if before else 0; a=b+float(qty)
    code=c.execute('SELECT Code FROM Products WHERE PID=?',(pid,)).fetchone()
    c.execute('INSERT INTO StockMoves (MoveDay,PID,Code,MoveType,Qty,BeforeQty,AfterQty,ReferenceType,ReferenceID,ByUser,ByEmpNo,CreatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (day or now_s()[:10],pid,code[0] if code else '',kind,qty,b,a,ref_type,ref_id,user.get('u',''),user.get('emp'),now_s()))
    c.execute('INSERT OR IGNORE INTO Stock (PID,Qty) VALUES (?,0)', (pid,))
    c.execute('UPDATE Stock SET Qty=Qty+? WHERE PID=?', (round(qty, 3), pid))
def inventory_adjust(c, item, delta, kind, user, ref_type='', ref_id='', day=None):
    """تحديث الرصيد الفعلي: Recipe/StoreStock للخامات، وStock للأصناف المخزنة مباشرة."""
    pid=int(item['id']); qty=float(delta); um=usage_map(c)
    raw=um.get(pid) or []
    if not raw:
        code=c.execute('SELECT Code FROM Products WHERE PID=?',(pid,)).fetchone()
        if code and code[0] and c.execute('SELECT 1 FROM StoreStock WHERE Code=?',(str(code[0]),)).fetchone():
            raw=[(str(code[0]),1.0)]
    if raw:
        for code,per in raw:
            amount=round(qty*float(per),3)
            row=c.execute('SELECT Qty FROM StoreStock WHERE Code=?',(code,)).fetchone(); before=float(row[0] or 0) if row else 0
            c.execute('INSERT OR IGNORE INTO StoreStock (Code,Qty) VALUES (?,0)',(code,)); c.execute('UPDATE StoreStock SET Qty=Qty+? WHERE Code=?',(amount,code))
            after=before+amount
            c.execute('INSERT INTO StockMoves (MoveDay,PID,Code,MoveType,Qty,BeforeQty,AfterQty,ReferenceType,ReferenceID,ByUser,ByEmpNo,CreatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(day or now_s()[:10],pid,code,kind,amount,before,after,ref_type,ref_id,user.get('u',''),user.get('emp'),now_s()))
    else:
        stock_move(c,pid,qty,kind,user,ref_type,ref_id,day)
def ensure_hist(c, source):
    hist=source+'Hist'
    c.execute('CREATE TABLE IF NOT EXISTS %s AS SELECT * FROM %s WHERE 0' % (hist,source))
    return hist
def archive_close_batch(c, ids, user, business_day):
    ids=[str(x) for x in ids]
    if not ids: return {'count':0,'total':0}
    qs=','.join('?'*len(ids))
    for src in ('Sales','SaleItems','SalePayments','SaleAdjustments','SaleLog'):
        hist=ensure_hist(c,src); key='SID'
        c.execute('INSERT OR IGNORE INTO %s SELECT * FROM %s WHERE SID IN (%s)'%(hist,src,qs),ids)
    for src,key,cond in [('Returns','RID','SID IN (%s)'%qs),('ReturnItems','RID','RID IN (SELECT RID FROM ReturnsHist WHERE SID IN (%s))'%qs)]:
        hist=ensure_hist(c,src)
        if src=='Returns': c.execute("INSERT OR IGNORE INTO %s SELECT * FROM %s WHERE SID IN (%s) AND Status<>'due'"%(hist,src,qs),ids)
        else: c.execute('INSERT OR IGNORE INTO %s SELECT * FROM %s WHERE RID IN (SELECT RID FROM ReturnsHist WHERE SID IN (%s))'%(hist,src,qs),ids)
    ensure_hist(c,'StockMoves'); c.execute('INSERT OR IGNORE INTO StockMovesHist SELECT * FROM StockMoves WHERE MoveDay<=?',(business_day,)); c.execute('DELETE FROM StockMoves WHERE MoveDay<=?',(business_day,))
    ensure_hist(c,'TableAudit'); c.execute('INSERT OR IGNORE INTO TableAuditHist SELECT * FROM TableAudit WHERE substr(At,1,10)<=?',(business_day,)); c.execute('DELETE FROM TableAudit WHERE substr(At,1,10)<=?',(business_day,))
    ensure_hist(c,'POrders'); ensure_hist(c,'POItems')
    done=[r[0] for r in c.execute("SELECT POID FROM POrders WHERE Stage IN ('received','cancelled') AND PODay<=?",(business_day,)).fetchall()]
    for poid in done:
        c.execute('INSERT OR IGNORE INTO POrdersHist SELECT * FROM POrders WHERE POID=?',(poid,)); c.execute('INSERT OR IGNORE INTO POItemsHist SELECT * FROM POItems WHERE POID=?',(poid,)); c.execute('DELETE FROM POItems WHERE POID=?',(poid,)); c.execute('DELETE FROM POrders WHERE POID=?',(poid,))
    summary_statement(business_day, c)
    c.execute('INSERT OR REPLACE INTO SalesSummaryHist SELECT * FROM SalesSummary WHERE BusinessDay=?',(business_day,))
    total=c.execute('SELECT COALESCE(SUM(STotal),0) FROM Sales WHERE SID IN (%s)'%qs,ids).fetchone()[0] or 0
    moves=c.execute('SELECT COUNT(*) FROM StockMoves WHERE MoveDay<=?',(business_day,)).fetchone()[0]
    c.execute('INSERT INTO CloseBatches (BusinessDay,ClosedAt,ClosedBy,SalesCount,SalesTotal,StockMovesCount,Status,Note) VALUES (?,?,?,?,?,?,?,?)',(business_day,now_s(),user.get('u',''),len(ids),total,moves,'verified','sales and completed purchase orders archived'))
    # حذف التشغيل بعد نجاح النسخ
    for src in ('SaleItems','SalePayments','SaleAdjustments','SaleLog','Returns'):
        c.execute('DELETE FROM %s WHERE SID IN (%s)'%(src,qs),ids) if src!='Returns' else c.execute("DELETE FROM Returns WHERE SID IN (%s) AND Status<>'due'"%qs,ids)
    c.execute('DELETE FROM ReturnItems WHERE RID IN (SELECT RID FROM ReturnsHist)')
    c.execute('DELETE FROM Sales WHERE SID IN (%s)'%qs,ids)
    return {'count':len(ids),'total':total}

LOCK_MSG = '🚫 غير مسموح! هذا اليوم تم تقفيله رسمياً. استخدم شاشة المرتجع بتاريخ اليوم.'


class Forbidden(Exception):
    pass


def check_unlocked(c, day, closed=False):
    # الفاتورة اللي اتقفلت (Closed=1) مقفولة حتى لو يومها لسه مش في day_locks (قفل بعد نص الليل / قفل وردية في نص اليوم)
    if closed or c.execute('SELECT 1 FROM day_locks WHERE day=?', (day,)).fetchone():
        raise Forbidden(LOCK_MSG)
def log(c, sid, act, user, reason, detail):
    c.execute('INSERT INTO SaleLog (SID,Act,ByUser,Reason,At,Detail) VALUES (?,?,?,?,?,?)',
              (sid, act, user['u'], reason, now_s(), json.dumps(detail, ensure_ascii=False)))


def snapshot(c, sid):
    # الفاتورة بعد قفل اليوم بتتنقل لجداول Hist، فنقرا منها لو مش لاقيينها في الشغّال (للمرتجع ورسالة القفل)
    sfx = ''
    r = c.execute('SELECT %s FROM Sales WHERE SID=?' % SALE_COLS, (sid,)).fetchone()
    if not r:
        r = c.execute('SELECT %s FROM SalesHist WHERE SID=?' % SALE_COLS, (sid,)).fetchone()
        sfx = 'Hist'
    if not r:
        raise Exception('الأوردر غير موجود')
    items = [dict(id=i[0], name=i[1], cat=i[2], unit=i[3], qty=i[4], price=i[5])
             for i in c.execute('SELECT PID,IName,Cat,Unit,Qty,Price FROM SaleItems%s WHERE SID=?' % sfx, (sid,))]
    payments = [dict(method=x[0], code=int(x[1] or 1), amount=float(x[2] or 0)) for x in c.execute('SELECT Method,Code,Amount FROM SalePayments%s WHERE SID=? ORDER BY rowid' % sfx, (sid,))]
    return row_to_sale(r, items, payments), (bool(r[15]) or bool(sfx))


def do_void(d, user):
    reason = str(d.get('reason', '')).strip()
    if not reason:
        raise Exception('اكتب سبب الإلغاء')
    with LOCK, db() as c:
        s, closed = snapshot(c, d['id'])
        check_unlocked(c, s['day'], closed)
        if s['returned']:
            raise Exception('الأوردر عليه مرتجع - مينفعش يتلغي')
        if c.execute("SELECT 1 FROM Dispatch WHERE SaleID=? AND Status IN ('out','delivered')", (d['id'],)).fetchone():
            raise Exception('الأوردر متحمّل على مندوب - اسحبه الأول من شاشة الدليفري')
        if s['void']:
            raise Exception('الأوردر ملغي بالفعل')
        if s.get('orderState') == 'F':
            raise Exception('OrderState=F لأوردر ملغي = خطأ — الأوردر متسلّم بالفعل')
        c.execute("UPDATE Sales SET Void=1,VReason=?,VBy=?,VAt=?,OrderAction='C' WHERE SID=?", (reason, user['u'], now_s(), d['id']))
        if closed:
            stock_back(c, s['items'], [])
        log(c, d['id'], 'void', user, reason, {'before': s})


# ★★★ [EDIT-MAP] تعديل أوردر ★★★
def do_edit(d, user):
    reason = str(d.get('reason', '')).strip()
    if not reason:
        raise Exception('اكتب سبب التعديل')
    items = []
    for i in d.get('items') or []:
        q, p = float(i['qty']), float(i['price'])
        if q <= 0 or p < 0:
            raise Exception('كمية أو سعر غير صحيح')
        items.append(dict(id=int(i['id']), name=str(i['name']), cat=str(i.get('cat') or ''),
                          unit='kg' if i.get('unit') == 'kg' else 'pc', qty=round(q, 3), price=p))
    if not items:
        raise Exception('الأوردر لازم يفضل فيه صنف واحد على الأقل (للإلغاء استخدم Void)')
    with LOCK, db() as c:
        s, closed = snapshot(c, d['id'])
        check_unlocked(c, s['day'], closed)
        if s['returned']:
            raise Exception('الأوردر عليه مرتجع - مينفعش يتعدل')
        if c.execute("SELECT 1 FROM Dispatch WHERE SaleID=? AND Status IN ('out','delivered')", (d['id'],)).fetchone():
            raise Exception('الأوردر متحمّل على مندوب - اسحبه الأول من شاشة الدليفري')
        if s['void']:
            raise Exception('الأوردر ملغي - مينفعش يتعدل')
        cu = d.get('cust') or {}
        if s['type'] == 'delivery':
            cn, cp, ca = cu.get('name', ''), cu.get('phone', ''), cu.get('addr', '')
        elif s['type'] == 'pickup':
            cn, cp, ca = cu.get('name', ''), cu.get('phone', ''), ''
            if d.get('fee'):
                d['total'] = round(float(d['total']) - float(d['fee']), 2)
            d['fee'] = 0
        else:
            cn, cp, ca = '', '', ''
        c.execute('UPDATE Sales SET SSub=?,SDisc=?,STax=?,SFee=?,STotal=?,CName=?,CPhone=?,CAddr=?,EBy=?,EAt=?,ECount=ECount+1,Comment=? WHERE SID=?',
                  (float(d['sub']), float(d['disc']), float(d.get('tax', 0)), float(d.get('fee', 0)), float(d['total']),
                   cn, cp, ca, user['u'], now_s(), str(d.get('comment', s['comment']) or '').strip(), d['id']))
        c.execute('DELETE FROM SaleItems WHERE SID=?', (d['id'],))
        put_items(c, d['id'], items)
        if closed:
            stock_back(c, s['items'], items)
        after, _ = snapshot(c, d['id'])
        log(c, d['id'], 'edit', user, reason, {'before': s, 'after': after})


def get_log(sid):
    with db() as c:
        return [dict(act=r[0], by=r[1], reason=r[2], at=r[3])
                for r in c.execute('SELECT Act,ByUser,Reason,At FROM SaleLog WHERE SID=? ORDER BY LID', (sid,))]


# ------------------------------------------------------------------ misc
# ★★★ [EDIT-MAP] الطباعة على طابعة الشبكة ★★★
def backup_after_close(day):
    """نسخة تلقائية بعد قفل اليوم داخل data/backups (آخر 30)"""
    try:
        import shutil
        bk = os.path.join(data_dir(), 'backups')
        os.makedirs(bk, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = 'close_%s_%s.db' % ((day or '')[:10], stamp)
        src = os.path.join(data_dir(), 'pos_data.db')
        if not os.path.isfile(src):
            return
        with db() as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        shutil.copy2(src, os.path.join(bk, name))
        files = sorted(
            [os.path.join(bk, f) for f in os.listdir(bk) if f.endswith('.db') or f.endswith('.tar.gz')],
            key=lambda p: os.path.getmtime(p), reverse=True)
        for old in files[30:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception as e:
        print('backup_after_close failed:', e)


def do_print(d):
    """يبعت بيانات ESC/POS خام للطابعة على الشبكة (بورت 9100)"""
    ip = str(d.get('ip', '')).strip()
    port = int(d.get('port') or 9100)
    try:
        a = ipaddress.ip_address(ip)
    except Exception:
        raise Exception('عنوان IP غير صحيح')
    if not (a.is_private or a.is_loopback):
        raise Exception('الـ IP لازم يكون على الشبكة المحلية')
    try:
        data = base64.b64decode(d.get('data', ''))
    except Exception:
        raise Exception('بيانات الطباعة غير صالحة')
    try:
        with socket.create_connection((ip, port), timeout=5) as s:
            s.settimeout(10)
            s.sendall(data)
    except Exception as e:
        raise Exception('تعذر الاتصال بالطابعة %s:%d (%s)' % (ip, port, e))


def do_import(d):
    save_state(d, 'admin')
    with LOCK, db() as c:
        c.execute('DELETE FROM Sales')
        c.execute('DELETE FROM SaleItems')
        for closed, lst in ((1, d.get('archive', [])), (0, d.get('sales', []))):
            for k, s in enumerate(lst):
                s['id'] = s.get('id') or '%s-%s-%s-%d' % (s['day'], s['no'], closed, k)
                ins_sale(c, s, closed)
        c.execute('DELETE FROM day_locks')
        c.execute("INSERT OR IGNORE INTO day_locks (day,locked_at,locked_by) SELECT DISTINCT SDay,?,'import' FROM Sales WHERE Closed=1", (now_s(),))


def reset():
    with LOCK, db() as c:
        for t in ('Sales', 'SaleItems', 'Stock', 'SaleLog', 'day_locks', 'Returns', 'ReturnItems', 'Stocktake', 'StocktakeMeta', 'StoreAdj', 'StockCount'):
            c.execute('DELETE FROM ' + t)


LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MIN = 10


class LoginLocked(Exception):
    def __init__(self, secs):
        self.secs = secs
        Exception.__init__(self, 'الحساب مقفول مؤقتاً بعد %d محاولات غلط - جرّب بعد %d دقيقة' % (LOGIN_MAX_FAILS, max(1, -(-secs // 60))))


def _login_lock_secs(c, key, now):
    """لو آخر 5 محاولات غلط في آخر 10 دقايق (من غير نجاح بينهم) => ثواني القفل المتبقية، وإلا 0"""
    win = now - LOGIN_LOCK_MIN * 60000
    last_ok = c.execute('SELECT IFNULL(MAX(AtMs),0) FROM LoginLog WHERE UKey=? AND OK=1', (key,)).fetchone()[0]
    rows = c.execute("SELECT AtMs FROM LoginLog WHERE UKey=? AND OK=0 AND IFNULL(Note,'')!='locked' AND AtMs>? AND AtMs>? ORDER BY AtMs DESC LIMIT ?",
                     (key, win, last_ok, LOGIN_MAX_FAILS)).fetchall()
    if len(rows) >= LOGIN_MAX_FAILS:
        left = (rows[0][0] + LOGIN_LOCK_MIN * 60000 - now) // 1000
        return max(0, left)
    return 0


def login(u, p, ip=''):
    u = (u or '').strip()
    key = u.lower()[:60]
    now = int(time.time() * 1000)
    with LOCK, db() as c:
        left = _login_lock_secs(c, key, now) if key else 0
        if left > 0:
            c.execute('INSERT INTO LoginLog (UKey,UName,At,AtMs,OK,IP,Note) VALUES (?,?,?,?,0,?,?)', (key, u[:60], now_s(), now, ip, 'locked'))
            raise LoginLocked(left)
        # الدخول بكود الموظف (EmpNo) أو باسم الدخول القديم (حسابات v12 زي admin)
        r = c.execute('SELECT UName,JobNo,Name,EmpNo,PHash FROM Employees WHERE lower(UName)=lower(?) AND Active=1', (u,)).fetchone()
        if not r and u.isdigit():
            r = c.execute('SELECT UName,JobNo,Name,EmpNo,PHash FROM Employees WHERE EmpNo=? AND Active=1', (int(u),)).fetchone()
        role = JOB_ROLE.get(r[1]) if r else None
        ok = bool(r) and vp(r[0], p or '', r[4]) and role not in (None, 'delivery')  # المناديب ملهمش دخول
        c.execute('INSERT INTO LoginLog (UKey,UName,EmpNo,At,AtMs,OK,IP,Note) VALUES (?,?,?,?,?,?,?,?)',
                  (key, (r[0] if r else u)[:60], r[3] if r else None, now_s(), now, 1 if ok else 0, ip,
                   '' if ok else ('غير موجود' if not r else 'باسورد غلط')))
        if not ok:
            return None
        if hp_is_legacy(r[4]):  # ترقية تلقائية للهاش القديم إلى PBKDF2 عند أول دخول ناجح
            c.execute('UPDATE Employees SET PHash=? WHERE EmpNo=?', (hp(r[0], p or ''), r[3]))
    return {'u': r[0], 'role': role, 'full': r[2], 'emp': r[3], 'job': r[1]}


def login_log(limit=200):
    with db() as c:
        return [dict(at=a, u=u or k, emp=e, ok=bool(o), ip=ip or '', note=n or '')
                for a, u, k, e, o, ip, n in c.execute('SELECT At,UName,UKey,EmpNo,OK,IP,Note FROM LoginLog ORDER BY LID DESC LIMIT ?', (int(limit),)).fetchall()]


def list_jobs():
    with db() as c:
        return [dict(job=j, name=n) for j, n in c.execute('SELECT JobNo,JobName FROM Jobs WHERE Active=1 ORDER BY JobNo').fetchall()]


def list_users():
    with db() as c:
        out = []
        jobs = {j: n for j, n in c.execute('SELECT JobNo,JobName FROM Jobs').fetchall()}
        for un, jn, nm, ph, en in c.execute('SELECT UName,JobNo,Name,Phone,EmpNo FROM Employees WHERE UName IS NOT NULL AND Active=1 ORDER BY JobNo,EmpNo').fetchall():
            role = JOB_ROLE.get(jn, 'cashier')
            if role == 'delivery' and not ph:
                d = c.execute('SELECT Phone FROM Deliveries WHERE DName=? LIMIT 1', (nm or un,)).fetchone()
                ph = d[0] if d else ''
            out.append(dict(u=un, role=role, full=nm or '', code=en, job=jn, jobName=jobs.get(jn, ''), phone=ph or ''))
        return out


NAME_RE = re.compile(r'[A-Za-z\u0600-\u06FF\u0750-\u077F ]+')


def gen_pw():
    return ''.join(secrets.choice('0123456789') for _ in range(6))


def save_user(d):
    """إضافة/تعديل/حذف موظف. المعرّف = EmpNo (كود الموظف، تلقائي ومايتغيّرش). يرجّع {'emp','pw'} (pw بس لو اتولّد/اتغيّر)."""
    emp = d.get('emp')
    emp = int(emp) if str(emp or '').isdigit() else None

    def admins_left(c):
        return c.execute('SELECT COUNT(*) FROM Employees WHERE JobNo IN (1,2) AND Active=1 AND UName IS NOT NULL').fetchone()[0]

    with LOCK, db() as c:
        ex = None
        if emp:
            ex = c.execute('SELECT EmpNo,JobNo,Name,UName,Phone FROM Employees WHERE EmpNo=?', (emp,)).fetchone()
        elif d.get('u'):
            ex = c.execute('SELECT EmpNo,JobNo,Name,UName,Phone FROM Employees WHERE UName=?', (str(d['u']).strip(),)).fetchone()
        if (emp or d.get('u')) and not ex:
            raise Exception('الموظف غير موجود')

        # ---- حذف
        if d.get('del'):
            if ex[1] in (1, 2) and admins_left(c) <= 1:
                raise Exception('لا يمكن حذف آخر مدير')
            if ex[1] == 5:
                rid = c.execute('SELECT DID FROM Deliveries WHERE DName=? LIMIT 1', (ex[2] or ex[3],)).fetchone()
                if rid:
                    if c.execute('SELECT 1 FROM DeliveryShifts WHERE DID=? LIMIT 1', (rid[0],)).fetchone():
                        c.execute('UPDATE Deliveries SET Active=0 WHERE DID=?', (rid[0],))
                    else:
                        c.execute('DELETE FROM Deliveries WHERE DID=?', (rid[0],))
            used = (c.execute('SELECT 1 FROM SalesAll WHERE EmpNo=? LIMIT 1', (ex[0],)).fetchone()
                    or c.execute('SELECT 1 FROM SaleLog WHERE ByEmpNo=? LIMIT 1', (ex[0],)).fetchone())
            if used:
                c.execute('UPDATE Employees SET Active=0 WHERE EmpNo=?', (ex[0],))
            else:
                c.execute('DELETE FROM Employees WHERE EmpNo=?', (ex[0],))
            return {'emp': ex[0]}

        # ---- القيم (في التعديل: اللي مش مبعوت يفضل زي ما هو)
        name = (d['name'] if 'name' in d else (ex[2] if ex else '')) or ''
        name = re.sub(r'\s+', ' ', str(name)).strip()
        if not name:
            raise Exception('اكتب اسم الموظف')
        if not NAME_RE.fullmatch(name):
            raise Exception('الاسم لازم حروف عربي أو إنجليزي فقط')
        jn = int(d['job']) if str(d.get('job') or '').isdigit() else (ex[1] if ex else None)
        if jn is None:
            raise Exception('اختار الوظيفة')
        if not c.execute('SELECT 1 FROM Jobs WHERE JobNo=? AND Active=1', (jn,)).fetchone() and not (ex and ex[1] == jn):
            raise Exception('الوظيفة غير موجودة')
        phone = str(d['phone']).strip() if 'phone' in d else (ex[4] if ex else '')
        phone = phone or ''
        if phone and not re.fullmatch(r'[0-9+ \-]{5,20}', phone):
            raise Exception('رقم التليفون غير صحيح')
        role = JOB_ROLE.get(jn, 'cashier')
        if role == 'delivery' and not phone:
            raise Exception('اكتب تليفون المندوب')

        pw_out = None
        if ex:
            if ex[1] in (1, 2) and jn not in (1, 2) and admins_left(c) <= 1:
                raise Exception('لا يمكن تغيير وظيفة آخر مدير')
            c.execute('UPDATE Employees SET JobNo=?, Name=?, Phone=?, Active=1 WHERE EmpNo=?', (jn, name, phone, ex[0]))
            if d.get('p') or d.get('regen'):
                pw_out = d.get('p') or gen_pw()
                c.execute('UPDATE Employees SET PHash=?, Password=? WHERE EmpNo=?', (hp(ex[3], pw_out), '', ex[0]))
            eno, old_name = ex[0], ex[2]
        else:
            pw_out = d.get('p') or gen_pw()
            cur = c.execute('INSERT INTO Employees (Name,JobNo,Phone,Password,Active,CreatedAt,UName,PHash) VALUES (?,?,?,?,1,?,?,NULL)',
                            (name, jn, phone, '', now_s(), 'tmp' + secrets.token_hex(6)))
            eno, old_name = cur.lastrowid, None
            un = str(eno)
            if c.execute('SELECT 1 FROM Employees WHERE UName=?', (un,)).fetchone():
                un = 'E' + un
            c.execute('UPDATE Employees SET UName=?, PHash=? WHERE EmpNo=?', (un, hp(un, pw_out), eno))

        # ---- مزامنة المندوب مع Deliveries (شاشة الدليفري)
        if role == 'delivery':
            row = c.execute('SELECT DID FROM Deliveries WHERE DName=? LIMIT 1', (old_name or name,)).fetchone()
            if not row:
                row = c.execute("SELECT DID FROM Deliveries WHERE Phone=? AND Phone!='' LIMIT 1", (phone,)).fetchone()
            if row:
                c.execute('UPDATE Deliveries SET DName=?, Phone=?, Active=1 WHERE DID=?', (name, phone, row[0]))
            else:
                c.execute('INSERT INTO Deliveries (DName, Phone, Active) VALUES (?,?,1)', (name, phone))
        return {'emp': eno, 'pw': pw_out}


# ------------------------------------------------------------------ مرتجع (v4) - 4 أقفال
# 1) بيختار فاتورة حقيقية من السيستم والأصناف بتيجي منها  2) كل صنف يرجع مرة واحدة بس (بالكمية)
# 3) باسورد مدير + سبب من قايمة مقفولة  4) فلوس المرتجع "مديونية مرتجع" مش من الدرج: المدير يصرفها من خزنة منفصلة
RET_REASONS_DEF = ['أوردر غلط', 'جودة سيئة', 'الزبون لغى', 'تأخير في التوصيل', 'صنف ناقص', 'أخرى']
FAILS = {}


def ret_reasons(cfg):
    r = [x.strip() for x in re.split(r'[,،]', str(cfg.get('retReasons') or '')) if x.strip()]
    return r or RET_REASONS_DEF


def find_manager(c, pw):
    for u, full, h in c.execute("SELECT UName,Name,PHash FROM Employees WHERE JobNo IN (1,2) AND Active=1 AND UName IS NOT NULL AND PHash IS NOT NULL").fetchall():
        if vp(u, pw, h):
            return u, (full or u)
    return None


def full_name(c, u):
    r = c.execute('SELECT Name FROM Employees WHERE UName=?', (u,)).fetchone()
    return r[0] if r and r[0] else u


def ret_dict(c, r):
    rit = c.execute('SELECT PID,IName,Cat,Unit,Qty,Price,Refund FROM ReturnItems WHERE RID=?', (r[0],)).fetchall() \
        or c.execute('SELECT PID,IName,Cat,Unit,Qty,Price,Refund FROM ReturnItemsHist WHERE RID=?', (r[0],)).fetchall()
    items = [dict(id=i[0], name=i[1], cat=i[2], unit=i[3], qty=i[4], price=i[5], refund=i[6]) for i in rit]
    return dict(id=r[0], no=r[1], sid=r[2], sno=r[3], day=r[4], time=r[5], reason=r[6], by=r[7], mgr=r[8],
                total=r[9], status=r[10], paidBy=r[11] or '', paidAt=r[12] or '', log=r[13] or '', items=items)


RET_COLS = 'RID,RNo,SID,SNo,RDay,RTime,Reason,Cashier,Manager,Total,Status,PaidBy,PaidAt,Log'


def list_returns(c):
    # الشغّال + اللي اتأرشف مع قفل الأيام (مرتجع اتصرف قبل كده)
    rows = {r[0]: r for r in c.execute('SELECT %s FROM ReturnsHist' % RET_COLS).fetchall()}
    rows.update({r[0]: r for r in c.execute('SELECT %s FROM Returns' % RET_COLS).fetchall()})
    return [ret_dict(c, r) for r in sorted(rows.values(), key=lambda r: r[1] or 0)]


# ★★★ [EDIT-MAP] المرتجع بالأقفال الأربعة ★★★
def do_return(d, user):
    now = datetime.now()
    key = user['u']
    fl = [t for t in FAILS.get(key, []) if (now - t).total_seconds() < 600]
    if len(fl) >= 5:
        raise Forbidden('تم إيقاف المرتجع مؤقتاً بسبب باسورد مدير غلط كذا مرة - استنى 10 دقايق')
    with LOCK, db() as c:
        cfg = get_cfg(c)
        reason = str(d.get('reason', '')).strip()
        if reason not in ret_reasons(cfg):
            raise Exception('اختار سبب المرتجع من القايمة')
        mgr = find_manager(c, str(d.get('pw', '')))
        if not mgr:
            fl.append(now)
            FAILS[key] = fl
            c.commit()
            raise Forbidden('باسورد المدير غير صحيح')
        FAILS.pop(key, None)
        s, closed = snapshot(c, d['sid'])
        if s['void']:
            raise Exception('الأوردر ملغي - مينفعش يتعمله مرتجع')
        orig, wsum = {}, {}
        for i in s['items']:
            orig[i['id']] = orig.get(i['id'], 0) + i['qty']
            wsum[i['id']] = wsum.get(i['id'], 0) + i['qty'] * i['price']
        meta = {i['id']: i for i in s['items']}
        done = {r[0]: r[1] for r in c.execute(
            'SELECT PID,SUM(Qty) FROM (SELECT ri.PID,ri.Qty FROM ReturnItems ri JOIN Returns r ON r.RID=ri.RID WHERE r.SID=? '
            'UNION ALL SELECT ri.PID,ri.Qty FROM ReturnItemsHist ri JOIN ReturnsHist r ON r.RID=ri.RID WHERE r.SID=?) GROUP BY PID', (s['id'], s['id']))}
        factor = (s['total'] - s['fee']) / s['sub'] if s['sub'] > 0 else 1.0
        lines, total = [], 0.0
        for it in d.get('items') or []:
            pid, q = int(it['id']), round(float(it['qty']), 3)
            if q <= 0:
                continue
            if pid not in orig:
                raise Forbidden('الصنف ده مش موجود في الفاتورة الأصلية')
            if done.get(pid, 0) + q > orig[pid] + 1e-9:
                raise Forbidden('الكمية دي رجعت قبل كده')
            price = wsum[pid] / orig[pid]
            refund = round(q * price * factor, 2)
            m = meta[pid]
            lines.append((pid, m['name'], m.get('cat') or '', m['unit'], q, round(price, 4), refund))
            total += refund
            done[pid] = done.get(pid, 0) + q
        if not lines:
            raise Exception('اختار كمية للمرتجع')
        rno = (c.execute('SELECT MAX(RNo) FROM Returns').fetchone()[0] or 0) + 1
        rid = secrets.token_hex(8)
        cname = full_name(c, user['u'])
        text = 'مرتجع فاتورة %s بواسطة كاشير %s وافق عليه مدير %s الساعة %s' % (s['no'], cname, mgr[1], now.strftime('%H:%M'))
        c.execute('INSERT INTO Returns (%s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)' % RET_COLS,
                  (rid, rno, s['id'], s['no'], biz_day(c, now), now.strftime('%H:%M'), reason, cname, mgr[1],
                   round(total, 2), 'due', '', '', text))
        c.executemany('INSERT INTO ReturnItems VALUES (?,?,?,?,?,?,?,?)', [(rid,) + l for l in lines])
        for pid,name,cat,unit,q,price,refund in lines:
            inventory_adjust(c,{'id':pid,'name':name,'cat':cat,'unit':unit,'price':price},q,'return',user,'return',rid,biz_day(c,now))
        full = all(done.get(pid, 0) >= orig[pid] - 1e-9 for pid in orig)
        c.execute('UPDATE Sales SET Returned=? WHERE SID=?', (2 if full else 1, s['id']))
        c.execute('UPDATE SalesHist SET Returned=? WHERE SID=?', (2 if full else 1, s['id']))
        log(c, s['id'], 'return', user, text, {'rid': rid, 'total': round(total, 2), 'reason': reason})
        r = c.execute('SELECT %s FROM Returns WHERE RID=?' % RET_COLS, (rid,)).fetchone()
        return ret_dict(c, r)


def do_return_pay(d, user):
    with LOCK, db() as c:
        c.execute("UPDATE Returns SET Status='paid',PaidBy=?,PaidAt=? WHERE RID=? AND Status='due'",
                  (full_name(c, user['u']), now_s(), d['rid']))


# ------------------------------------------------------------------ طلبيات الشراء + الموردين + الأكواد المخزنية (v4)
def po_dict(c, r):
    items = [dict(code=i[0], name=i[1], unit=i[2], qty=i[3], price=i[4], rqty=i[5])
             for i in c.execute('SELECT Code,IName,Unit,Qty,Price,RQty FROM POItems WHERE POID=?', (r[0],))]
    return dict(id=r[0], no=r[1], day=r[2], time=r[3], supplier=r[4], total=r[5], status=r[6], by=r[7],
                sentAt=r[8] or '', note=r[9] or '', err=r[10] or '', stage=r[11] or 'progress',
                recvAt=r[12] or '', recvBy=r[13] or '', items=items)


PO_BASE = 'POID,PONo,PODay,POTime,Supplier,Total,Status,CreatedBy,SentAt,Note,Err'
PO_COLS = PO_BASE + ',Stage,RecvAt,RecvBy'


FISH_CAT = 'أسماك'


# أسعار تقديرية (جنيه/كيلو) من أسواق مصر أكتوبر 2026: سوق العبور (الأسماك) وبوابة الأسعار (السلع) - عدّلها من 🏷️ الموردين والأكواد
# ★★★ [EDIT-MAP] ⭐ أسعار الأسماك التقديرية (أكتوبر 2026) - عدّلها هنا أو من شاشة الأكواد ★★★
FISH_RULES = [('كفتة', 0), ('فيليه', 150), ('قزاز', 300), ('جمبري', 580), ('بلطي', 80), ('مكريل', 120), ('ماكريل', 120), ('مكرونة', 150), ('وقار', 220),
              ('دينيس', 300), ('قشر بياض', 250), ('بربوني', 200), ('بريوني', 200), ('موسى', 330), ('قاروص', 250), ('لوت', 250), ('شعور', 200),
              ('ثعابين', 250), ('كابوريا', 180), ('سبيط', 300), ('كاليماري', 300)]
# ★★★ [EDIT-MAP] ⭐ الخامات الأساسية وأسعارها (ملح، زيت، أرز...) ★★★
STAPLES = [('5001', 'أرز', 'kg', 'بقالة', 35), ('5002', 'دقيق', 'kg', 'بقالة', 27), ('5003', 'زيت عباد الشمس', 'ltr', 'بقالة', 105),
           ('5004', 'زيت ذرة', 'ltr', 'بقالة', 120), ('5005', 'سكر', 'kg', 'بقالة', 35), ('5006', 'مكرونة', 'kg', 'بقالة', 26),
           ('5007', 'طماطم', 'kg', 'خضار', 20), ('5008', 'بصل', 'kg', 'خضار', 17), ('5009', 'ثوم', 'kg', 'خضار', 56),
           ('5010', 'ليمون', 'kg', 'خضار', 27), ('5011', 'بطاطس', 'kg', 'خضار', 20), ('5012', 'بيض', 'pc', 'بقالة', 4.2),
           ('5013', 'فراخ', 'kg', 'لحوم', 95), ('5014', 'كمون', 'kg', 'بهارات', 300), ('5015', 'فلفل أسود', 'kg', 'بهارات', 420),
           ('5016', 'شطة', 'kg', 'بهارات', 215), ('5017', 'كزبرة ناشفة', 'kg', 'بهارات', 140), ('5018', 'بابريكا', 'kg', 'بهارات', 170),
           ('5019', 'ملح', 'kg', 'بقالة', 0), ('5020', 'طحينة', 'kg', 'بقالة', 0)]


def fish_price(name):
    for k, v in FISH_RULES:
        if k in name:
            return v
    return 0


def seed_prices(c):
    c.execute('CREATE TABLE IF NOT EXISTS Flags (K TEXT PRIMARY KEY)')
    if not c.execute("SELECT 1 FROM Flags WHERE K='staples_v1'").fetchone():
        for code, name, unit, cat, price in STAPLES:
            if not c.execute('SELECT 1 FROM StoreItems WHERE Code=? OR IName=?', (code, name)).fetchone():
                c.execute("INSERT INTO StoreItems (Code,IName,Unit,Cat,Price,SupID,Src) VALUES (?,?,?,?,?,0,'')", (code, name, unit, cat, price))
        c.execute("INSERT INTO Flags VALUES ('staples_v1')")
    if not c.execute("SELECT 1 FROM Flags WHERE K='fish_prices_v1'").fetchone():
        for code, name in c.execute("SELECT Code,IName FROM StoreItems WHERE Src='menu' AND IFNULL(Price,0)=0").fetchall():
            if fish_price(name):
                c.execute('UPDATE StoreItems SET Price=? WHERE Code=?', (fish_price(name), code))
        c.execute("INSERT INTO Flags VALUES ('fish_prices_v1')")


# ★★★ [EDIT-MAP] مزامنة أصناف جروب الأسماك مع الأكواد المخزنية ★★★
def sync_fish(c):
    """v7: أصناف جروب الأسماك في المنيو بتظهر تلقائياً في أكواد الطلبيات بنفس كود الصنف (الاسم والوحدة بيتحدّثوا، والسعر/المورد بتحدده انت)"""
    cur = set()
    for code, name, unit in c.execute('SELECT Code,PName,Unit FROM Products WHERE Cat=? AND Code IS NOT NULL AND Code<>\'\'', (FISH_CAT,)).fetchall():
        code = str(code).strip()
        cur.add(code)
        row = c.execute('SELECT IName,Unit,Cat,Src FROM StoreItems WHERE Code=?', (code,)).fetchone()
        if row:
            # FIX: نكتب بس لو فيه تغيير فعلاً (قبل كده كان بيكتب في كل مرة وبيتعلّق لو فيه عملية حفظ شغالة)
            if row[3] == 'menu' and (row[0], row[1], row[2]) != (name, unit or 'kg', FISH_CAT):
                c.execute("UPDATE StoreItems SET IName=?,Unit=?,Cat=? WHERE Code=? AND Src='menu'", (name, unit or 'kg', FISH_CAT, code))
        else:
            c.execute('INSERT INTO StoreItems (Code,IName,Unit,Cat,Price,SupID,Src) VALUES (?,?,?,?,?,0,\'menu\')', (code, name, unit or 'kg', FISH_CAT, fish_price(name)))
    # صنف اتمسح أو اتغيّر كوده من المنيو: يتشال من الأكواد (الطلبيات القديمة محتفظة باسمها)
    if cur:  # لو جروب الأسماك اتسمّى تاني/اتمسح: بنوقف المزامنة ومنمسحش حاجة
        for (code,) in c.execute('SELECT Code FROM StoreItems WHERE Src=\'menu\'').fetchall():
            if code in cur:
                continue
            if c.execute('SELECT 1 FROM POItems WHERE Code=? UNION SELECT 1 FROM Recipe WHERE Code=? LIMIT 1', (code, code)).fetchone():
                c.execute("UPDATE StoreItems SET Src='' WHERE Code=?", (code,))  # عليه طلبيات/ريسبي: يفضل كخامة عادية
            else:
                c.execute('DELETE FROM StoreItems WHERE Code=?', (code,))


# ★★★ [EDIT-MAP] بيانات المخازن: الأكواد والموردين والطلبيات ★★★
def get_store():
    with db() as c:
        sync_fish(c)
        seed_prices(c)
        return dict(
            suppliers=[dict(id=r[0], name=r[1], phone=r[2] or '', note=r[3] or '')
                       for r in c.execute('SELECT SupID,SName,Phone,Note FROM Suppliers ORDER BY SName')],
            items=[dict(code=r[0], name=r[1], unit=r[2], cat=r[3] or '', price=r[4] or 0, supid=r[5] or 0)
                   for r in c.execute('SELECT Code,IName,Unit,Cat,Price,SupID FROM StoreItems ORDER BY Code')],
            templates=[dict(id=r[0], name=r[1], items=json.loads(r[2] or '[]'))
                       for r in c.execute('SELECT TID,TName,Items FROM PoTemplates ORDER BY TName')],
            orders=[po_dict(c, r) for r in c.execute('SELECT %s FROM POrders ORDER BY PONo DESC LIMIT 300' % PO_COLS)],
            stock={})


def store_extra(st):
    """رصيد كل خامة دلوقتي + آخر سعر شراء (من آخر طلبية)"""
    with db() as c:
        rc, cs, ad = mat_move(c, '0000-00-00', '9999-99-99')
        pr = last_prices(c)
    for i in st['items']:
        k = str(i['code'])
        i['bal'] = round(rc.get(k, 0) - cs.get(k, 0) + ad.get(k, 0), 3)
        i['last'] = pr.get(k, 0) or 0
    return st


# ★★★ [EDIT-MAP] إنشاء الطلبية ★★★
def do_po(d, user):
    sup = str(d.get('supplier', '')).strip()
    if not sup:
        raise Exception('اكتب اسم المورد')
    with LOCK, db() as c:
        sync_fish(c)
        ex = c.execute('SELECT %s FROM POrders WHERE POID=?' % PO_COLS, (d['id'],)).fetchone()
        if ex:
            return po_dict(c, ex)
        lines, total = [], 0.0
        for it in d.get('items') or []:
            q = round(float(it['qty']), 3)
            row = c.execute('SELECT Code,IName,Unit,Price FROM StoreItems WHERE Code=?', (str(it['code']).strip(),)).fetchone()
            if not row:
                raise Exception('الكود %s غير موجود' % it['code'])
            if q <= 0:
                raise Exception('كمية غير صحيحة للكود %s' % it['code'])
            lines.append((d['id'], row[0], row[1], row[2], q, row[3] or 0))
            total += q * (row[3] or 0)
        if not lines:
            raise Exception('الطلبية فاضية')
        if not c.execute('SELECT 1 FROM Suppliers WHERE SName=?', (sup,)).fetchone():
            c.execute('INSERT INTO Suppliers (SName,Phone,Note) VALUES (?,?,?)', (sup, '', ''))
        n = c.execute("SELECT MAX(CAST(SUBSTR(PONo,4) AS INTEGER)) FROM POrders").fetchone()[0] or 0
        now = datetime.now()
        c.execute('INSERT INTO POrders (%s) VALUES (?,?,?,?,?,?,?,?,?,?,?)' % PO_BASE,
                  (d['id'], 'PO-%04d' % (n + 1), biz_day(c, now), now.strftime('%H:%M'), sup, round(total, 2),
                   'saved', user['u'], '', str(d.get('note', '')).strip(), ''))
        c.executemany('INSERT INTO POItems (POID,Code,IName,Unit,Qty,Price) VALUES (?,?,?,?,?,?)', lines)
        return po_dict(c, c.execute('SELECT %s FROM POrders WHERE POID=?' % PO_COLS, (d['id'],)).fetchone())


# ------------------------------------------------------------------ v10: ريسبي + حركة الخامات + جرد الشهر
def last_prices(c):
    """سعر شراء كل خامة = سعرها في آخر طلبية (مش ملغاة)، ولو مفيش بياخد سعر الكود"""
    pr = {str(r[0]): (r[1] or 0) for r in c.execute('SELECT Code,Price FROM StoreItems')}
    for code, price in c.execute("SELECT i.Code,i.Price FROM POItems i JOIN POrders o ON o.POID=i.POID "
                                 "WHERE IFNULL(o.Stage,'progress')!='cancelled' AND IFNULL(i.Price,0)>0 ORDER BY o.PONo"):
        pr[str(code)] = price
    return pr


def usage_map(c):
    """pid -> [(كود الخامة, الكمية لكل وحدة بيع)]: من الريسبي. صنف الأسماك من غير ريسبي وكوده = كود خامة بياخد 1:1"""
    um = {}
    for pid, code, q in c.execute('SELECT PID,Code,Qty FROM Recipe'):
        um.setdefault(pid, []).append((str(code), q or 0))
    codes = {str(r[0]) for r in c.execute('SELECT Code FROM StoreItems')}
    for pid, cat, code in c.execute('SELECT PID,Cat,Code FROM Products'):
        if pid not in um and cat == FISH_CAT and code and str(code) in codes:
            um[pid] = [(str(code), 1.0)]
    return um


# ★★★ [EDIT-MAP] حساب تكلفة الصنف من الريسبي ★★★
def unit_cost(c, pid, um, pr):
    if pid in um:
        return round(sum(q * pr.get(code, 0) for code, q in um[pid]), 4)
    r = c.execute('SELECT Cost FROM Products WHERE PID=?', (pid,)).fetchone()
    return (r[0] or 0) if r else 0


# ★★★ [EDIT-MAP] حركة الخامات ★★★
def mat_move(c, lo, hi, closed_only=False, kinds=None):
    """حركة الخامات في الفترة [lo,hi): وارد (طلبيات تم الاستلام) / مستهلك (مبيعات × الريسبي) / تسويات الجرد"""
    recv, cons, adj = {}, {}, {}
    for code, q in c.execute("SELECT i.Code,SUM(IFNULL(i.RQty,0)) FROM POItems i JOIN POrders o ON o.POID=i.POID "
                             "WHERE o.Stage='received' AND substr(o.RecvAt,1,10)>=? AND substr(o.RecvAt,1,10)<? GROUP BY i.Code", (lo, hi)):
        recv[str(code)] = q or 0
    um = usage_map(c)
    sql = ('SELECT i.PID,SUM(i.Qty) FROM SaleItemsAll i JOIN SalesAll s ON s.SID=i.SID WHERE IFNULL(s.Void,0)=0 AND s.SDay>=? AND s.SDay<?'
           + (' AND IFNULL(s.Closed,0)=1' if closed_only else '') + ' GROUP BY i.PID')
    for pid, q in c.execute(sql, (lo, hi)):
        for code, per in um.get(pid, []):
            cons[code] = cons.get(code, 0) + (q or 0) * per
    sql = 'SELECT Code,SUM(Qty) FROM StoreAdj WHERE Day>=? AND Day<?' + (' AND Kind IN (%s)' % ','.join('?' * len(kinds)) if kinds else '') + ' GROUP BY Code'
    for code, q in c.execute(sql, (lo, hi) + tuple(kinds or ())):
        adj[str(code)] = q or 0
    return recv, cons, adj


def _month(m):
    m = str(m or '')[:7]
    if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', m):
        raise Exception('شهر غير صحيح')
    return m


def _prev_month(m):
    y, mo = int(m[:4]), int(m[5:7])
    return '%04d-%02d' % ((y - 1, 12) if mo == 1 else (y, mo - 1))


def _st_calc(c, m):
    lo, hi = m + '-01', m + '-32'
    pr, pc, pa = mat_move(c, '0000-00-00', lo)
    rc, cs, _ = mat_move(c, lo, hi)
    open_adj = {str(r[0]): r[1] for r in c.execute("SELECT Code,Qty FROM StoreAdj WHERE Month=? AND Kind='open'", (m,))}
    prev = c.execute("SELECT 1 FROM StocktakeMeta WHERE Month=? AND Status='closed'", (_prev_month(m),)).fetchone()
    out = {}
    for (code,) in c.execute('SELECT Code FROM StoreItems').fetchall():
        k = str(code)
        prior = pr.get(k, 0) - pc.get(k, 0) + pa.get(k, 0)
        opening = prior + (0 if prev else open_adj.get(k, 0))
        out[k] = dict(prior=prior, opening=round(opening, 3), received=round(rc.get(k, 0), 3), used=round(cs.get(k, 0), 3),
                      expected=round(opening + rc.get(k, 0) - cs.get(k, 0), 3))
    return out, bool(prev)


# ★★★ [EDIT-MAP] جرد الشهر ★★★
def stocktake_get(month):
    m = _month(month)
    with db() as c:
        meta = c.execute('SELECT Status,ClosedBy,ClosedAt FROM StocktakeMeta WHERE Month=?', (m,)).fetchone()
        calc, fixed = _st_calc(c, m)
        pr = last_prices(c)
        mine = {str(r[0]): r[1] for r in c.execute('SELECT Code,Actual FROM StockCount WHERE Month=?', (m,))}
        rows = []
        for code, name, unit, cat in c.execute('SELECT Code,IName,Unit,Cat FROM StoreItems ORDER BY Cat,Code').fetchall():
            k = str(code)
            x = calc[k]
            rows.append(dict(code=k, name=name, unit=unit or 'kg', cat=cat or '', opening=x['opening'], fixed=fixed,
                             received=x['received'], used=x['used'], expected=x['expected'], actual=mine.get(k),
                             price=pr.get(k, 0), value=round(x['expected'] * pr.get(k, 0), 2)))
        return dict(month=m, status=(meta[0] if meta else 'open'), closedBy=(meta[1] if meta else '') or '',
                    closedAt=(meta[2] if meta else '') or '', rows=rows)


def stocktake_save(d):
    m = _month(d.get('month'))
    with LOCK, db() as c:
        st = c.execute('SELECT Status FROM StocktakeMeta WHERE Month=?', (m,)).fetchone()
        if st and st[0] == 'closed':
            raise Exception('جرد الشهر ده اتعتمد - مينفعش يتعدّل')
        calc, fixed = _st_calc(c, m)
        for r in d.get('rows') or []:
            k = str(r['code'])
            if k not in calc:
                continue
            old = c.execute('SELECT Opening,Actual FROM StockCount WHERE Month=? AND Code=?', (m, k)).fetchone()
            op, ac = (old[0], old[1]) if old else (None, None)
            if 'opening' in r and not fixed:
                if r['opening'] in (None, ''):
                    op = None
                    c.execute("DELETE FROM StoreAdj WHERE Month=? AND Code=? AND Kind='open'", (m, k))
                else:
                    op = round(float(r['opening']), 3)
                    c.execute("INSERT OR REPLACE INTO StoreAdj (Month,Code,Kind,Day,Qty) VALUES (?,?,'open',?,?)",
                              (m, k, m + '-01', round(op - calc[k]['prior'], 3)))
            if 'actual' in r:
                ac = None if r['actual'] in (None, '') else round(float(r['actual']), 3)
                if ac is not None and ac < 0:
                    raise Exception('كمية غير صحيحة')
            c.execute('INSERT OR REPLACE INTO StockCount (Month,Code,Opening,Actual) VALUES (?,?,?,?)', (m, k, op, ac))


def stocktake_close(d, user):
    """اعتماد الجرد: الفرق بين العدد الفعلي والمتوقع بيتسجل تسوية، فرصيد المخزن بيبقى = العدد الفعلي ورصيد أول الشهر الجاي كمان"""
    m = _month(d.get('month'))
    with LOCK, db() as c:
        if c.execute("SELECT 1 FROM StocktakeMeta WHERE Month=? AND Status='closed'", (m,)).fetchone():
            return
        calc, _ = _st_calc(c, m)
        for k, ac in c.execute('SELECT Code,Actual FROM StockCount WHERE Month=? AND Actual IS NOT NULL', (m,)).fetchall():
            if str(k) in calc:
                c.execute("INSERT OR REPLACE INTO StoreAdj (Month,Code,Kind,Day,Qty) VALUES (?,?,'count',?,?)",
                          (m, str(k), m + '-31', round(ac - calc[str(k)]['expected'], 3)))
        c.execute('INSERT OR REPLACE INTO StocktakeMeta (Month,Status,ClosedBy,ClosedAt) VALUES (?,?,?,?)',
                  (m, 'closed', full_name(c, user['u']), now_s()))


def stocktake_reopen(d):
    m = _month(d.get('month'))
    with LOCK, db() as c:
        if c.execute("SELECT 1 FROM StocktakeMeta WHERE Month>? AND Status='closed'", (m,)).fetchone():
            raise Exception('فيه جرد شهر بعده معتمد - افتح الأحدث الأول')
        c.execute("DELETE FROM StoreAdj WHERE Month=? AND Kind='count'", (m,))
        c.execute('DELETE FROM StocktakeMeta WHERE Month=?', (m,))


# ★★★ [EDIT-MAP] حفظ الريسبي ★★★
def recipe_save(d):
    pid = int(d['pid'])
    with LOCK, db() as c:
        codes = {str(r[0]) for r in c.execute('SELECT Code FROM StoreItems')}
        rows = {}
        for r in d.get('rows') or []:
            k, q = str(r['code']).strip(), round(float(r['qty']), 4)
            if k not in codes:
                raise Exception('الكود %s مش موجود في الموردين والأكواد' % k)
            if q <= 0:
                raise Exception('كمية غير صحيحة للكود %s' % k)
            rows[k] = round(rows.get(k, 0) + q, 4)
        c.execute('DELETE FROM Recipe WHERE PID=?', (pid,))
        c.executemany('INSERT INTO Recipe (PID,Code,Qty) VALUES (?,?,?)', [(pid, k, q) for k, q in rows.items()])
        if 'cost' in d:
            c.execute('UPDATE Products SET Cost=? WHERE PID=?', (float(d.get('cost') or 0), pid))



# ★★★ v16: أصناف المنيو — حفظ صنف واحد (بدل save_state اللي بيكتب فوق المخزون كله) ★★★
def products_get():
    with db() as c:
        pr_rows, product_printers = printer_rows(c)
        prods = [dict(id=r[0], name=r[1], cat=r[2] or '', unit=r[3] or 'pc', price=r[4] or 0, icon=r[5] or '',
                      code=r[6] or '', hidden=bool(r[7]), cost=r[8] or 0, printer_ids=product_printers.get(int(r[0]), []))
                 for r in c.execute('SELECT PID,PName,Cat,Unit,Price,Icon,Code,Hidden,Cost FROM Products ORDER BY Cat,PID')]
        recipes = {}
        for pid, code, q in c.execute('SELECT PID,Code,Qty FROM Recipe ORDER BY rowid'):
            recipes.setdefault(str(pid), []).append(dict(code=str(code), qty=q))
        mats = [dict(code=str(r[0]), name=r[1], unit=r[2] or '') for r in c.execute('SELECT Code,IName,Unit FROM StoreItems ORDER BY Code')]
    return dict(products=prods, recipes=recipes, materials=mats)


def product_save(d):
    with LOCK, db() as c:
        if d.get('del'):
            pid = int(d['id'])
            c.execute('DELETE FROM Products WHERE PID=?', (pid,))
            c.execute('DELETE FROM Recipe WHERE PID=?', (pid,))
            c.execute('DELETE FROM Stock WHERE PID=?', (pid,))
            c.execute('DELETE FROM ProductPrinters WHERE PID=?', (pid,))
            return {'ok': 1}
        name = str(d.get('name', '')).strip()
        if not name:
            raise Exception('اكتب اسم الصنف')
        price, cost = float(d.get('price') or 0), float(d.get('cost') or 0)
        if price < 0 or cost < 0:
            raise Exception('السعر أو التكلفة غير صحيحة')
        vals = (name, str(d.get('cat', '')).strip(), str(d.get('unit') or 'pc'), price, str(d.get('icon', '')),
                str(d.get('code') or '').strip(), 1 if d.get('hidden') else 0, cost)
        pid = int(d.get('id') or 0)
        if pid and c.execute('SELECT 1 FROM Products WHERE PID=?', (pid,)).fetchone():
            c.execute('UPDATE Products SET PName=?,Cat=?,Unit=?,Price=?,Icon=?,Code=?,Hidden=?,Cost=? WHERE PID=?', vals + (pid,))
        else:
            pid = (c.execute('SELECT MAX(PID) FROM Products').fetchone()[0] or 0) + 1
            c.execute('INSERT INTO Products (PID,PName,Cat,Unit,Price,Icon,Code,Hidden,Cost) VALUES (?,?,?,?,?,?,?,?,?)', (pid,) + vals)
        if 'printer_ids' in d:
            c.execute('DELETE FROM ProductPrinters WHERE PID=?', (pid,))
            for no in d.get('printer_ids') or []:
                row = c.execute('SELECT PrinterID FROM Printers WHERE PrinterNo=?', (int(no),)).fetchone()
                if row: c.execute('INSERT OR IGNORE INTO ProductPrinters (PID,PrinterID) VALUES (?,?)', (pid, row[0]))
        return {'ok': 1, 'id': pid}


# ★★★ [EDIT-MAP] تقارير المخزن والأرباح ★★★
def report_stock(frm, to):
    """تقرير المخزن + تكلفة وربح الأصناف في فترة"""
    lo, hi = (frm or '0000-00-00'), ((to or '9999-99-99') + ' ')
    with db() as c:
        sync_fish(c)
        pr = last_prices(c)
        b_rc, b_cs, b_ad = mat_move(c, '0000-00-00', lo)
        rc, cs, ad = mat_move(c, lo, hi)
        buy = {str(r[0]): (r[1] or 0) for r in c.execute(
            "SELECT i.Code,SUM(IFNULL(i.RQty,0)*IFNULL(i.Price,0)) FROM POItems i JOIN POrders o ON o.POID=i.POID "
            "WHERE o.Stage='received' AND substr(o.RecvAt,1,10)>=? AND substr(o.RecvAt,1,10)<? GROUP BY i.Code", (lo, hi))}
        mats, sv = [], 0.0
        for code, name, unit in c.execute('SELECT Code,IName,Unit FROM StoreItems ORDER BY Cat,Code').fetchall():
            k = str(code)
            op = b_rc.get(k, 0) - b_cs.get(k, 0) + b_ad.get(k, 0)
            cl = op + rc.get(k, 0) - cs.get(k, 0) + ad.get(k, 0)
            val = cl * pr.get(k, 0)
            sv += val
            mats.append(dict(code=k, name=name, unit=unit, opening=round(op, 3), received=round(rc.get(k, 0), 3), used=round(cs.get(k, 0), 3),
                             adj=round(ad.get(k, 0), 3), closing=round(cl, 3), price=pr.get(k, 0), value=round(val, 2), bought=round(buy.get(k, 0), 2)))
        um = usage_map(c)
        prods, tr, tc = [], 0.0, 0.0
        for pid, name, cat, unit, q, rev in c.execute(
                'SELECT i.PID,i.IName,i.Cat,i.Unit,SUM(i.Qty),SUM(i.Qty*i.Price) FROM SaleItemsAll i JOIN SalesAll s ON s.SID=i.SID '
                'WHERE IFNULL(s.Void,0)=0 AND s.SDay>=? AND s.SDay<? GROUP BY i.PID,i.IName ORDER BY 6 DESC', (lo, hi)).fetchall():
            uc = unit_cost(c, pid, um, pr)
            cost = (q or 0) * uc
            tr += rev or 0
            tc += cost
            prods.append(dict(id=pid, name=name, cat=cat or '', unit=unit, qty=round(q or 0, 3), revenue=round(rev or 0, 2), ucost=round(uc, 3),
                              cost=round(cost, 2), profit=round((rev or 0) - cost, 2), nocost=(uc == 0)))
        return dict(mats=mats, prods=prods, totals=dict(revenue=round(tr, 2), cost=round(tc, 2), profit=round(tr - tc, 2),
                                                        stockValue=round(sv, 2), bought=round(sum(buy.values()), 2)))



# ★★★ [EDIT-MAP] المرحلة 5: تقارير متقدمة + أسماء الموظفين ★★★
OTYPE_AR = {'C': 'كاشير', 'V': 'دليفري', 'A': 'استلام فرع', 'D': 'صالة'}
PPLACE_AR = {'C': 'فرع (كاشير)', 'V': 'مندوب', 'A': 'استلام فرع', 'D': 'صالة', 'O': 'أونلاين'}


def emp_name_maps(c):
    """EmpNo -> اسم، UName.lower -> اسم (لعرض الاسم بدل الكود في التقارير)"""
    by_no, by_u = {}, {}
    for en, nm, un in c.execute('SELECT EmpNo,Name,UName FROM Employees'):
        label = (nm or un or str(en) or '').strip()
        by_no[int(en)] = label
        if un:
            by_u[str(un).lower()] = label
    return by_no, by_u


def resolve_name(by_no, by_u, empno=None, uname=None):
    if empno is not None and int(empno or 0) in by_no:
        return by_no[int(empno)]
    if uname and str(uname).lower() in by_u:
        return by_u[str(uname).lower()]
    return (uname or '') or '-'


def _day_range(frm, to):
    lo = (frm or '0000-01-01')[:10]
    hi = (to or '9999-12-31')[:10]
    return lo, hi


def _sales_agg(c, lo, hi):
    """صفوف مبيعات غير ملغاة في الفترة: day, total, disc, fee, otype, pplace, cashier, empno"""
    rows = c.execute(
        'SELECT SDay,STotal,SDisc,SFee,IFNULL(OrderType,\'\'),IFNULL(PayPlace,\'\'),Cashier,EmpNo,SType '
        'FROM SalesAll WHERE IFNULL(Void,0)=0 AND SDay>=? AND SDay<=?', (lo, hi)).fetchall()
    out = []
    for day, total, disc, fee, ot, pp, cash, emp, st in rows:
        if not ot:
            ot = 'A' if st == 'pickup' else ('V' if st == 'delivery' else 'C')
        if not pp:
            pp = ot
        out.append(dict(day=day, total=float(total or 0), disc=float(disc or 0), fee=float(fee or 0),
                        otype=ot, pplace=pp, cashier=cash or '', empno=emp))
    return out


def report_weekly(frm, to):
    """تقرير أسبوعي: ملخص كل يوم في الفترة + إجمالي + تفصيل حسب نوع الأوردر"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        sales = _sales_agg(c, lo, hi)
        by_day = {}
        for s in sales:
            d = by_day.setdefault(s['day'], {'n': 0, 'total': 0.0, 'disc': 0.0, 'fee': 0.0, 'types': {}})
            d['n'] += 1
            d['total'] += s['total']
            d['disc'] += s['disc']
            d['fee'] += s['fee']
            t = d['types'].setdefault(s['otype'], {'n': 0, 'total': 0.0})
            t['n'] += 1
            t['total'] += s['total']
        days = []
        for day in sorted(by_day.keys()):
            d = by_day[day]
            days.append(dict(day=day, n=d['n'], total=round(d['total'], 2), disc=round(d['disc'], 2),
                             fee=round(d['fee'], 2), avg=round(d['total'] / d['n'], 2) if d['n'] else 0,
                             types={k: dict(n=v['n'], total=round(v['total'], 2)) for k, v in d['types'].items()}))
        types = {}
        for s in sales:
            t = types.setdefault(s['otype'], {'n': 0, 'total': 0.0})
            t['n'] += 1
            t['total'] += s['total']
        tot_n = len(sales)
        tot_t = sum(s['total'] for s in sales)
        return dict(
            from_=lo, to=hi, days=days,
            types=[{'code': k, 'label': OTYPE_AR.get(k, k), 'n': v['n'], 'total': round(v['total'], 2)}
                   for k, v in sorted(types.items())],
            totals=dict(n=tot_n, total=round(tot_t, 2), disc=round(sum(s['disc'] for s in sales), 2),
                        fee=round(sum(s['fee'] for s in sales), 2),
                        avg=round(tot_t / tot_n, 2) if tot_n else 0))


def report_monthly(month):
    """تقرير شهري مع مقارنة بالشهر السابق. month=YYYY-MM"""
    m = (month or cur_biz_day())[:7]
    try:
        y, mo = int(m[:4]), int(m[5:7])
    except Exception:
        raise Exception('شهر غير صحيح (YYYY-MM)')
    lo = '%04d-%02d-01' % (y, mo)
    if mo == 12:
        hi = '%04d-12-31' % y
        prev = '%04d-12' % (y - 1)
    else:
        hi = (datetime(y, mo + 1, 1) - timedelta(days=1)).strftime('%Y-%m-%d')
        prev = '%04d-%02d' % (y if mo > 1 else y - 1, mo - 1 if mo > 1 else 12)
    cur = report_weekly(lo, hi)
    # previous month
    py, pm = int(prev[:4]), int(prev[5:7])
    plo = '%04d-%02d-01' % (py, pm)
    if pm == 12:
        phi = '%04d-12-31' % py
    else:
        phi = (datetime(py, pm + 1, 1) - timedelta(days=1)).strftime('%Y-%m-%d')
    prev_r = report_weekly(plo, phi)

    def pct(a, b):
        if not b:
            return None
        return round((a - b) / b * 100, 1)

    ct, pt = cur['totals'], prev_r['totals']
    return dict(
        month=m, from_=lo, to=hi, days=cur['days'], types=cur['types'], totals=ct,
        prev=dict(month=prev, from_=plo, to=phi, totals=pt),
        cmp=dict(
            n=pct(ct['n'], pt['n']),
            total=pct(ct['total'], pt['total']),
            avg=pct(ct['avg'], pt['avg']),
            disc=pct(ct['disc'], pt['disc'])))


def report_riders(frm, to):
    """تقرير كل مندوب: أوردرات + تحصيل + متوسط وقت التوصيل + نسبة نجاح + فروق الشفتات"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        riders = {r[0]: r[1] for r in c.execute('SELECT DID,DName FROM Deliveries')}
        # dispatch في الفترة (حسب OutAt ms أو DeliveredAt)
        rows = c.execute(
            "SELECT d.DID,d.Status,d.Refused,d.OutAt,d.BackAt,d.DeliveredAt,IFNULL(s.STotal,0),IFNULL(s.SFee,0),d.SaleID,"
            "COALESCE(s.SDay,(SELECT COALESCE(ShiftDay,substr(StartAt,1,10)) FROM DeliveryShifts WHERE ShiftID=d.ShiftID)) "
            "FROM Dispatch d LEFT JOIN SalesAll s ON s.SID=d.SaleID").fetchall()
        by = {}
        for did, st, ref, out_at, back_at, del_at, total, fee, sid, sday in rows:
            day = sday or (del_at or '')[:10]  # يوم العمل بتاع الأوردر (أو الشفت) — مش ساعة الجهاز
            if day and (day < lo or day > hi):
                continue
            g = by.setdefault(did, dict(orders=0, delivered=0, refused=0, total=0.0, fees=0.0, times=[]))
            g['orders'] += 1
            if int(ref or 0):
                g['refused'] += 1
            if st == 'delivered':
                g['delivered'] += 1
                g['total'] += float(total or 0)
                g['fees'] += float(fee or 0)
                if out_at and back_at:
                    try:
                        g['times'].append(max(0, (int(back_at) - int(out_at)) / 1000.0))
                    except Exception:
                        pass
                elif out_at and del_at:
                    try:
                        t2 = datetime.strptime(del_at[:19], '%Y-%m-%d %H:%M:%S').timestamp()
                        g['times'].append(max(0, t2 - int(out_at) / 1000.0))
                    except Exception:
                        pass
        # shifts diffs in range
        shifts = c.execute(
            "SELECT DID,IFNULL(SUM(Orders),0),IFNULL(SUM(Collected),0),IFNULL(SUM(Expected),0),IFNULL(SUM(Actual),0),"
            "IFNULL(SUM(Diff),0),COUNT(*) FROM DeliveryShifts WHERE Status='closed' "
            "AND COALESCE(ShiftDay,substr(StartAt,1,10))>=? AND COALESCE(ShiftDay,substr(StartAt,1,10))<=? GROUP BY DID", (lo, hi)).fetchall()
        sh_map = {r[0]: dict(shift_orders=r[1], collected=r[2], expected=r[3], actual=r[4], diff=r[5], shifts=r[6]) for r in shifts}
        out = []
        all_ids = set(by) | set(sh_map) | set(riders)
        for did in sorted(all_ids, key=lambda x: riders.get(x, str(x))):
            g = by.get(did, dict(orders=0, delivered=0, refused=0, total=0.0, fees=0.0, times=[]))
            sh = sh_map.get(did, dict(shift_orders=0, collected=0, expected=0, actual=0, diff=0, shifts=0))
            times = g['times']
            avg_sec = (sum(times) / len(times)) if times else None
            success = round(g['delivered'] / g['orders'] * 100, 1) if g['orders'] else None
            out.append(dict(
                rid=did, name=riders.get(did, 'مندوب #%s' % did),
                orders=g['orders'], delivered=g['delivered'], refused=g['refused'],
                total=round(g['total'], 2), fees=round(g['fees'], 2),
                avg_min=round(avg_sec / 60.0, 1) if avg_sec is not None else None,
                success=success,
                shifts=sh['shifts'], collected=round(sh['collected'] or 0, 2),
                expected=round(sh['expected'] or 0, 2), actual=round(sh['actual'] or 0, 2),
                diff=round(sh['diff'] or 0, 2)))
        return dict(from_=lo, to=hi, riders=out,
                    totals=dict(
                        orders=sum(x['orders'] for x in out),
                        delivered=sum(x['delivered'] for x in out),
                        refused=sum(x['refused'] for x in out),
                        total=round(sum(x['total'] for x in out), 2),
                        diff=round(sum(x['diff'] for x in out), 2)))


def report_returns_adv(frm, to):
    """مرتجعات: بالكاشير + بالسبب + بالمبلغ"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        by_no, by_u = emp_name_maps(c)
        cols = {r[1].lower() for r in c.execute('PRAGMA table_info(Returns)')}
        has_cno = 'cashierno' in cols
        q = ('SELECT RNo,RDay,RTime,Reason,Cashier,Manager,Total,Status' +
             (',CashierNo' if has_cno else '') +
             ' FROM Returns WHERE RDay>=? AND RDay<=? ORDER BY RDay,RNo')
        rows = c.execute(q, (lo, hi)).fetchall()
        items = []
        by_cashier, by_reason = {}, {}
        tot = 0.0
        for row in rows:
            no, day, tm, reason, cash, mgr, total, status = row[:8]
            cno = row[8] if has_cno and len(row) > 8 else None
            total = float(total or 0)
            tot += total
            name = resolve_name(by_no, by_u, cno, cash)
            items.append(dict(no=no, day=day, time=tm or '', reason=reason or '', by=name,
                              mgr=resolve_name(by_no, by_u, None, mgr), total=round(total, 2),
                              status='مستحق' if status == 'due' else 'اتصرف'))
            bc = by_cashier.setdefault(name, dict(n=0, total=0.0))
            bc['n'] += 1
            bc['total'] += total
            rs = (reason or 'بدون سبب').strip() or 'بدون سبب'
            br = by_reason.setdefault(rs, dict(n=0, total=0.0))
            br['n'] += 1
            br['total'] += total
        return dict(
            from_=lo, to=hi, items=items,
            by_cashier=[{'name': k, 'n': v['n'], 'total': round(v['total'], 2)}
                        for k, v in sorted(by_cashier.items(), key=lambda x: -x[1]['total'])],
            by_reason=[{'reason': k, 'n': v['n'], 'total': round(v['total'], 2)}
                       for k, v in sorted(by_reason.items(), key=lambda x: -x[1]['total'])],
            totals=dict(n=len(items), total=round(tot, 2)))


def report_employees(frm, to):
    """تقرير كل موظف (كاشير + استلام فرع): عدد + مبلغ"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        by_no, by_u = emp_name_maps(c)
        sales = _sales_agg(c, lo, hi)
        by = {}
        for s in sales:
            # استلام فرع يتسجل على EmpNo عند التسليم؛ الكاشير على Cashier
            key = None
            if s['empno']:
                key = ('emp', int(s['empno']))
                name = resolve_name(by_no, by_u, s['empno'], s['cashier'])
            else:
                key = ('u', (s['cashier'] or '').lower())
                name = resolve_name(by_no, by_u, None, s['cashier'])
            g = by.setdefault(key, dict(name=name, n=0, total=0.0, disc=0.0, types={}))
            g['n'] += 1
            g['total'] += s['total']
            g['disc'] += s['disc']
            t = g['types'].setdefault(s['otype'], {'n': 0, 'total': 0.0})
            t['n'] += 1
            t['total'] += s['total']
        rows = []
        for g in sorted(by.values(), key=lambda x: -x['total']):
            rows.append(dict(
                name=g['name'], n=g['n'], total=round(g['total'], 2), disc=round(g['disc'], 2),
                avg=round(g['total'] / g['n'], 2) if g['n'] else 0,
                types={k: dict(n=v['n'], total=round(v['total'], 2)) for k, v in g['types'].items()}))
        return dict(from_=lo, to=hi, employees=rows,
                    totals=dict(n=sum(x['n'] for x in rows), total=round(sum(x['total'] for x in rows), 2)))


def report_by_otype(frm, to):
    """حسب نوع الأوردر C/V/A/D"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        sales = _sales_agg(c, lo, hi)
        by = {}
        for s in sales:
            g = by.setdefault(s['otype'] or 'C', dict(n=0, total=0.0, disc=0.0, fee=0.0))
            g['n'] += 1
            g['total'] += s['total']
            g['disc'] += s['disc']
            g['fee'] += s['fee']
        rows = []
        for code in ('C', 'V', 'A', 'D'):
            g = by.get(code) or dict(n=0, total=0.0, disc=0.0, fee=0.0)
            rows.append(dict(code=code, label=OTYPE_AR.get(code, code), n=g['n'],
                             total=round(g['total'], 2), disc=round(g['disc'], 2),
                             fee=round(g['fee'], 2), avg=round(g['total'] / g['n'], 2) if g['n'] else 0))
        # any unknown codes
        for code, g in by.items():
            if code not in ('C', 'V', 'A', 'D'):
                rows.append(dict(code=code, label=code, n=g['n'], total=round(g['total'], 2),
                                 disc=round(g['disc'], 2), fee=round(g['fee'], 2),
                                 avg=round(g['total'] / g['n'], 2) if g['n'] else 0))
        return dict(from_=lo, to=hi, rows=rows,
                    totals=dict(n=sum(r['n'] for r in rows), total=round(sum(r['total'] for r in rows), 2)))


def report_by_payplace(frm, to):
    """حسب مكان الدفع C/V/A/D/O"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        sales = _sales_agg(c, lo, hi)
        by = {}
        for s in sales:
            pp = s['pplace'] or s['otype'] or 'C'
            g = by.setdefault(pp, dict(n=0, total=0.0))
            g['n'] += 1
            g['total'] += s['total']
        rows = []
        for code in ('C', 'V', 'A', 'D', 'O'):
            g = by.pop(code, None) or dict(n=0, total=0.0)
            rows.append(dict(code=code, label=PPLACE_AR.get(code, code), n=g['n'],
                             total=round(g['total'], 2), avg=round(g['total'] / g['n'], 2) if g['n'] else 0))
        for code, g in sorted(by.items()):
            rows.append(dict(code=code, label=code, n=g['n'], total=round(g['total'], 2),
                             avg=round(g['total'] / g['n'], 2) if g['n'] else 0))
        return dict(from_=lo, to=hi, rows=rows,
                    totals=dict(n=sum(r['n'] for r in rows), total=round(sum(r['total'] for r in rows), 2)))


def report_cash_collection(frm, to):
    """تحصيل نقدي: فرع / مناديب / أونلاين + الفرق من محاسبة الشفتات"""
    lo, hi = _day_range(frm, to)
    with db() as c:
        sales = _sales_agg(c, lo, hi)
        branch = riders = online = other = 0.0
        n_b = n_r = n_o = n_x = 0
        for s in sales:
            pp = s['pplace'] or s['otype'] or 'C'
            if pp in ('C', 'A', 'D'):  # فرع / استلام / صالة
                branch += s['total']
                n_b += 1
            elif pp == 'V':
                riders += s['total']
                n_r += 1
            elif pp == 'O':
                online += s['total']
                n_o += 1
            else:
                other += s['total']
                n_x += 1
        # من شفتات المناديب المقفولة
        sh = c.execute(
            "SELECT IFNULL(SUM(Collected),0),IFNULL(SUM(Expected),0),IFNULL(SUM(Actual),0),IFNULL(SUM(Diff),0),COUNT(*) "
            "FROM DeliveryShifts WHERE Status='closed' AND COALESCE(ShiftDay,substr(StartAt,1,10))>=? AND COALESCE(ShiftDay,substr(StartAt,1,10))<=?",
            (lo, hi)).fetchone()
        collected, expected, actual, diff, n_shifts = [x or 0 for x in sh]
        return dict(
            from_=lo, to=hi,
            branch=dict(n=n_b, total=round(branch, 2)),
            riders=dict(n=n_r, total=round(riders, 2)),
            online=dict(n=n_o, total=round(online, 2)),
            other=dict(n=n_x, total=round(other, 2)),
            shifts=dict(n=int(n_shifts), collected=round(collected, 2), expected=round(expected, 2),
                        actual=round(actual, 2), diff=round(diff, 2)),
            totals=dict(
                sales=round(branch + riders + online + other, 2),
                branch=round(branch, 2), riders=round(riders, 2), online=round(online, 2),
                rider_diff=round(diff, 2)))


def report_advanced(kind, frm, to, month=''):
    """موجّه التقارير المتقدمة (مرحلة 5)"""
    kind = (kind or '').strip().lower()
    if kind == 'weekly':
        return report_weekly(frm, to)
    if kind == 'monthly':
        return report_monthly(month or (frm or '')[:7] or cur_biz_day()[:7])
    if kind in ('riders', 'rider'):
        return report_riders(frm, to)
    if kind in ('returns', 'return'):
        return report_returns_adv(frm, to)
    if kind in ('employees', 'employee', 'staff'):
        return report_employees(frm, to)
    if kind in ('otype', 'ordertypes', 'types'):
        return report_by_otype(frm, to)
    if kind in ('payplace', 'pay', 'payment'):
        return report_by_payplace(frm, to)
    if kind in ('cash', 'collection', 'cashcol'):
        return report_cash_collection(frm, to)
    if kind in ('adjustments', 'discounts', 'complimentary'):
        return report_adjustments(frm, to)
    if kind in ('summary', 'summary_statement', 'summarystatement'):
        return summary_statement(frm or cur_biz_day())
    if kind in ('days', 'daily', 'day'):
        return report_by_otype(frm, to)
    if kind in ('items', 'products', 'product'):
        return report_stock(frm, to)  # إيراد/تكلفة أصناف ضمن تقرير المخزن
    raise Exception('نوع تقرير غير معروف: ' + kind)

def report_adjustments(frm, to):
    lo, hi = (frm or '0000-00-00'), ((to or '9999-99-99') + ' ')
    with db() as c:
        rows=c.execute("""SELECT a.AID,a.SID,a.OpCode,a.OpName,a.Scope,a.PID,a.Amount,a.Percent,a.ByUser,a.ByEmpNo,a.At,a.Note,
                               s.SNo,s.SDay,s.STotal,i.IName
                        FROM SaleAdjustmentsAll a JOIN SalesAll s ON s.SID=a.SID
                        LEFT JOIN SaleItemsAll i ON i.SID=a.SID AND i.PID=a.PID
                        WHERE s.SDay>=? AND s.SDay<? ORDER BY a.At DESC""",(lo,hi)).fetchall()
        items=[]
        for r in rows:
            items.append(dict(aid=r[0],sid=r[1],code=int(r[2] or 0),type=r[3] or ('Complimentary' if r[2]==20 else 'Discount'),scope=r[4] or 'invoice',pid=r[5],amount=round(r[6] or 0,2),percent=round(r[7] or 0,2),by=r[8] or '',empNo=r[9],at=r[10] or '',note=r[11] or '',saleNo=r[12],day=r[13],total=round(r[14] or 0,2),item=r[15] or ''))
        return dict(from_=lo[:10],to=to or '',items=items,totals=dict(count=len(items),discount=round(sum(x['amount'] for x in items if x['code']==10),2),complimentary=round(sum(x['amount'] for x in items if x['code']==20),2)))

def summary_statement(day=None, cx=None):
    day=day or (biz_day(cx) if cx is not None else cur_biz_day())
    if cx is not None:
        return _summary_statement(cx, day)
    with db() as c:
        return _summary_statement(c, day)
def _summary_statement(c, day):
        rows=c.execute("SELECT SID,STotal,SSub,SDisc,Void,OrderType,SType,TableNo FROM SalesAll WHERE SDay=?",(day,)).fetchall()
        if not rows:
            h=c.execute('SELECT * FROM SalesSummaryHist WHERE BusinessDay=?',(day,)).fetchone()
            if h:
                return dict(day=h[0],salesCount=h[2],gross=round(h[3] or 0,2),net=round(h[4] or 0,2),discount=round(h[5] or 0,2),complimentary=round(h[6] or 0,2),void=round(h[7] or 0,2),payments=dict(cash=round(h[8] or 0,2),visa=round(h[9] or 0,2),mastercard=round(h[10] or 0,2),meeza=round(h[11] or 0,2),wallet=round(h[12] or 0,2),transfer=round(h[13] or 0,2),other=round(h[14] or 0,2)),types=dict(cashier=round(h[15] or 0,2),delivery=round(h[16] or 0,2),pickup=round(h[17] or 0,2),hall=round(h[18] or 0,2)),hallOpensTables=True)
        valid=[r for r in rows if not r[4]]; void_total=sum(float(r[1] or 0) for r in rows if r[4])
        gross=sum(float(r[2] or 0) for r in valid); net=sum(float(r[1] or 0) for r in valid); disc=sum(float(r[3] or 0) for r in valid)
        adj=c.execute("SELECT OpCode,SUM(Amount) FROM SaleAdjustmentsAll a JOIN SalesAll s ON s.SID=a.SID WHERE s.SDay=? AND IFNULL(s.Void,0)=0 GROUP BY OpCode",(day,)).fetchall(); am={int(x[0] or 0):float(x[1] or 0) for x in adj}
        pays={k:0.0 for k in ('cash','visa','mastercard','meeza','wallet','transfer','other')}
        for method,amount in c.execute("SELECT p.Method,SUM(p.Amount) FROM SalePaymentsAll p JOIN SalesAll s ON s.SID=p.SID WHERE s.SDay=? AND IFNULL(s.Void,0)=0 GROUP BY p.Method",(day,)).fetchall(): pays[str(method or 'other')]=float(amount or 0)
        types={'cashier':0.0,'delivery':0.0,'pickup':0.0,'hall':0.0}
        for r in valid:
            typ='hall' if r[7] is not None or r[5]=='D' else ('delivery' if r[5]=='V' or r[6]=='delivery' else ('pickup' if r[5]=='A' or r[6]=='pickup' else 'cashier')); types[typ]+=float(r[1] or 0)
        vals=(day,now_s(),len(valid),gross,net,am.get(10,0),am.get(20,0),void_total,pays['cash'],pays['visa'],pays['mastercard'],pays['meeza'],pays['wallet'],pays['transfer'],pays['other'],types['cashier'],types['delivery'],types['pickup'],types['hall'])
        c.execute('INSERT OR REPLACE INTO SalesSummary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',vals)
        return dict(day=day,salesCount=len(valid),gross=round(gross,2),net=round(net,2),discount=round(am.get(10,0),2),complimentary=round(am.get(20,0),2),void=round(void_total,2),payments={k:round(v,2) for k,v in pays.items()},types={k:round(v,2) for k,v in types.items()},hallOpensTables=True)


def do_po_stage(d, user):
    """تغيير حالة الطلبية: progress (جاري التنفيذ) / received (تم الاستلام: بيزوّد المخزن مرة واحدة) / cancelled (ملغاة)"""
    stage = d.get('stage')
    if stage not in ('progress', 'received', 'cancelled'):
        raise Exception('حالة غير صحيحة')
    with LOCK, db() as c:
        r = c.execute('SELECT Stage FROM POrders WHERE POID=?', (d['id'],)).fetchone()
        if not r:
            raise Exception('الطلبية غير موجودة')
        cur = r[0] or 'progress'
        if cur == stage:
            return
        if cur == 'received':
            raise Exception('الطلبية اتسجلت \"تم الاستلام\" واتزوّد المخزن - مينفعش يتغيّر')
        if stage == 'received':
            if cur == 'cancelled':
                raise Exception('الطلبية ملغاة - أعد فتحها الأول')
            got = {str(k): float(v) for k, v in (d.get('recv') or {}).items()}
            for code, name, qty in c.execute('SELECT Code,IName,Qty FROM POItems WHERE POID=?', (d['id'],)).fetchall():
                q = round(got.get(str(code), qty), 3)
                if q < 0:
                    raise Exception('كمية غير صحيحة للكود %s' % code)
                c.execute('UPDATE POItems SET RQty=? WHERE POID=? AND Code=?', (q, d['id'], code))
                row=c.execute('SELECT Qty FROM StoreStock WHERE Code=?',(str(code),)).fetchone(); before=float(row[0] or 0) if row else 0
                c.execute('INSERT OR IGNORE INTO StoreStock (Code,Qty) VALUES (?,0)',(str(code),)); c.execute('UPDATE StoreStock SET Qty=Qty+? WHERE Code=?',(q,str(code)))
                pid=c.execute('SELECT PID FROM Products WHERE Code=?',(str(code),)).fetchone(); pid=int(pid[0]) if pid else 0
                c.execute('INSERT INTO StockMoves (MoveDay,PID,Code,MoveType,Qty,BeforeQty,AfterQty,ReferenceType,ReferenceID,ByUser,ByEmpNo,CreatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(now_s()[:10],pid,str(code),'purchase_receive',q,before,before+q,'purchase',d['id'],user.get('u',''),user.get('emp'),now_s()))
            c.execute('UPDATE POrders SET Stage=?,RecvAt=?,RecvBy=? WHERE POID=?', (stage, now_s(), full_name(c, user['u']), d['id']))
        else:
            c.execute('UPDATE POrders SET Stage=?,RecvAt=?,RecvBy=? WHERE POID=?', (stage, '', '', d['id']))


def po_mark(poid, status, err=''):
    with LOCK, db() as c:
        c.execute('UPDATE POrders SET Status=?,Err=?,SentAt=? WHERE POID=?',
                  (status, err[:200], now_s() if status == 'sent' else '', poid))



# ★★★ v15: الفروع + طلبيات التحويل بين الفروع ★★★
def branch_key():
    """مفتاح مشترك بين الفروع (X-Branch-Key) — لازم يتطابق في كل الفروع"""
    with db() as c:
        k = c.execute("SELECT V FROM Settings WHERE K='branchKey'").fetchone()
        if not k or not k[0]:
            k = secrets.token_hex(16)
            c.execute('INSERT OR REPLACE INTO Settings (K,V) VALUES (?,?)', ('branchKey', k))
            return k
        return k[0]


def local_branch(c):
    """الفرع الحالي — أول مرة بنسجله تلقائياً من إعدادات branchCode/branchName"""
    r = c.execute('SELECT Code,BName FROM Branches WHERE IsLocal=1 AND Active=1').fetchone()
    if r:
        return {'code': r[0], 'name': r[1]}
    cfg = get_cfg(c)
    bc = str(cfg.get('branchCode') or 'HQ').strip() or 'HQ'
    bn = str(cfg.get('branchName') or 'الفرع الرئيسي').strip() or 'الفرع الرئيسي'
    if c.execute('SELECT 1 FROM Branches WHERE Code=?', (bc,)).fetchone():
        c.execute('UPDATE Branches SET IsLocal=1 WHERE Code=?', (bc,))
    else:
        c.execute('INSERT OR IGNORE INTO Branches (Code,BName,IP,Port,IsLocal,BKey,Active,CreatedAt) VALUES (?,?,?,?,1,?,1,?)',
                  (bc, bn, '127.0.0.1', PORT, branch_key(), now_s()))
    c.execute('UPDATE Branches SET IsLocal=0 WHERE Code!=? AND IsLocal=1', (bc,))
    return {'code': bc, 'name': bn}


def list_branches():
    with db() as c:
        me = local_branch(c)
        rows = c.execute('SELECT Code,BName,IP,Port,IsLocal,Active,Note FROM Branches ORDER BY IsLocal DESC, Code').fetchall()
        return {'branches': [dict(zip(('code', 'name', 'ip', 'port', 'isLocal', 'active', 'note'), r)) for r in rows],
                'local': me, 'key': branch_key()}


def set_branch_key(d):
    """FIX2: ضبط المفتاح المشترك (نفس القيمة في كل الفروع)"""
    k = str(d.get('key') or '').strip()
    if not re.match(r'^[A-Za-z0-9_-]{8,64}$', k):
        raise ValueError('المفتاح لازم يكون 8-64 حرف/رقم من غير مسافات')
    with LOCK, db() as c:
        c.execute('INSERT OR REPLACE INTO Settings (K,V) VALUES (?,?)', ('branchKey', k))
        c.execute('UPDATE Branches SET BKey=?', (k,))


def save_branch(d):
    code = str(d.get('code', '')).strip()
    if not re.match(r'^[A-Za-z0-9_-]{1,20}$', code):
        raise ValueError('كود الفرع لازم يكون حروف/أرقام من غير مسافات')
    bkey = branch_key()  # قبل فتح الاتصال (يتفادى database is locked)
    with LOCK, db() as c:
        local_branch(c)
        if d.get('isLocal'):
            c.execute('UPDATE Branches SET IsLocal=0')  # فرع واحد بس هو "أنا"
        c.execute('INSERT INTO Branches (Code,BName,IP,Port,IsLocal,BKey,Active,Note,CreatedAt) VALUES (?,?,?,?,?,?,?,?,?) '
                  'ON CONFLICT(Code) DO UPDATE SET BName=excluded.BName,IP=excluded.IP,Port=excluded.Port,'
                  'IsLocal=excluded.IsLocal,Active=excluded.Active,Note=excluded.Note',
                  (code, str(d.get('name') or code), str(d.get('ip') or '').strip(), int(d.get('port') or PORT),
                   1 if d.get('isLocal') else 0, bkey, 1 if d.get('active', 1) else 0,
                   str(d.get('note') or ''), now_s()))


def del_branch(code):
    with LOCK, db() as c:
        if c.execute('SELECT IsLocal FROM Branches WHERE Code=?', (code,)).fetchone() == (1,):
            raise ValueError('مينفعش تمسح الفرع الحالي')
        c.execute('DELETE FROM Branches WHERE Code=?', (code,))


def branch_ping(code):
    with db() as c:
        r = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (code,)).fetchone()
        if not r or not r[0]:
            return {'ok': 0, 'err': 'الفرع مش موجود أو مفصول'}
        try:
            with socket.create_connection((r[0], int(r[1] or 8080)), timeout=3):
                return {'ok': 1}
        except OSError as e:
            return {'ok': 0, 'err': str(e)}


def http_post(ip, port, path, payload, timeout=6):
    import http.client
    conn = http.client.HTTPConnection(ip, int(port or 8080), timeout=timeout)
    conn.request('POST', path, json.dumps(payload, ensure_ascii=False),
                 {'Content-Type': 'application/json', 'X-Branch-Key': branch_key()})
    r = conn.getresponse()
    body = json.loads(r.read().decode('utf-8') or '{}')
    conn.close()
    if r.status != 200:
        raise RuntimeError(body.get('error') or ('HTTP %d' % r.status))
    return body


def next_bono(c):
    n = (c.execute("SELECT V FROM Settings WHERE K='boSeq'").fetchone() or ['0'])[0]
    n = int(n) + 1
    c.execute('INSERT OR REPLACE INTO Settings (K,V) VALUES (?,?)', ('boSeq', str(n)))
    return n


def bo_list(direction):
    with db() as c:
        me = local_branch(c)['code']
        where = 'FromCode=?' if direction == 'out' else 'ToCode=?'
        rows = c.execute('SELECT * FROM BranchOrders WHERE %s ORDER BY BODay DESC,BOTime DESC,BONo DESC' % where, (me,)).fetchall()
        cols = [r[1] for r in c.execute('PRAGMA table_info(BranchOrders)')]
        out = []
        for r in rows:
            o = dict(zip(cols, r))
            o['items'] = [dict(zip(('code', 'iname', 'unit', 'qty', 'rqty'), x))
                          for x in c.execute('SELECT Code,IName,Unit,Qty,RQty FROM BranchOrderItems WHERE BOID=?', (o['BOID'],))]
            out.append(o)
        return out


def bo_save(d, u):
    with LOCK, db() as c:
        me = local_branch(c)
        b = c.execute('SELECT Code,BName FROM Branches WHERE Code=? AND Active=1', (str(d.get('toCode') or ''),)).fetchone()
        if not b:
            raise ValueError('اختار فرع مستلم صحيح')
        if b[0] == me['code']:
            raise ValueError('مينفعش تبعت طلبية لنفس الفرع')
        items = [x for x in (d.get('items') or []) if float(x.get('qty') or 0) > 0]
        if not items:
            raise ValueError('ضيف أصناف للطلبية')
        boid = 'BO-%s-%d' % (me['code'], int(time.time() * 1000))  # فريد عالمياً = مفيش تكرار
        c.execute('INSERT INTO BranchOrders (BOID,BONo,BODay,BOTime,FromCode,FromName,ToCode,ToName,Status,CreatedBy,Note) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (boid, str(next_bono(c)), now_s()[:10], now_s()[11:], me['code'], me['name'], b[0], b[1], 'draft', u['u'], str(d.get('note') or '')))
        for x in items:
            c.execute('INSERT INTO BranchOrderItems (BOID,Code,IName,Unit,Qty,RQty) VALUES (?,?,?,?,?,0)',
                      (boid, str(x.get('code')), str(x.get('iname') or ''), str(x.get('unit') or ''), float(x['qty'])))
        return {'boid': boid}


def stock_apply(c, code, qty, ref, user, empno=0):
    """خصم/إضافة في مخزن الأكواد + حركة مرتبطة بنفس رقم الطلبية (نفس ستايل استلام طلبية المورد)"""
    code = str(code)
    row = c.execute('SELECT Qty FROM StoreStock WHERE Code=?', (code,)).fetchone()
    before = float(row[0] or 0) if row else 0
    c.execute('INSERT OR IGNORE INTO StoreStock (Code,Qty) VALUES (?,0)', (code,))
    c.execute('UPDATE StoreStock SET Qty=Qty+? WHERE Code=?', (qty, code))
    after = before + qty
    c.execute('INSERT INTO StockMoves (MoveDay,PID,Code,MoveType,Qty,BeforeQty,AfterQty,ReferenceType,ReferenceID,ByUser,ByEmpNo,CreatedAt) '
              'VALUES (?,0,?,?,?,?,?,?,?,?,?,?)',
              (now_s()[:10], code, 'branch_out' if qty < 0 else 'branch_in', abs(qty), before, after, 'BranchOrder', ref, user, empno, now_s()))


def bo_send(boid, u):
    with LOCK, db() as c:
        o = c.execute('SELECT Status,ToCode FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        if not o:
            raise ValueError('الطلبية مش موجودة')
        if o[0] != 'draft':
            raise ValueError('الطلبية اتبعتت قبل كده (حالتها: %s)' % o[0])
        b = c.execute('SELECT IP,Port,BName FROM Branches WHERE Code=? AND Active=1', (o[1],)).fetchone()
        if not b or not b[0]:
            raise ValueError('الفرع المستلم مفصول — سجّل IP صحيح من شاشة الفروع')
        cols = [r[1] for r in c.execute('PRAGMA table_info(BranchOrders)')]
        row = c.execute('SELECT * FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        payload = dict(zip(cols, row))
        payload['items'] = [dict(zip(('code', 'iname', 'unit', 'qty'), x))
                            for x in c.execute('SELECT Code,IName,Unit,Qty FROM BranchOrderItems WHERE BOID=?', (boid,))]
        ip, port = b[0], b[1]
        short = []
        for x in payload['items']:
            have = c.execute('SELECT Qty FROM StoreStock WHERE Code=?', (str(x['code']),)).fetchone()
            have = float(have[0] or 0) if have else 0.0
            if have < float(x['qty']):
                short.append('%s (المتاح %g والمطلوب %g)' % (x.get('iname') or x['code'], have, float(x['qty'])))
        if short:  # FIX: منع تحويل كمية أكبر من الرصيد (كان بيطلّع مخزون بالسالب)
            raise ValueError('الرصيد مش كفاية: ' + '، '.join(short))
    try:
        http_post(ip, port, '/api/peer/transfer', payload)
    except Exception as e:
        with LOCK, db() as c:
            c.execute('UPDATE BranchOrders SET Err=? WHERE BOID=?', (str(e)[:200], boid))
        raise ValueError('الإرسال فشل (اتأكد من الشبكة): %s' % e)
    with LOCK, db() as c:
        c.execute("UPDATE BranchOrders SET Status='sent',SentAt=?,Err='' WHERE BOID=?", (now_s(), boid))


def confirm_payload(c, boid):
    """FIX5: تأكيد الاستلام لازم يشيل الكميات المستلمة فعلاً (كان بيبعت رقم الطلبية بس فالمرسل مكانش بيخصم)"""
    return {'boid': boid,
            'items': [{'code': code, 'rqty': float(rq or 0)}
                      for code, rq in c.execute('SELECT Code,RQty FROM BranchOrderItems WHERE BOID=?', (boid,)).fetchall()]}


def peer_transfer(d):
    """استقبال طلبية من فرع تاني — بنتحقق من المفتاح المشترك وإن المرسل معروف"""
    with LOCK, db() as c:
        me = local_branch(c)
        if not d.get('FromCode') or d.get('FromCode') == me['code']:
            raise Forbidden('فرع المرسل غير صحيح')
        if not c.execute('SELECT 1 FROM Branches WHERE Code=? AND Active=1', (d.get('FromCode'),)).fetchone():
            raise Forbidden('فرع المرسل مش مسجّل عندك: %s' % d.get('FromCode'))
        if d.get('ToCode') != me['code']:
            raise Forbidden('الطلبية دي مش موجهة للفرع ده')
        if c.execute('SELECT 1 FROM BranchOrders WHERE BOID=?', (d.get('BOID'),)).fetchone():
            return {'ok': 1, 'dup': 1}  # استلام مرتين = ممنوع
        c.execute('INSERT INTO BranchOrders (BOID,BONo,BODay,BOTime,FromCode,FromName,ToCode,ToName,Status,CreatedBy,SentAt,Note) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (d.get('BOID'), str(d.get('BONo') or ''), d.get('BODay') or now_s()[:10], d.get('BOTime') or now_s()[11:],
                   d.get('FromCode'), d.get('FromName') or d.get('FromCode'), me['code'], me['name'],
                   'sent', d.get('CreatedBy') or '', now_s(), str(d.get('Note') or '')))
        for x in d.get('items') or []:
            if float(x.get('qty') or 0) > 0:
                c.execute('INSERT INTO BranchOrderItems (BOID,Code,IName,Unit,Qty,RQty) VALUES (?,?,?,?,?,0)',
                          (d.get('BOID'), str(x.get('code')), str(x.get('iname') or ''), str(x.get('unit') or ''), float(x['qty'])))
    broadcast({'t': 'reload'})
    return {'ok': 1}  # FIX3: كان بيرجع None فالمرسل بيشوف فشل رغم نجاح التسجيل


def bo_receive(d, u):
    boid = d.get('boid')
    with LOCK, db() as c:
        me = local_branch(c)
        o = c.execute('SELECT Status,FromCode FROM BranchOrders WHERE BOID=? AND ToCode=?', (boid, me['code'])).fetchone()
        if not o:
            raise ValueError('الطلبية مش موجودة')
        if o[0] != 'sent':
            raise ValueError('الطلبية مش بانتظار الاستلام (حالتها: %s)' % o[0])
        items = c.execute('SELECT Code,Qty FROM BranchOrderItems WHERE BOID=?', (boid,)).fetchall()
        over = {str(x.get('code')): float(x.get('rqty')) for x in (d.get('items') or []) if x.get('rqty') not in (None, '')}
        for code, qty in items:
            r = over.get(str(code), qty)  # لو مدخلش كميات مستلمة: بنستلم الكمية كاملة
            r = min(r, float(qty))
            if r <= 0:
                continue
            c.execute('UPDATE BranchOrderItems SET RQty=? WHERE BOID=? AND Code=?', (r, boid, code))
            stock_apply(c, code, r, boid, u['u'], u.get('emp', 0) or 0)  # + للمستلم
        c.execute("UPDATE BranchOrders SET Status='received',RecvAt=?,RecvBy=? WHERE BOID=?", (now_s(), u['u'], boid))
        cpay = confirm_payload(c, boid)
        b = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (o[1],)).fetchone()
    if b and b[0]:  # لو الشبكة شغالة: نأكد للمرسل يخصم من مخزونه
        try:
            http_post(b[0], b[1], '/api/peer/confirm', cpay)
        except Exception:
            pass  # الخصم هيحصل لما المرسل يفتح الطلبية (زرار تأكيدات معلقة)
    broadcast({'t': 'reload'})


def peer_confirm(d):
    boid = d.get('boid')
    with LOCK, db() as c:
        o = c.execute('SELECT Status,FromCode FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        if not o:
            return {'ok': 1}
        if o[0] != 'sent':
            return {'ok': 1}  # اتأكدت/اتستلمت قبل كده — ممنوع التكرار
        me = local_branch(c)
        if o[1] != me['code']:
            raise Forbidden('مش طلبيتك')
        if 'items' not in d:
            raise ValueError('تأكيد الاستلام ناقص الكميات المستلمة')
        recv = {str(x.get('code')): float(x.get('rqty') or 0) for x in (d.get('items') or [])}
        for code, qty in c.execute('SELECT Code,Qty FROM BranchOrderItems WHERE BOID=?', (boid,)).fetchall():
            r = min(recv.get(str(code), 0.0), float(qty))
            if r > 0:
                c.execute('UPDATE BranchOrderItems SET RQty=? WHERE BOID=? AND Code=?', (r, boid, code))
                stock_apply(c, code, -r, boid, 'branch_sync')  # − من المرسل
        c.execute("UPDATE BranchOrders SET Status='done' WHERE BOID=?", (boid,))
    broadcast({'t': 'reload'})
    return {'ok': 1}  # FIX3


def bo_resend_confirms():
    """للمرسل: يجرب يستلم تأكيدات الاستلام المعلقة (لو الشبكة كانت واقعة وقت الاستلام)"""
    n = 0
    with db() as c:
        me = local_branch(c)['code']
        pend = c.execute("SELECT BOID,ToCode FROM BranchOrders WHERE FromCode=? AND Status='sent'", (me,)).fetchall()
        for boid, tocode in pend:
            b = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (tocode,)).fetchone()
            if not b or not b[0]:
                continue
            try:
                http_post(b[0], b[1], '/api/peer/ask', {'boid': boid}, timeout=4)
                n += 1
            except Exception:
                pass
    return {'ok': 1, 'asked': n}


def peer_ask(d):
    """المرسل بيسألنا (المستلم) عن حالة الطلبية — لو استلمناها بعتّله تأكيد الخصم فوراً"""
    boid = d.get('boid')
    with db() as c:
        o = c.execute('SELECT Status,FromCode FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        if o and o[0] == 'received':
            b = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (o[1],)).fetchone()
            cpay = confirm_payload(c, boid)
            if b and b[0]:
                try:
                    http_post(b[0], b[1], '/api/peer/confirm', cpay, timeout=4)
                except Exception:
                    pass
            return {'ok': 1, 'was': 'received'}
        return {'ok': 1, 'was': o[0] if o else 'unknown'}


def bo_cancel(d):
    """FIX4: إلغاء آمن — مينفعش يتلغي بعد ما الطرف التاني استلم، وبيتلغي عند الطرفين"""
    boid = d.get('boid')
    with db() as c:
        me = local_branch(c)['code']
        o = c.execute('SELECT Status,FromCode,ToCode FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        if not o:
            raise ValueError('الطلبية مش موجودة')
        st, fc, tc = o
        if me not in (fc, tc):
            raise ValueError('الطلبية دي مش بتاعة الفرع ده')
        if st == 'cancelled':
            return
        if st in ('received', 'done'):
            raise ValueError('الطلبية اتستلمت — مينفعش تتلغي')
        if st == 'draft':
            if fc != me:
                raise ValueError('المسودة بتاعة الفرع المرسل بس')
            with LOCK, db() as c2:
                c2.execute("UPDATE BranchOrders SET Status='cancelled' WHERE BOID=? AND Status='draft'", (boid,))
            return
        other = tc if fc == me else fc
        b = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (other,)).fetchone()
    if not b or not b[0]:
        raise ValueError('الطلبية مبعوتة — لازم الاتصال بالفرع التاني علشان تتلغي')
    try:
        r = http_post(b[0], b[1], '/api/peer/cancel', {'boid': boid})
    except Exception as e:
        raise ValueError('مقدرتش أتواصل مع الفرع التاني لإلغاء الطلبية: %s' % e)
    if r.get('was') in ('received', 'done'):
        raise ValueError('الفرع التاني استلم الطلبية فعلاً — مينفعش تتلغي (اضغط «تأكيدات معلقة» لتسوية المخزون)')
    with LOCK, db() as c:
        c.execute("UPDATE BranchOrders SET Status='cancelled' WHERE BOID=? AND Status='sent'", (boid,))
    broadcast({'t': 'reload'})


def peer_cancel(d):
    """FIX4: الطرف التاني طلب إلغاء — بنلغي بس لو لسه 'sent' (متستلمتش)"""
    boid = d.get('boid')
    with LOCK, db() as c:
        me = local_branch(c)['code']
        o = c.execute('SELECT Status,FromCode,ToCode FROM BranchOrders WHERE BOID=?', (boid,)).fetchone()
        if not o:
            return {'ok': 1, 'was': 'unknown'}
        if me not in (o[1], o[2]):
            raise Forbidden('مش طلبيتك')
        was = o[0]
        if was == 'sent':
            c.execute("UPDATE BranchOrders SET Status='cancelled' WHERE BOID=?", (boid,))
        b = c.execute('SELECT IP,Port FROM Branches WHERE Code=? AND Active=1', (o[1],)).fetchone() if (was == 'received' and o[2] == me) else None
        cpay = confirm_payload(c, boid) if b else None
    if b and b[0]:  # استلمناها فعلاً: نبعت تأكيد الخصم للمرسل
        try:
            http_post(b[0], b[1], '/api/peer/confirm', cpay, timeout=4)
        except Exception:
            pass
    broadcast({'t': 'reload'})
    return {'ok': 1, 'was': was}


def do_po_send(d):
    """إرسال الطلبية بـ HTTP للعنوان اللي في الإعدادات (poIp/poPort/poPath). لو فشل: بتفضل 'في الانتظار' وتتبعت بعدين"""
    import urllib.request
    with db() as c:
        cfg = get_cfg(c)
        r = c.execute('SELECT %s FROM POrders WHERE POID=?' % PO_COLS, (d['id'],)).fetchone()
        o = po_dict(c, r) if r else None
    if not o:
        raise Exception('الطلبية غير موجودة')
    ip = str(cfg.get('poIp') or '').strip()
    if cfg.get('poMode') != 'http' or not ip:
        raise Exception('إرسال الطلبيات HTTP غير مفعّل في الإعدادات')
    path = str(cfg.get('poPath') or '/')
    url = ip if ip.startswith('http') else 'http://%s:%d%s' % (ip, int(cfg.get('poPort') or 80), path if path.startswith('/') else '/' + path)
    body = json.dumps({'type': 'purchase_order', 'restaurant': cfg.get('name', ''), 'code': o['no'], 'date': o['day'],
                       'time': o['time'], 'supplier': o['supplier'], 'total': o['total'], 'note': o['note'],
                       'items': [dict(i, total=round(i['qty'] * i['price'], 2)) for i in o['items']]}, ensure_ascii=False).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json; charset=utf-8'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            if not 200 <= resp.status < 300:
                raise Exception('HTTP %s' % resp.status)
        po_mark(d['id'], 'sent')
        return {'ok': 1, 'status': 'sent'}
    except Exception as e:
        po_mark(d['id'], 'pending', str(e))
        return {'ok': 1, 'status': 'pending', 'err': str(e)[:200]}


def save_supplier(d):
    with LOCK, db() as c:
        if d.get('del'):
            c.execute('DELETE FROM Suppliers WHERE SupID=?', (int(d['id']),))
        elif d.get('id'):
            c.execute('UPDATE Suppliers SET SName=?,Phone=?,Note=? WHERE SupID=?', (d['name'], d.get('phone', ''), d.get('note', ''), int(d['id'])))
        else:
            if not str(d.get('name', '')).strip():
                raise Exception('اكتب اسم المورد')
            c.execute('INSERT INTO Suppliers (SName,Phone,Note) VALUES (?,?,?)', (d['name'].strip(), d.get('phone', ''), d.get('note', '')))


def save_store_item(d):
    code, old = str(d.get('code', '')).strip(), str(d.get('old_code') or d.get('code', '')).strip()
    with LOCK, db() as c:
        if d.get('del'):
            c.execute('DELETE FROM StoreItems WHERE Code=?', (old,))
            return
        if not code or not str(d.get('name', '')).strip():
            raise Exception('الكود والاسم مطلوبين')
        if old != code and c.execute('SELECT 1 FROM StoreItems WHERE Code=?', (code,)).fetchone():
            raise Exception('الكود مستخدم لصنف تاني')
        vals = (code, d['name'].strip(), d.get('unit', 'kg'), d.get('cat', ''), float(d.get('price') or 0), int(d.get('supid') or 0))
        if c.execute('SELECT 1 FROM StoreItems WHERE Code=?', (old,)).fetchone():
            c.execute('UPDATE StoreItems SET Code=?,IName=?,Unit=?,Cat=?,Price=?,SupID=? WHERE Code=?', vals + (old,))
        else:
            c.execute('INSERT INTO StoreItems (Code,IName,Unit,Cat,Price,SupID) VALUES (?,?,?,?,?,?)', vals)


def save_template(d):
    with LOCK, db() as c:
        if d.get('del'):
            c.execute('DELETE FROM PoTemplates WHERE TID=?', (int(d['id']),))
            return
        name = str(d.get('name', '')).strip()
        if not name or not d.get('items'):
            raise Exception('اكتب اسم القالب وضيف أصناف')
        items = json.dumps([{'code': str(i['code']), 'qty': float(i['qty'])} for i in d['items']], ensure_ascii=False)
        if d.get('id'):
            c.execute('UPDATE PoTemplates SET TName=?,Items=? WHERE TID=?', (name, items, int(d['id'])))
        else:
            c.execute('INSERT INTO PoTemplates (TName,Items) VALUES (?,?)', (name, items))


# ------------------------------------------------------------------ [EDIT-MAP] ★ الدليفري: المناديب + الشفتات + تحميل الأوردرات (v11) ★
# الجداول: Deliveries (المناديب) | DeliveryShifts (شفت المندوب: العهدة والتحصيل والمحاسبة) | Dispatch (كل تحميل أوردر على مندوب) | DeliveryLog (سجل الأحداث)
def _ms():
    return int(time.time() * 1000)


def delivery_log(c, rid, act, user, detail=''):
    c.execute('INSERT INTO DeliveryLog (DID,Act,At,ByUser,Detail) VALUES (?,?,?,?,?)', (rid, act, now_s(), user['u'], str(detail)[:300]))


def dv_state():
    with db() as c:
        riders = [dict(id=r[0], name=r[1], phone=r[2] or '', active=bool(r[3]))
                  for r in c.execute('SELECT DID,DName,Phone,Active FROM Deliveries ORDER BY DName')]
        shifts = {str(r[1]): dict(id=r[0], rid=r[1], start=r[2], cash=r[3] or 0)
                  for r in c.execute("SELECT ShiftID,DID,StartAt,StartCash FROM DeliveryShifts WHERE Status='open'")}
        disp = [dict(id=r[0], sid=r[1], rid=r[2], shift=r[3], outAt=r[4], backAt=r[5], status=r[6])
                for r in c.execute("SELECT DispID,SaleID,DID,ShiftID,OutAt,BackAt,Status FROM Dispatch WHERE Status='out' OR "
                                   "(Status='delivered' AND ShiftID IN (SELECT ShiftID FROM DeliveryShifts WHERE Status='open'))")]
        done = [r[0] for r in c.execute("SELECT DISTINCT SaleID FROM Dispatch WHERE Status IN ('out','delivered')")]
        bday = ensure_business_day(c)
    return dict(riders=riders, shifts=shifts, dispatch=disp, done=done, now=_ms(), businessDay=bday)


def delivery_orders():
    """v16: أوردرات الدليفري الشغالة (مش ملغاة، مش استلام فرع) — لشاشة الدليفري React"""
    with db() as c:
        its = {}
        for r in c.execute("SELECT SID,IName,Qty,Price FROM SaleItems"):
            its.setdefault(r[0], []).append(dict(n=r[1], q=r[2], p=r[3]))
        return [dict(id=r[0], no=r[1], day=r[2], time=r[3], total=r[4] or 0, fee=r[5] or 0,
                     name=r[6] or '', phone=r[7] or '', addr=r[8] or '', state=r[9] or '', items=its.get(r[0], []))
                for r in c.execute("SELECT SID,SNo,SDay,STime,STotal,SFee,CName,CPhone,CAddr,OrderState FROM Sales "
                                   "WHERE SType='delivery' AND Void=0 AND IFNULL(OrderType,'')!='A' ORDER BY SDay,STime")]


def delivery_save(d):
    with LOCK, db() as c:
        if d.get('del'):
            rid = int(d['id'])
            if c.execute('SELECT 1 FROM DeliveryShifts WHERE DID=? LIMIT 1', (rid,)).fetchone():
                c.execute('UPDATE Deliveries SET Active=0 WHERE DID=?', (rid,))  # له تاريخ: بنوقفه بس
            else:
                c.execute('DELETE FROM Deliveries WHERE DID=?', (rid,))
        elif d.get('id'):
            c.execute('UPDATE Deliveries SET DName=?,Phone=?,Active=? WHERE DID=?', (str(d['name']).strip(), d.get('phone', ''), 1 if d.get('active', True) else 0, int(d['id'])))
        else:
            if not str(d.get('name', '')).strip():
                raise Exception('اكتب اسم المندوب')
            c.execute('INSERT INTO Deliveries (DName,Phone,Active) VALUES (?,?,1)', (d['name'].strip(), d.get('phone', '')))


def delivery_checkin(d, user):
    rid, cash = int(d['rid']), round(float(d.get('cash') or 0), 2)
    if cash < 0:
        raise Exception('عهدة غير صحيحة')
    with LOCK, db() as c:
        r = c.execute('SELECT Active FROM Deliveries WHERE DID=?', (rid,)).fetchone()
        if not r or not r[0]:
            raise Exception('المندوب غير موجود أو موقوف')
        if c.execute("SELECT 1 FROM DeliveryShifts WHERE DID=? AND Status='open'", (rid,)).fetchone():
            raise Exception('المندوب محضّر بالفعل (شفته مفتوح)')
        c.execute("INSERT INTO DeliveryShifts (DID,StartAt,StartCash,Status,ShiftDay) VALUES (?,?,?,'open',?)", (rid, now_s(), cash, biz_day(c)))
        delivery_log(c, rid, 'checkin', user, 'عهدة %s' % cash)


def delivery_dispatch(d, user):
    rid, sids = int(d['rid']), list(d.get('sids') or [])
    if not sids:
        raise Exception('اختار أوردر واحد على الأقل')
    with LOCK, db() as c:
        sh = c.execute("SELECT ShiftID FROM DeliveryShifts WHERE DID=? AND Status='open'", (rid,)).fetchone()
        if not sh:
            raise Exception('المندوب مش محضّر - حضّره الأول')
        t = _ms()  # المندوب يقدر ياخد أكتر من أوردر مع بعض
        for sid in sids:
            s = c.execute('SELECT SType,Void,OrderType FROM Sales WHERE SID=?', (sid,)).fetchone()
            if s and (s[2] == 'A' or s[0] == 'pickup'):
                raise Exception('أوردر استلام فرع - بيتسلّم من شاشة «استلام فرع» مش على مندوب')
            if not s or s[0] != 'delivery' or s[1]:
                raise Exception('أوردر مش دليفري أو ملغي')
            if c.execute("SELECT 1 FROM Sales WHERE SID=? AND OrderState='F'", (sid,)).fetchone():
                raise Exception('الأوردر ده اتسلّم قبل كده من شاشة الكاشير')
            if c.execute("SELECT 1 FROM Dispatch WHERE SaleID=? AND Status IN ('out','delivered')", (sid,)).fetchone():
                raise Exception('أوردر متحمّل قبل كده')
            c.execute("INSERT INTO Dispatch (SaleID,DID,ShiftID,OutAt,Status,ByUser) VALUES (?,?,?,?,'out',?)", (sid, rid, sh[0], t, user['u']))
        delivery_log(c, rid, 'dispatch', user, '%d أوردر' % len(sids))
    return {'ok': 1, 'outAt': t}


def delivery_return(d, user):
    rid = int(d['rid'])
    with LOCK, db() as c:
        n = c.execute("UPDATE Dispatch SET Status='delivered',BackAt=? WHERE DID=? AND Status='out'", (_ms(), rid)).rowcount
        if not n:
            raise Exception('المندوب مفيش معاه أوردرات بره')
        delivery_log(c, rid, 'return', user, '%d أوردر اتسلّم' % n)


def delivery_recall(d, user):
    rid = int(d['rid'])
    with LOCK, db() as c:
        n = c.execute("UPDATE Dispatch SET Status='pulled',BackAt=? WHERE DID=? AND Status='out'", (_ms(), rid)).rowcount
        if not n:
            raise Exception('المندوب مفيش معاه أوردرات بره')
        delivery_log(c, rid, 'recall', user, '%d أوردر رجعوا للمعلّقة' % n)


def dispatch_pull(d, user):
    """سحب أوردر واحد بس من المندوب (بالـ DispID) وإرجاعه للمعلّقة"""
    did = d.get('dispid', d.get('did'))
    if did is None:
        raise Exception('رقم الأوردر ناقص')
    did = int(did)
    with LOCK, db() as c:
        r = c.execute("SELECT d.DID FROM Dispatch d JOIN DeliveryShifts s ON s.ShiftID=d.ShiftID WHERE d.DispID=? AND d.Status IN ('out','delivered') AND s.Status='open'", (did,)).fetchone()
        if not r:
            raise Exception('الأوردر مش معاه مندوب (أو شفت المندوب اتقفل)')
        c.execute("UPDATE Dispatch SET Status='pulled',BackAt=? WHERE DispID=?", (_ms(), did))
        delivery_log(c, r[0], 'pull', user, 'DispID %s' % did)


def _dispatch_one(d, user, status, act, refused=0):
    """تسليم/رفض أوردر واحد من اللي مع المندوب (بالـ DispID)"""
    did = d.get('dispid', d.get('did'))
    if did is None:
        raise Exception('رقم الأوردر ناقص')
    did = int(did)
    with LOCK, db() as c:
        r = c.execute("SELECT d.DID FROM Dispatch d JOIN DeliveryShifts s ON s.ShiftID=d.ShiftID WHERE d.DispID=? AND d.Status='out' AND s.Status='open'", (did,)).fetchone()
        if not r:
            raise Exception('الأوردر مش بره مع المندوب (اتسلّم أو اتسحب قبل كده)')
        c.execute("UPDATE Dispatch SET Status=?,BackAt=?,Refused=?,DeliveredAt=? WHERE DispID=?",
                  (status, _ms(), refused, now_s() if status == 'delivered' else None, did))
        delivery_log(c, r[0], act, user, 'DispID %s' % did)


def dispatch_deliver(d, user):
    _dispatch_one(d, user, 'delivered', 'deliver1')


def dispatch_refuse(d, user):
    # الزبون رفض: الأوردر يرجع للمعلّقة (Status=pulled) وبيتعلّم Refused=1 ومش بيدخل في حساب المندوب
    _dispatch_one(d, user, 'pulled', 'refuse', 1)


def mark_delivered(d, user):
    """كاشير: تم التسليم - OrderState=F + EmpNo + DeliveredAt + PayPlace"""
    sid = d.get('sid') or d.get('id')
    if not sid:
        raise Exception('رقم الأوردر مطلوب')
    with LOCK, db() as c:
        r = c.execute("SELECT Void,OrderState,OrderType,SType FROM Sales WHERE SID=?", (sid,)).fetchone()
        if not r:
            raise Exception('الأوردر غير موجود')
        if r[0]:
            raise Exception('OrderState=F لأوردر ملغي = خطأ')
        if r[1] == 'F':
            raise Exception('الأوردر متسلّم قبل كده')
        # resolve EmpNo from Employees by username/fullname match, else null
        empno = user.get('emp')
        otype = r[2] or ('V' if r[3] == 'delivery' else 'C')
        c.execute("UPDATE Sales SET OrderState='F', EmpNo=?, PayPlace=?, OrderAction=COALESCE(NULLIF(OrderAction,''),'') WHERE SID=?",
                  (empno, otype, sid))
        # if delivery dispatch exists, mark delivered
        c.execute("UPDATE Dispatch SET Status='delivered', BackAt=?, DeliveredAt=? WHERE SaleID=? AND Status='out'",
                  (int(time.time()*1000), now_s(), sid))
        log(c, sid, 'deliver', user, 'تم التسليم', {'empNo': empno, 'orderType': otype})
    return {'ok': 1, 'sid': sid, 'empNo': empno}



def delivery_settle(d, user):
    rid, actual = int(d['rid']), round(float(d.get('actual') or 0), 2)
    with LOCK, db() as c:
        sh = c.execute("SELECT ShiftID,StartCash FROM DeliveryShifts WHERE DID=? AND Status='open'", (rid,)).fetchone()
        if not sh:
            raise Exception('المندوب ده مفيش له شفت مفتوح')
        if c.execute("SELECT 1 FROM Dispatch WHERE DID=? AND Status='out'", (rid,)).fetchone():
            raise Exception('المندوب لسه معاه أوردرات بره - اضغط رجع أو إعادة المندوب الأول')
        n, coll, fees = c.execute("SELECT COUNT(*),IFNULL(SUM(s.STotal),0),IFNULL(SUM(s.SFee),0) FROM Dispatch d JOIN SalesAll s ON s.SID=d.SaleID "
                                  "WHERE d.ShiftID=? AND d.Status='delivered'", (sh[0],)).fetchone()
        coll, fees = round(coll, 2), round(fees, 2)
        exp = round((sh[1] or 0) + coll, 2)
        diff = round(actual - exp, 2)
        c.execute("UPDATE DeliveryShifts SET Status='closed',EndAt=?,Collected=?,Expected=?,Actual=?,Diff=?,Orders=?,Fees=?,ClosedBy=?,Note=? WHERE ShiftID=?",
                  (now_s(), coll, exp, actual, diff, n, fees, user['u'], str(d.get('note', ''))[:200], sh[0]))
        delivery_log(c, rid, 'settle', user, 'متوقع %s فعلي %s فرق %s' % (exp, actual, diff))
        return dict(id=sh[0], rid=rid, orders=n, collected=coll, fees=fees, cash=sh[1] or 0, expected=exp, actual=actual, diff=diff)


def shifts_list(frm, to):
    with db() as c:
        return [dict(id=r[0], rid=r[1], rider=r[2], start=r[3], end=r[4] or '', cash=r[5] or 0, orders=r[6] or 0, collected=r[7] or 0, fees=r[8] or 0,
                     expected=r[9] or 0, actual=r[10] or 0, diff=r[11] or 0, by=r[12] or '', note=r[13] or '', status=r[14], day=r[15] or str(r[3])[:10])
                for r in c.execute("SELECT s.ShiftID,s.DID,r.DName,s.StartAt,s.EndAt,s.StartCash,s.Orders,s.Collected,s.Fees,s.Expected,s.Actual,s.Diff,s.ClosedBy,s.Note,s.Status,s.ShiftDay "
                                   "FROM DeliveryShifts s JOIN Deliveries r ON r.DID=s.DID WHERE COALESCE(s.ShiftDay,substr(s.StartAt,1,10))>=? AND COALESCE(s.ShiftDay,substr(s.StartAt,1,10))<=? ORDER BY s.ShiftID DESC", (frm, to))]


def dv_guard_close():
    with db() as c:
        if c.execute("SELECT 1 FROM DeliveryShifts WHERE Status='open' LIMIT 1").fetchone():
            raise Exception('فيه مناديب لسه شفتهم مفتوح - حاسبهم واقفل شفتهم الأول من شاشة الدليفري')


# ------------------------------------------------------------------ backups (v3) - يدوي بس، من غير مسح أوتوماتيكي
BK_DIR = os.path.join(os.path.dirname(DB), 'backups')
BK_RE = re.compile(r'^pos_backup_(?:auto_)?(\d{8})_(\d{6})\.db$')
BK_AUTO_HOUR, BK_AUTO_KEEP = 3, 30  # ★ [EDIT-MAP] ساعة النسخ التلقائي وعدد النسخ المحفوظة


# ★★★ [EDIT-MAP] النسخ الاحتياطي ★★★
def make_backup(auto=False):
    os.makedirs(BK_DIR, exist_ok=True)
    name = ('pos_backup_auto_' if auto else 'pos_backup_') + datetime.now().strftime('%Y%m%d_%H%M%S') + '.db'
    dst = os.path.join(BK_DIR, name)
    src, out = sqlite3.connect(DB, timeout=30), sqlite3.connect(dst)
    try:
        src.backup(out)  # نسخة متسقة حتى لو في بيع شغال
    finally:
        out.close()
        src.close()
    return {'name': name, 'size': os.path.getsize(dst)}


def prune_auto_backups(keep=None):
    # بيمسح النسخ التلقائية القديمة بس (pos_backup_auto_*) ويسيب آخر N - النسخ اليدوي عمره ما بيتمسح من هنا
    keep = keep or BK_AUTO_KEEP
    files = sorted((n for n in os.listdir(BK_DIR) if n.startswith('pos_backup_auto_') and BK_RE.match(n)), reverse=True) if os.path.isdir(BK_DIR) else []
    for n in files[keep:]:
        try:
            os.remove(os.path.join(BK_DIR, n))
        except OSError:
            pass


def auto_backup_loop():
    while True:
        try:
            now = datetime.now()
            if now.hour >= BK_AUTO_HOUR:
                today = now.strftime('%Y%m%d')
                have = os.path.isdir(BK_DIR) and any(n.startswith('pos_backup_auto_' + today) for n in os.listdir(BK_DIR))
                if not have:  # لو الجهاز كان مقفول الساعة 3 بيتاخد أول ما يشتغل
                    make_backup(auto=True)
                    prune_auto_backups()
                    print('Backup تلقائي اتاخد', today)
        except Exception as e:
            print('Backup تلقائي فشل:', e)
        time.sleep(300)


def bk_time(name):
    m = BK_RE.match(name)
    return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S') if m else None


def list_backups():
    if not os.path.isdir(BK_DIR):
        return []
    out = []
    for n in os.listdir(BK_DIR):
        t = bk_time(n)
        if t:
            out.append({'name': n, 'size': os.path.getsize(os.path.join(BK_DIR, n)), 'at': t.strftime('%Y-%m-%d %H:%M:%S')})
    return sorted(out, key=lambda x: x['name'], reverse=True)


def clean_backups(days):
    # بيمسح بس النسخ الأقدم من المدة اللي اتحددت (قرار يدوي)
    days = int(days)
    if days < 1:
        raise Exception('مدة غير صحيحة')
    cut, n = datetime.now() - timedelta(days=days), 0
    for f in list_backups():
        if bk_time(f['name']) < cut:
            os.remove(os.path.join(BK_DIR, f['name']))
            n += 1
    return n


# ------------------------------------------------------------------ الفرع / الشبكة (v5)
def local_ips():
    # بيجيب عناوين IP بتاعة الجهاز على الشبكة عشان تتحط في إعدادات الفرع وتتفتح من الأجهزة التانية
    ips = set()
    try:
        for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(r[4][0])
    except OSError:
        pass
    try:
        so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        so.connect(('10.255.255.255', 1))
        ips.add(so.getsockname()[0])
        so.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith('127.') and not i.startswith('169.254.'))


def site_info():
    with db() as c:
        cfg = get_cfg(c)
    return {'name': cfg.get('name', ''), 'branchName': cfg.get('branchName', ''), 'branchCode': cfg.get('branchCode', ''), 'ver': VERSION}


PAYMENT_CODES = {'cash':1,'visa':2,'mastercard':3,'meeza':4,'wallet':5,'transfer':6,'other':7,'complimentary':8}
def tables_state():
    with db() as c:
        return [dict(no=r[0],area=r[1],status=r[2],waiter=r[3] or '',guests=int(r[4] or 0),updatedAt=r[5] or '',updatedBy=r[6] or '') for r in c.execute('SELECT TableNo,Area,Status,Waiter,Guests,UpdatedAt,UpdatedBy FROM RestaurantTables ORDER BY TableNo')]
def waiters_state():
    with db() as c:
        return [dict(empNo=r[0],name=r[1],username=r[2] or '') for r in c.execute('SELECT EmpNo,Name,UName FROM Employees WHERE Active=1 AND JobNo=4 ORDER BY Name')]
def table_audit(c, no, action, user, old_status='', new_status='', waiter='', guests=0, detail=''):
    c.execute('INSERT INTO TableAudit (TableNo,Action,OldStatus,NewStatus,Waiter,Guests,ByUser,ByEmpNo,At,Detail) VALUES (?,?,?,?,?,?,?,?,?,?)', (int(no),action,old_status,new_status,waiter,int(guests or 0),user.get('u',''),user.get('emp'),now_s(),detail))

def table_audit_list(no=None, limit=500):
    with db() as c:
        q='SELECT AID,TableNo,Action,OldStatus,NewStatus,Waiter,Guests,ByUser,ByEmpNo,At,Detail FROM TableAudit'
        args=[]
        if no is not None: q+=' WHERE TableNo=?'; args.append(int(no))
        q+=' ORDER BY AID DESC LIMIT ?'; args.append(int(limit))
        return [dict(aid=r[0],tableNo=r[1],action=r[2],oldStatus=r[3] or '',newStatus=r[4] or '',waiter=r[5] or '',guests=int(r[6] or 0),byUser=r[7] or '',byEmpNo=r[8],at=r[9] or '',detail=r[10] or '') for r in c.execute(q,args)]

def table_save(d, user):
    no=int(d.get('no')); status=str(d.get('status') or 'open')
    if no<1 or no>200 or status not in ('open','reserved','guest','closed'): raise Exception('بيانات الطاولة غير صحيحة')
    with db() as c:
        old=c.execute('SELECT Status,Waiter,Guests FROM RestaurantTables WHERE TableNo=?',(no,)).fetchone()
        c.execute('INSERT OR IGNORE INTO RestaurantTables (TableNo,Area,Status) VALUES (?,?,?)', (no, str(d.get('area') or ('داخلي' if no<=20 else 'خارجي')), 'closed'))
        c.execute('UPDATE RestaurantTables SET Area=?,Status=?,Waiter=?,Guests=?,UpdatedAt=?,UpdatedBy=? WHERE TableNo=?', (str(d.get('area') or 'داخلي'),status,str(d.get('waiter') or ''),int(d.get('guests') or 0),now_s(),user.get('u',''),no))
        table_audit(c,no,'status_change',user,old[0] if old else '',status,str(d.get('waiter') or ''),int(d.get('guests') or 0), 'area='+str(d.get('area') or ''))
        if status=='guest':
            c.execute('INSERT OR IGNORE INTO TableOrders (TableNo,Status,Waiter,Guests,Items,SentItems,CreatedAt,UpdatedAt,OpenedBy) VALUES (?,?,?,?,?,?,?,?,?)', (no,'open',str(d.get('waiter') or ''),int(d.get('guests') or 0),'[]','[]',now_s(),now_s(),user.get('u','')))
        elif status in ('closed','open','reserved'):
            c.execute("UPDATE TableOrders SET Status=?,UpdatedAt=? WHERE TableNo=? AND Status<>'closed'", ('open' if status=='open' else status,now_s(),no))
    return {'ok':1,'table':next(x for x in tables_state() if x['no']==no)}
def table_admin(d,user):
    act=str(d.get('action') or '')
    with db() as c:
        if act=='add':
            no=int(d.get('no') or 0)
            if no<=0 or c.execute('SELECT 1 FROM RestaurantTables WHERE TableNo=?',(no,)).fetchone(): raise Exception('رقم الطاولة موجود بالفعل')
            c.execute('INSERT INTO RestaurantTables (TableNo,Area,Status,UpdatedAt,UpdatedBy) VALUES (?,?,?,?,?)',(no,str(d.get('area') or 'داخلي'),'closed',now_s(),user.get('u','')))
        elif act=='delete':
            no=int(d.get('no')); 
            if c.execute("SELECT 1 FROM RestaurantTables WHERE TableNo=? AND Status='guest'",(no,)).fetchone(): raise Exception('لا يمكن حذف طاولة عليها ضيف')
            old=c.execute('SELECT Status,Waiter,Guests FROM RestaurantTables WHERE TableNo=?',(no,)).fetchone()
            c.execute('DELETE FROM RestaurantTables WHERE TableNo=?',(no,)); c.execute('DELETE FROM TableOrders WHERE TableNo=?',(no,))
            table_audit(c,no,'delete',user,old[0] if old else '', 'deleted', old[1] if old else '', old[2] if old else 0)
        else: raise Exception('عملية الطاولة غير صحيحة')
    return {'ok':1,'tables':tables_state()}
def table_order_get(no):
    with db() as c:
        r=c.execute('SELECT TableNo,Status,Waiter,Guests,Items,SentItems,CreatedAt,UpdatedAt FROM TableOrders WHERE TableNo=?',(int(no),)).fetchone()
    if not r: return {'no':int(no),'status':'open','waiter':'','guests':0,'items':[],'sentItems':[]}
    return {'no':r[0],'status':r[1],'waiter':r[2] or '','guests':int(r[3] or 0),'items':json.loads(r[4] or '[]'),'sentItems':json.loads(r[5] or '[]'),'createdAt':r[6] or '','updatedAt':r[7] or ''}
def table_order_save(d,user):
    no=int(d.get('no')); items=d.get('items') or []; sent=d.get('sentItems') or []
    with db() as c:
        c.execute('INSERT OR REPLACE INTO TableOrders (TableNo,Status,Waiter,Guests,Items,SentItems,CreatedAt,UpdatedAt,OpenedBy) VALUES (?,?,?,?,?,?,?,?,COALESCE((SELECT OpenedBy FROM TableOrders WHERE TableNo=?),?))', (no,'open',str(d.get('waiter') or ''),int(d.get('guests') or 0),json.dumps(items,ensure_ascii=False),json.dumps(sent,ensure_ascii=False),d.get('createdAt') or now_s(),now_s(),no,user.get('u','')))
        c.execute("UPDATE RestaurantTables SET Status='guest',Waiter=?,Guests=?,UpdatedAt=?,UpdatedBy=? WHERE TableNo=?",(str(d.get('waiter') or ''),int(d.get('guests') or 0),now_s(),user.get('u',''),no))
        table_audit(c,no,'open_order',user,'guest','guest',str(d.get('waiter') or ''),int(d.get('guests') or 0))
    return {'ok':1,'order':table_order_get(no)}
def table_order_close(no,user):
    with db() as c:
        c.execute("UPDATE TableOrders SET Status='closed',UpdatedAt=? WHERE TableNo=?",(now_s(),int(no)))
        table_audit(c,no,'close_order',user,'guest','open','','0')
        c.execute("UPDATE RestaurantTables SET Status='open',Waiter='',Guests=0,UpdatedAt=?,UpdatedBy=? WHERE TableNo=?",(now_s(),user.get('u',''),int(no)))
    return {'ok':1}


# ------------------------------------------------------------------ http
# ★★★ [EDIT-MAP] 📡 كل مسارات الـ API (do_GET / do_POST) ★★★
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, code, obj, ct='application/json'):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ct + '; charset=utf-8')
        # ضغط الردود الكبيرة (زي /api/state بعد شهور من البيانات): الحجم بيقل حوالي 10 مرات والمتصفح بيفكه لوحده
        if ct == 'application/json' and len(b) > 65536 and 'gzip' in (self.headers.get('Accept-Encoding') or ''):
            b = gzip.compress(b, 1)
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def user(self):
        return TOK.get(self.headers.get('X-Token', ''))

    def events(self):
        # بث لحظي (Server-Sent Events): كل جهاز بيسمع لأي أوردر/تعديل بيحصل من جهاز تاني
        q = parse_qs(urlparse(self.path).query)
        if not TOK.get((q.get('t') or [''])[0]):
            return self.out(401, {'error': 'login'})
        sub = queue.Queue(maxsize=500)
        with SUBS_LOCK:
            SUBS.add(sub)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.wfile.write(b'retry: 2000\n\n')
            self.wfile.flush()
            while True:
                try:
                    ev = sub.get(timeout=15)
                    self.wfile.write(('data: ' + json.dumps(ev, ensure_ascii=False) + '\n\n').encode('utf-8'))
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with SUBS_LOCK:
                SUBS.discard(sub)

    def download(self):
        q = parse_qs(urlparse(self.path).query)
        u = TOK.get((q.get('t') or [''])[0])
        name = (q.get('name') or [''])[0]
        if not u or u['role'] != 'admin' or not BK_RE.match(name) or not os.path.isfile(os.path.join(BK_DIR, name)):
            return self.out(403, {'error': 'forbidden'})
        b = open(os.path.join(BK_DIR, name), 'rb').read()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/admin', '/admin/'):  # شاشة الإدارة الجديدة (React) — الشاشة القديمة لسه على /
            fp = os.path.join(REACT_DIR, 'admin.html')
            if os.path.isfile(fp):
                return self.out(200, open(fp, 'rb').read(), 'text/html')
            return self.out(404, {'error': 'admin.html غير موجود — ابني الصورة من جديد (docker compose up -d --build)'})
        if p in ('/react', '/react/') or p.startswith('/react/assets/'):
            rel = 'index.html' if p in ('/react', '/react/') else p[len('/react/'):]
            fp = os.path.abspath(os.path.join(REACT_DIR, rel))
            if fp.startswith(os.path.abspath(REACT_DIR) + os.sep) and os.path.isfile(fp):
                typ = mimetypes.guess_type(fp)[0] or 'application/octet-stream'
                return self.out(200, open(fp, 'rb').read(), typ)
            return self.out(404, {'error': 'React asset not found'})
        if p in ('/', '/index.html'):
            if MODE == 'cashier':
                fp = os.path.join(REACT_DIR, 'index.html')
                if os.path.isfile(fp):
                    return self.out(200, open(fp, 'rb').read(), 'text/html')
            try:
                return self.out(200, open(HTML, 'rb').read(), 'text/html')
            except OSError:
                return self.out(500, {'error': 'pos_istakoza.html not found'})
        if p == '/api/site':
            return self.out(200, site_info())
        if p == '/favicon.ico':
            return self.out(204, b'', 'image/x-icon')
        if p == '/api/events':
            return self.events()
        if p == '/api/backup/download':
            return self.download()
        u = self.user()
        if not u:
            return self.out(401, {'error': 'login'})
        adm = u['role'] == 'admin'  # FIX1: كان غير معرّف فكانت شاشة الفروع بترجع 500
        try:
            if p == '/api/businessday':
                return self.out(200, dict(day=cur_biz_day()))
            if p == '/api/state':
                return self.out(200, dict(get_state(), me=dict(u, perm=emp_perms(u.get('job'),u.get('emp')))))
            if p == '/api/tables' and can(u, 'tables'):
                return self.out(200, tables_state())
            if p == '/api/waiters' and can(u, 'tables'):
                return self.out(200, waiters_state())
            if p == '/api/table/order' and can(u, 'tables'):
                q=parse_qs(urlparse(self.path).query); return self.out(200, table_order_get((q.get('no') or ['0'])[0]))
            if p == '/api/table/audit' and can(u, 'tables'):
                q=parse_qs(urlparse(self.path).query); return self.out(200, table_audit_list((q.get('no') or [None])[0]))
            if p == '/api/users' and u['role'] == 'admin':
                return self.out(200, list_users())
            if p == '/api/permissions' and u['role']=='admin':
                q=parse_qs(urlparse(self.path).query); return self.out(200, permission_rows(int((q.get('emp') or ['0'])[0])))
            if p == '/api/loginlog' and u['role'] == 'admin':
                return self.out(200, login_log())
            if p == '/api/jobs' and u['role'] == 'admin':
                return self.out(200, list_jobs())
            if p == '/api/netinfo' and u['role'] == 'admin':
                return self.out(200, {'ips': local_ips(), 'port': PORT, 'host': socket.gethostname(), 'db': DB, 'html': HTML, 'ver': VERSION})
            if p == '/api/products':
                return self.out(200, products_get())
            if p == '/api/printers' and (adm or can(u, 'printer')):
                return self.out(200, printers_get())
            if p == '/api/stocktake' and can(u, 'stock'):
                q = parse_qs(urlparse(self.path).query)
                return self.out(200, stocktake_get((q.get('month') or [''])[0]))
            if p == '/api/report/stock' and can(u, 'rep'):
                q = parse_qs(urlparse(self.path).query)
                return self.out(200, report_stock((q.get('from') or [''])[0], (q.get('to') or [''])[0]))
            if p == '/api/report/advanced' and can(u, 'rep'):
                q = parse_qs(urlparse(self.path).query)
                return self.out(200, report_advanced(
                    (q.get('type') or [''])[0],
                    (q.get('from') or [''])[0],
                    (q.get('to') or [''])[0],
                    (q.get('month') or [''])[0]))
            if p in ('/api/delivery', '/api/rider') and can(u, 'dlv'):
                return self.out(200, dv_state())
            if p == '/api/delivery/orders' and can(u, 'dlv'):
                return self.out(200, delivery_orders())
            if p in ('/api/delivery/shifts', '/api/rider/shifts') and can(u, 'dlvPay'):
                q = parse_qs(urlparse(self.path).query)
                return self.out(200, shifts_list((q.get('from') or ['0000-00-00'])[0], (q.get('to') or ['9999-99-99'])[0]))
            if p == '/api/store' and can(u, 'po'):
                return self.out(200, store_extra(get_store()))
            if p == '/api/branches' and (adm or can(u, 'po')):
                return self.out(200, list_branches())
            if p == '/api/bo/ping' and (adm or can(u, 'po')):
                q = parse_qs(urlparse(self.path).query)
                return self.out(200, branch_ping((q.get('code') or [''])[0]))
            if p == '/api/bo' and (adm or can(u, 'po')):
                return self.out(200, {'out': bo_list('out'), 'in': bo_list('in')})
            if p == '/api/po' and can(u, 'po'):
                return self.out(200, store_extra(get_store()))
            if p == '/api/backups' and u['role'] == 'admin':
                return self.out(200, {'dir': BK_DIR, 'files': list_backups()})
            if p == '/api/log':
                return self.out(200, get_log((parse_qs(urlparse(self.path).query).get('sid') or [''])[0]))
            self.out(403, {'error': 'forbidden'})
        except Exception as e:
            self.out(500, {'error': str(e)})

    def do_POST(self):
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        except Exception:
            d = {}
        p = self.path
        try:
            if p == '/api/login':
                try:
                    u = login(d.get('u', ''), d.get('p', ''), self.client_address[0])
                except LoginLocked as e:
                    return self.out(429, {'error': str(e), 'locked': e.secs})
                if not u:
                    return self.out(400, {'error': 'اسم المستخدم أو كلمة السر غير صحيحة'})
                t = secrets.token_hex(16)
                TOK[t] = u
                return self.out(200, dict(u, perm=emp_perms(u.get('job'),u.get('emp')), token=t, mode=MODE))
            # ★ v15: قناة الفروع — مفتاح مشترك مش توكن موظف (لازم تكون قبل فحص التوكن) ★
            if p in ('/api/peer/transfer', '/api/peer/confirm', '/api/peer/ask', '/api/peer/cancel'):
                if self.headers.get('X-Branch-Key') != branch_key():
                    return self.out(403, {'error': 'bad branch key'})
                try:
                    if p == '/api/peer/transfer':
                        r = peer_transfer(d)
                    elif p == '/api/peer/confirm':
                        r = peer_confirm(d)
                    elif p == '/api/peer/cancel':
                        r = peer_cancel(d)
                    else:
                        r = peer_ask(d)
                    return self.out(200, dict(r, ok=1))
                except Forbidden as e:
                    return self.out(403, {'error': str(e)})
                except Exception as e:
                    return self.out(400, {'error': str(e)})
            u = self.user()
            if not u:
                return self.out(401, {'error': 'login'})
            adm = u['role'] == 'admin'
            cid = self.headers.get('X-Cid', '')
            if p == '/api/logout':
                TOK.pop(self.headers.get('X-Token'), None)
            elif p == '/api/state':
                save_state(d, u['role'] if adm or can(u, 'price') else 'none')
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/sale':
                r = add_sale(d, u)
                if not r.get('dup'):
                    broadcast({'t': 'sale', 'sale': d, 'who': u['full'] or u['u'], 'cid': cid})
                return self.out(200, r)
            elif p == '/api/table' and can(u, 'tables'):
                r=table_save(d,u); broadcast({'t':'table','table':r['table'],'cid':cid}); return self.out(200,r)
            elif p == '/api/table/admin' and u['role']=='admin':
                r=table_admin(d,u); broadcast({'t':'reload','cid':cid}); return self.out(200,r)
            elif p == '/api/table/order' and can(u, 'tables'):
                if d.get('action')=='close': r=table_order_close(d.get('no'),u)
                else: r=table_order_save(d,u)
                broadcast({'t':'table','cid':cid}); return self.out(200,r)
            elif p in ('/api/delivery', '/api/rider') and adm:
                delivery_save(d)
                broadcast({'t': 'reload', 'cid': cid})
            elif p in ('/api/delivery/checkin', '/api/rider/checkin') and can(u, 'dlv'):
                delivery_checkin(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p in ('/api/delivery/dispatch', '/api/rider/dispatch') and can(u, 'dlv'):
                r = delivery_dispatch(d, u)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, r)
            elif p == '/api/sale/deliver' and can(u, 'dlv'):
                r = mark_delivered(d, u)
                broadcast({'t': 'deliver', 'sid': d.get('sid') or d.get('id'), 'cid': cid})
                return self.out(200, r)
            elif p in ('/api/delivery/return', '/api/rider/return') and can(u, 'dlv'):
                delivery_return(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p in ('/api/delivery/recall', '/api/rider/recall') and can(u, 'dlv'):
                delivery_recall(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/dispatch/deliver' and can(u, 'dlv'):
                dispatch_deliver(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/dispatch/refuse' and can(u, 'dlv'):
                dispatch_refuse(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/dispatch/pull' and can(u, 'dlv'):
                dispatch_pull(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p in ('/api/delivery/settle', '/api/rider/settle') and can(u, 'dlvPay'):
                r = delivery_settle(d, u)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, {'ok': 1, 'shift': r})
            elif p == '/api/close' and can(u, 'close'):
                dv_guard_close()
                with LOCK, db() as c:
                    business_day=biz_day(c)
                    for i in d['ids']: c.execute('UPDATE Sales SET Closed=1 WHERE SID=?', (i,))
                    result=archive_close_batch(c,d['ids'],u,business_day)
                    c.execute('INSERT OR IGNORE INTO day_locks (day,locked_at,locked_by) VALUES (?,?,?)',(business_day,now_s(),u['u']))
                    next_day = advance_business_day(c, business_day, u)
                    try:
                        summary = summary_statement(business_day, c)
                    except Exception:
                        summary = {'day': business_day}
                try:
                    backup_after_close(business_day)
                except Exception as e:
                    print('close backup:', e)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, {'ok':1,'archive':result,'day':business_day,'nextDay':next_day,'summary':summary})
            elif p == '/api/void' and can(u, 'void'):
                do_void(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/edit' and can(u, 'edit'):
                do_edit(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/return' and can(u, 'return'):
                r = do_return(d, u)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, {'ok': 1, 'ret': r})
            elif adm and p == '/api/return/pay':
                do_return_pay(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/stocktake/save' and can(u, 'stock'):
                stocktake_save(d)
            elif p == '/api/stocktake/close' and can(u, 'stock'):
                stocktake_close(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif adm and p == '/api/product/save':
                r = product_save(d)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, r)
            elif adm and p == '/api/printer/save':
                r = printer_save(d)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, r)
            elif adm and p == '/api/printer/routing':
                r = printer_routing_save(d)
                broadcast({'t': 'reload', 'cid': cid})
                return self.out(200, r)
            elif adm and p == '/api/recipe':
                recipe_save(d)
                broadcast({'t': 'reload', 'cid': cid})
            elif adm and p == '/api/stocktake/reopen':
                stocktake_reopen(d)
            elif p == '/api/po' and can(u, 'po'):
                return self.out(200, {'ok': 1, 'order': do_po(d, u)})
            elif p == '/api/branch/save' and (adm or can(u, 'po')):
                save_branch(d)
                broadcast({'t': 'reload', 'cid': cid})
            elif adm and p == '/api/branch/del':
                del_branch(d['code'])
            elif p == '/api/bo' and (adm or can(u, 'po')):
                return self.out(200, bo_save(d, u))
            elif p == '/api/bo/send' and (adm or can(u, 'po')):
                bo_send(d.get('boid'), u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/bo/receive' and (adm or can(u, 'po')):
                bo_receive(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/bo/resend' and (adm or can(u, 'po')):
                return self.out(200, bo_resend_confirms())
            elif p == '/api/bo/cancel' and (adm or can(u, 'po')):
                bo_cancel(d)
            elif p == '/api/branch/key' and adm:
                set_branch_key(d)
            elif p == '/api/po/send' and can(u, 'po'):
                return self.out(200, do_po_send(d))
            elif p == '/api/po/stage' and can(u, 'po'):
                do_po_stage(d, u)
                broadcast({'t': 'reload', 'cid': cid})
            elif p == '/api/po/mark' and can(u, 'po'):
                po_mark(d['id'], 'sent' if d.get('status') == 'sent' else 'pending', str(d.get('err', '')))
            elif adm and p == '/api/po/del':
                with LOCK, db() as c:
                    c.execute('DELETE FROM POItems WHERE POID=?', (d['id'],))
                    c.execute('DELETE FROM POrders WHERE POID=?', (d['id'],))
            elif adm and p == '/api/store/supplier':
                save_supplier(d)
            elif adm and p == '/api/store/item':
                save_store_item(d)
            elif p == '/api/store/template' and can(u, 'po'):
                save_template(d)
            elif p == '/api/print':
                do_print(d)
            elif adm and p == '/api/permissions':
                if d.get('action')=='copy': r=copy_employee_permissions(d,u)
                else: r=save_employee_permissions(d,u)
                return self.out(200, dict(r,ok=1))
            elif adm and p == '/api/users':
                return self.out(200, dict(save_user(d), ok=1))
            elif adm and p == '/api/backup':
                return self.out(200, dict(make_backup(), ok=1))
            elif adm and p == '/api/backup/clean':
                return self.out(200, {'ok': 1, 'deleted': clean_backups(d.get('days', 30))})
            elif adm and p == '/api/reset':
                reset()
                broadcast({'t': 'reload', 'cid': cid})
            elif adm and p == '/api/import':
                do_import(d)
                broadcast({'t': 'reload', 'cid': cid})
            else:
                return self.out(403, {'error': 'forbidden'})
            self.out(200, {'ok': 1})
        except Forbidden as e:
            self.out(403, {'error': str(e)})
        except Exception as e:
            print('POST ERROR:', repr(e))
            self.out(400, {'error': str(e)})


def open_pos(url):
    # بيفتح البرنامج كنافذة تطبيق بطباعة صامتة (--kiosk-printing) لو Chrome/Edge موجود، وإلا المتصفح العادي
    if os.name == 'nt':
        pf = [os.environ.get(k, '') for k in ('ProgramFiles', 'ProgramFiles(x86)', 'LocalAppData')]
        cands = [os.path.join(pf[0], 'Google', 'Chrome', 'Application', 'chrome.exe'),
                 os.path.join(pf[1], 'Google', 'Chrome', 'Application', 'chrome.exe'),
                 os.path.join(pf[2], 'Google', 'Chrome', 'Application', 'chrome.exe'),
                 os.path.join(pf[1], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                 os.path.join(pf[0], 'Microsoft', 'Edge', 'Application', 'msedge.exe')]
        prof = os.path.join(os.environ.get('LOCALAPPDATA', APP_DIR), 'IstakozaPOS_Browser')
        for b in cands:
            if os.path.isfile(b):
                try:
                    subprocess.Popen([b, '--kiosk-printing', '--user-data-dir=' + prof, '--app=' + url])
                    return
                except OSError:
                    pass
    webbrowser.open(url)


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def port_busy():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=1):
            return True
    except OSError:
        return False


if __name__ == '__main__':
    if os.name == 'nt':
        os.system('chcp 65001 >nul')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    url = 'http://localhost:%d' % PORT
    if port_busy():
        print('البرنامج شغال بالفعل (البورت %d مستخدم) - هفتحه في المتصفح' % PORT)
        if '--no-browser' not in sys.argv:
            open_pos(url)
        sys.exit(0)
    init()
    print('السيرفر شغال: %s   (من جهاز تاني: http://IP-الجهاز:%d)' % (url, PORT))
    print('مكان البيانات:', DB)
    print('الصفحة:', HTML)
    print('الدخول الأول: admin / admin  - غيّر كلمة السر من الإعدادات')
    print('متقفلش الشاشة السودا دي طول ما بتشتغل.')
    if '--no-browser' not in sys.argv:
        threading.Timer(1.2, lambda: open_pos(url)).start()
    threading.Thread(target=auto_backup_loop, daemon=True).start()
    threading.Thread(target=live_relay_loop, daemon=True).start()
    Srv(('0.0.0.0', PORT), H).serve_forever()
