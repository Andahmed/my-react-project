import React, {useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {
  CreditCard, Gift, Percent, Armchair, Send, LogIn, Plus, Minus,
  Truck, Store, ShoppingCart, LogOut, RefreshCw, Trash2
} from 'lucide-react';
import './style.css';

type Product = {id: number; name: string; cat: string; unit: string; price: number; icon?: string; code?: string; hidden?: boolean; printer_ids?: number[]};
type Line = Product & {qty: number};
type Table = {no: number; area: string; status: string; waiter?: string; guests?: number};
type Waiter = {empNo: number; name: string; username?: string};
type State = {products: Product[]; me: any; cfg: any; printers?: any[]; stock?: Record<string, number>; sales?: any[]};
type Pay = [string, string, number];
type OrderType = 'cash' | 'table' | 'delivery' | 'pickup';
type Adjustment = {
  opCode: number; opName: string; scope: 'invoice' | 'item'; pids: number[];
  amount: number; percent: number; note: string;
};
type PaymentLine = {method: string; amount: string};

const PAY: Pay[] = [
  ['cash', 'Cash', 1], ['visa', 'Visa', 2], ['mastercard', 'Mastercard', 3],
  ['meeza', 'Meeza', 4], ['wallet', 'E-Wallet', 5], ['transfer', 'Bank Transfer', 6], ['other', 'Other', 7],
];

async function api<T = any>(url: string, body?: any): Promise<T> {
  const token = sessionStorage.getItem('istakoza-token') || '';
  const headers: Record<string, string> = {};
  if (token) headers['X-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(url, {
    method: body !== undefined ? 'POST' : 'GET',
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) {
    sessionStorage.removeItem('istakoza-token');
    throw new Error('انتهت جلسة الدخول');
  }
  if (!r.ok) throw new Error((d as any).error || 'حدث خطأ');
  return d as T;
}

const money = (n: number) => `${(n || 0).toFixed(2)} ج`;
const uid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

/** ESC/POS بسيط + قص — للطابعات الشبكية عبر /api/print */
function escPosTicket(sale: any, cfg: any, kitchen = false): Uint8Array {
  const enc = new TextEncoder();
  const chunks: number[] = [];
  const push = (a: number[] | Uint8Array) => { for (let i = 0; i < a.length; i++) chunks.push(a[i]); };
  const text = (s: string) => push(enc.encode(s + '\n'));
  push([0x1b, 0x40]); // init
  push([0x1b, 0x61, 0x01]); // center
  text(cfg?.name || 'Istakoza');
  text(kitchen ? '--- KITCHEN ---' : '--- RECEIPT ---');
  push([0x1b, 0x61, 0x00]); // left
  text(`#${String(sale.no ?? '').padStart(4, '0')}  ${sale.day || ''} ${sale.time || ''}`);
  text(`Type: ${sale.type || sale.orderType || 'cash'}`);
  if (sale.tableNo) text(`Table: ${sale.tableNo}  Waiter: ${sale.waiter || ''}`);
  if (sale.cust?.name) text(`Cust: ${sale.cust.name} ${sale.cust.phone || ''}`);
  text('-------------------------------');
  for (const it of sale.items || []) {
    const line = kitchen
      ? `${it.name}  x${it.qty}`
      : `${it.name}  x${it.qty}  ${(it.qty * it.price).toFixed(2)}`;
    text(line);
  }
  text('-------------------------------');
  if (!kitchen) {
    if (sale.disc) text(`Disc: ${Number(sale.disc).toFixed(2)}`);
    if (sale.fee) text(`Delivery: ${Number(sale.fee).toFixed(2)}`);
    push([0x1b, 0x61, 0x01]);
    text(`TOTAL  ${Number(sale.total).toFixed(2)}`);
    push([0x1b, 0x61, 0x00]);
    const pay = (sale.payments && sale.payments[0]?.method) || 'cash';
    text(`Pay: ${pay}`);
  }
  if (sale.comment) text(String(sale.comment).slice(0, 80));
  text(cfg?.foot || 'Thank you');
  // feed + full cut
  const feed = Math.max(4, Math.min(20, Math.round((Number(cfg?.feed) || 10) / 3) + 2));
  push([0x1b, 0x64, feed, 0x1d, 0x56, 0x00, 0x1b, 0x69]);
  return Uint8Array.from(chunks);
}

function b64(u: Uint8Array) {
  let s = '';
  for (let i = 0; i < u.length; i += 8192) s += String.fromCharCode(...u.subarray(i, i + 8192));
  return btoa(s);
}

async function printNetSale(sale: any, cfg: any, configuredPrinters: any[], toast: (m: string) => void, target: 'all' | 'customer' | 'kitchen' = 'all') {
  const printers = (configuredPrinters?.length ? configuredPrinters : (cfg?.printers || [])).filter((x: any) => (x.enabled ?? x.on) && x.ip);
  if (!printers.length) return;
  await Promise.all(printers.filter((pr: any) => target === 'all' || (target === 'kitchen' ? pr.type === 'kitchen' : pr.type !== 'kitchen')).map(async (pr: any) => {
    try {
      const kitchen = pr.type === 'kitchen';
      let items = sale.items || [];
      if (kitchen) {
        const groups = Array.isArray(pr.groups) ? pr.groups : (pr.cats || []);
        items = items.filter((it: any) => {
          const override = Array.isArray(it.printer_ids) ? it.printer_ids.map(Number) : [];
          if (override.length) return override.includes(Number(pr.no));
          return groups.length === 0 || groups.includes(it.cat);
        });
        if (!items.length) return;
      }
      const data = b64(escPosTicket({...sale, items}, cfg, kitchen));
      await api('/api/print', {ip: pr.ip, port: pr.port || 9100, data});
      toast(`🖨️ طُبع على ${pr.name || pr.ip}`);
    } catch (e: any) {
      toast(`⚠️ طباعة ${pr.name || pr.ip}: ${e.message}`);
    }
  }));
}


function Login({onDone}: {onDone: () => void}) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api<any>('/api/login', {u, p});
      sessionStorage.setItem('istakoza-token', r.token);
      onDone();
    } catch (e: any) {
      setErr(e.message || 'فشل الدخول');
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login">
      <div className="login-card">
        <h1>إستاكوزا POS</h1>
        <p>واجهة الكاشير React — مربوطة بقاعدة SQL</p>
        <input placeholder="كود الموظف" value={u} onChange={e => setU(e.target.value)} autoFocus />
        <input type="password" placeholder="كلمة السر" value={p} onChange={e => setP(e.target.value)} onKeyDown={e => e.key === 'Enter' && go()} />
        <button className="primary" disabled={busy || !u} onClick={go}><LogIn size={18} /> دخول</button>
        {err && <b className="error">{err}</b>}
      </div>
    </main>
  );
}

function App() {
  const [authed, setAuthed] = useState(!!sessionStorage.getItem('istakoza-token'));
  const [state, setState] = useState<State | null>(null);
  const [cart, setCart] = useState<Line[]>([]);
  const [cat, setCat] = useState('الكل');
  const [search, setSearch] = useState('');
  const [orderType, setOrderType] = useState<OrderType>('cash');
  const [payOpen, setPayOpen] = useState(false);
  const [payments, setPayments] = useState<PaymentLine[]>([]);
  const [options, setOptions] = useState(false);
  const [tablesOpen, setTablesOpen] = useState(false);
  const [tables, setTables] = useState<Table[]>([]);
  const [waiters, setWaiters] = useState<Waiter[]>([]);
  const [tableNo, setTableNo] = useState<number | null>(null);
  const [tableMeta, setTableMeta] = useState({waiter: '', guests: 0});
  const [tableSetup, setTableSetup] = useState<Table | null>(null);
  const [cust, setCust] = useState({name: '', phone: '', addr: ''});
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [adjOpen, setAdjOpen] = useState(false);
  const [adjType, setAdjType] = useState<'discount' | 'complimentary'>('discount');
  const [adjScope, setAdjScope] = useState<'invoice' | 'item'>('invoice');
  const [adjPids, setAdjPids] = useState<number[]>([]);
  const [adjMode, setAdjMode] = useState<'amount' | 'percent'>('amount');
  const [adjValue, setAdjValue] = useState(0);
  const [adjustment, setAdjustment] = useState<Adjustment | null>(null);
  const [weightProduct, setWeightProduct] = useState<Product | null>(null);
  const [weightGrams, setWeightGrams] = useState('');
  const [area, setArea] = useState('');
  const [lastNo, setLastNo] = useState<number | null>(null);
  const [ordersOpen, setOrdersOpen] = useState(false);
  const [pickupOpen, setPickupOpen] = useState(false);
  const [cashDeliveryOpen, setCashDeliveryOpen] = useState(false);
  const [selectedSale, setSelectedSale] = useState<any>(null);
  const [orderQuery, setOrderQuery] = useState('');
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnSale, setReturnSale] = useState<any>(null);
  const [returnReason, setReturnReason] = useState('أوردر غلط');
  const [managerPw, setManagerPw] = useState('');
  const [returnQtys, setReturnQtys] = useState<Record<number, string>>({});
  const [editSale, setEditSale] = useState<any>(null);
  const [editItems, setEditItems] = useState<any[]>([]);
  const [editReason, setEditReason] = useState('');

  const toast = (m: string) => { setNotice(m); window.setTimeout(() => setNotice(''), 4000); };

  const load = async () => {
    try {
      const s = await api<State>('/api/state');
      setState(s);
    } catch (e: any) {
      if (String(e.message).includes('جلسة')) setAuthed(false);
      setState(null);
      toast(e.message);
    }
  };

  useEffect(() => { if (authed) load(); }, [authed]);

  const can = (perm: string) =>
    state?.me?.role === 'admin' || Number(state?.me?.perm?.[perm] || 0) === 1;

  const products = useMemo(() => {
    const list = (state?.products || []).filter(p => !p.hidden);
    return list.filter(p =>
      (cat === 'الكل' || p.cat === cat) &&
      (!search || p.name.includes(search) || String(p.code || '').includes(search))
    );
  }, [state, cat, search]);

  const cats = useMemo(() => {
    const set = new Set((state?.products || []).filter(p => !p.hidden).map(p => p.cat).filter(Boolean));
    return ['الكل', ...Array.from(set)];
  }, [state]);

  const areas = useMemo(() => Array.from(new Set(tables.map(t => t.area).filter(Boolean))), [tables]);
  useEffect(() => { if (!area && areas.length) setArea(areas[0]); }, [areas, area]);

  const payMethods = PAY.filter(x => can(x[0]) || state?.me?.role === 'admin');
  const subtotal = cart.reduce((a, x) => a + x.qty * x.price, 0);
  const disc = adjustment?.amount || 0;
  const fee = orderType === 'delivery' ? Number(state?.cfg?.fee || 0) : 0;
  const total = Math.max(0, subtotal - disc + fee);

  const add = (p: Product) => {
    if (p.unit === 'kg') { setWeightProduct(p); setWeightGrams(''); return; }
    setCart(c => {
    const x = c.find(i => i.id === p.id);
    return x ? c.map(i => i.id === p.id ? {...i, qty: i.qty + 1} : i) : [...c, {...p, qty: 1}];
    });
  };
  const addWeight = (grams: number) => {
    if (!weightProduct || !(grams > 0)) return toast('أدخل وزنًا صحيحًا');
    const qty = grams / 1000;
    setCart(c => {
      const x = c.find(i => i.id === weightProduct.id);
      return x ? c.map(i => i.id === weightProduct.id ? {...i, qty: i.qty + qty} : i) : [...c, {...weightProduct, qty}];
    });
    setWeightProduct(null);
  };
  const change = (id: number, d: number) => setCart(c =>
    c.map(i => i.id === id ? {...i, qty: i.qty + d} : i).filter(i => i.qty > 0)
  );
  const clearCart = () => {
    setCart([]); setAdjustment(null); setTableNo(null);
    setTableMeta({waiter: '', guests: 0}); setCust({name: '', phone: '', addr: ''}); setNote('');
  };

  const loadTables = async () => {
    try {
      const [ts, ws] = await Promise.all([api<Table[]>('/api/tables'), api<Waiter[]>('/api/waiters')]);
      setTables(ts || []); setWaiters(ws || []);
    } catch (e: any) { toast(e.message); }
  };

  useEffect(() => {
    if (!tablesOpen) return;
    loadTables();
    const t = window.setInterval(loadTables, 5000);
    return () => window.clearInterval(t);
  }, [tablesOpen]);

  const openTable = async (t: Table) => {
    if (t.status === 'closed') return toast('هذه الطاولة مغلقة');
    if (t.status === 'guest') {
      try {
        const o = await api<any>(`/api/table/order?no=${t.no}`);
        setCart((o.items || []).map((x: any) => ({
          id: x.id, name: x.name, cat: x.cat || '', unit: x.unit || 'pc', price: x.price, qty: x.qty,
        })));
        setTableMeta({waiter: o.waiter || t.waiter || '', guests: o.guests || t.guests || 0});
        setTableNo(t.no); setOrderType('table'); setTablesOpen(false);
        toast(`تم فتح أوردر الطاولة ${t.no}`);
      } catch (e: any) { toast(e.message); }
    } else setTableSetup(t);
  };

  const confirmOpen = async () => {
    if (!tableSetup || !tableMeta.waiter || !tableMeta.guests) return toast('اختر الويتر وعدد الأشخاص');
    try {
      await api('/api/table/order', {
        no: tableSetup.no, waiter: tableMeta.waiter, guests: tableMeta.guests, items: [], sentItems: [],
      });
      setTableNo(tableSetup.no); setCart([]); setOrderType('table');
      setTableSetup(null); setTablesOpen(false);
      toast(`تم فتح الطاولة ${tableSetup.no}`);
      loadTables();
    } catch (e: any) { toast(e.message); }
  };

  const sendKitchen = async () => {
    if (!tableNo) return toast('أرسل للمطبخ من طاولة مفتوحة');
    if (!cart.length) return toast('الفاتورة فارغة');
    setBusy(true);
    try {
      const items = cart.map(x => ({id: x.id, name: x.name, unit: x.unit, qty: x.qty, price: x.price, cat: x.cat}));
      await api('/api/table/order', {no: tableNo, waiter: tableMeta.waiter, guests: tableMeta.guests, items, sentItems: items});
      toast('تم إرسال الطلب للمطبخ');
    } catch (e: any) { toast(e.message); }
    finally { setBusy(false); }
  };

  const applyAdjustment = () => {
    const selected = cart.filter(x => adjPids.includes(x.id));
    const base = adjScope === 'invoice' ? subtotal : selected.reduce((a, x) => a + x.qty * x.price, 0);
    if (!base || (adjScope === 'item' && !adjPids.length)) return toast('اختر صنفاً واحداً على الأقل');
    const value = adjType === 'complimentary' ? base : (adjMode === 'percent' ? base * Number(adjValue || 0) / 100 : Number(adjValue || 0));
    if (adjType === 'discount' && value <= 0) return toast('أدخل قيمة صحيحة');
    const amount = Math.min(base, value);
    setAdjustment({
      opCode: adjType === 'complimentary' ? 20 : 10,
      opName: adjType === 'complimentary' ? 'Complimentary' : 'Discount',
      scope: adjScope, pids: adjScope === 'item' ? adjPids : [],
      amount, percent: adjMode === 'percent' ? Number(adjValue || 0) : 0,
      note: adjScope === 'item' ? `على ${adjPids.length} صنف` : 'على الفاتورة',
    });
    setAdjOpen(false);
    toast(adjType === 'complimentary' ? 'تم تطبيق Complimentary' : 'تم تطبيق الخصم');
  };

  const completePay = async (lines: PaymentLine[]) => {
    const sum = Math.round(lines.reduce((a, x) => a + (Number(x.amount) || 0), 0) * 100) / 100;
    if (!lines.length || lines.some(x => !(Number(x.amount) > 0))) return toast('أدخل مبلغًا صحيحًا لكل طريقة دفع');
    if (Math.abs(sum - total) > 0.01) return toast(`مجموع طرق الدفع يجب أن يساوي ${money(total)} (الحالي ${money(sum)})`);
    if (!cart.length) return toast('الفاتورة فارغة');
    for (const x of lines) if (!can(x.method) && state?.me?.role !== 'admin') return toast('طريقة الدفع غير مسموحة');
    if (orderType === 'delivery' && (!cust.name || !cust.phone)) return toast('أدخل اسم وهاتف العميل');
    if (orderType === 'table' && !tableNo) return toast('اختر طاولة أولاً');
    setBusy(true);
    try {
      const payload: any = {
        id: uid(),
        type: orderType === 'table' ? 'table' : orderType,
        orderType: orderType === 'cash' ? 'C' : orderType,
        items: cart.map(x => ({id: x.id, name: x.name, cat: x.cat, unit: x.unit, qty: x.qty, price: x.price, printer_ids: x.printer_ids || []})),
        sub: subtotal, disc, tax: 0, fee, total,
        comment: note || `React POS · ${orderType}`,
        adjustment: adjustment || undefined,
        payments: lines.map(x => ({method: x.method, code: PAY.find(p => p[0] === x.method)?.[2] || 1, amount: Number(x.amount)})),
      };
      if (orderType === 'table' || tableNo) {
        payload.tableNo = tableNo;
        payload.waiter = tableMeta.waiter;
        payload.guests = tableMeta.guests;
      }
      if (orderType === 'delivery' || orderType === 'pickup') payload.cust = {...cust};
      const r = await api<any>('/api/sale', payload);
      if (tableNo) { try { await api('/api/table/order', {action: 'close', no: tableNo}); } catch {} }
      setLastNo(r.no ?? null);
      const printed = {
        ...payload, no: r.no, id: r.id || payload.id,
        day: new Date().toISOString().slice(0, 10),
        time: new Date().toLocaleTimeString('ar-EG'),
      };
      toast(`تم الحفظ — فاتورة #${r.no ?? '—'} · ${money(total)}`);
      try { await printNetSale(printed, state?.cfg || {}, state?.printers || [], toast); } catch {}
      clearCart(); setPayOpen(false); setPayments([]); setOrderType('cash');
      await load();
    } catch (e: any) { toast(e.message); }
    finally { setBusy(false); }
  };
  const payCashNow = () => completePay([{method: 'cash', amount: String(total)}]);
  const openPayments = () => { if (!cart.length) return toast('الفاتورة فارغة'); setPayments([{method: payMethods[0]?.[0] || 'cash', amount: String(total)}]); setPayOpen(true); };
  const setPayment = (i: number, patch: Partial<PaymentLine>) => setPayments(xs => xs.map((x, n) => n === i ? {...x, ...patch} : x));
  const addPayment = () => {
    const used = payments.reduce((a, x) => a + (Number(x.amount) || 0), 0);
    setPayments(xs => [...xs, {method: payMethods.find(x => x[0] !== xs[xs.length - 1]?.method)?.[0] || 'visa', amount: String(Math.max(0, total - used))}]);
  };
  const orders = (state?.sales || []).filter((s: any) => !orderQuery || String(s.no).includes(orderQuery) || String(s.cust?.name || '').includes(orderQuery) || String(s.cust?.phone || '').includes(orderQuery)).sort((a: any, b: any) => String(b.id).localeCompare(String(a.id)));
  const markSaleDelivered = async (s: any) => {
    try {
      await api('/api/sale/deliver', {sid: s.id});
      toast(`تم تسجيل تسليم الفاتورة #${s.no}`);
      await load();
    } catch (e: any) {
      toast(e.message || 'تعذر تسجيل التسليم');
      await load();
    }
  };

  const pickupOrders = (state?.sales || []).filter((s: any) => s.type === 'pickup' && !s.void && s.orderState !== 'F')
    .sort((a: any, b: any) => String(a.id).localeCompare(String(b.id)));
  const cashOrdersToDeliver = (state?.sales || []).filter((s: any) => !s.void && s.by === state?.me?.u && s.orderState !== 'F' && s.type !== 'delivery' && s.type !== 'pickup')
    .sort((a: any, b: any) => String(a.id).localeCompare(String(b.id)));

  const voidSale = async (s: any) => {
    if (!can('void')) return toast('غير مصرح بإلغاء الفواتير');
    const reason = window.prompt(`سبب إلغاء الفاتورة #${s.no}`); if (!reason) return;
    try { await api('/api/void', {id: s.id, reason}); toast('تم إلغاء الفاتورة'); await load(); } catch (e: any) { toast(e.message); }
  };
  const reprintSale = async (sale: any, target: 'customer' | 'kitchen') => {
    if (!can('reprint')) return toast('غير مصرح لك بإعادة الطباعة');
    try {
      const printers = (state?.printers || state?.cfg?.printers || []).filter((p: any) => (p.enabled ?? p.on) && p.ip && (target === 'kitchen' ? p.type === 'kitchen' : p.type !== 'kitchen'));
      if (printers.length) {
        await printNetSale(sale, state?.cfg || {}, state?.printers || [], toast, target);
      } else {
        const kitchen = target === 'kitchen';
        const esc = (v: any) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c] || c));
        const cfg = state?.cfg || {};
        const rows = (sale.items || []).map((it: any) => `<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px dashed #aaa"><span>${esc(it.name)} × ${esc(it.qty)}</span>${kitchen ? '' : `<b>${money(Number(it.qty || 0) * Number(it.price || 0))}</b>`}</div>`).join('');
        const flags: Record<string, boolean> = {delivery: sale.type === 'delivery' || sale.type === 'pickup', comment: !!sale.comment, disc: Number(sale.disc || 0) > 0, fee: Number(sale.fee || 0) > 0, phone: !!cfg.phone, showCashier: !!cfg.rcpCashier};
        const defaultCustomer = `<div style="text-align:center"><h2>{{name}}</h2><div>{{phone}}</div></div><div style="text-align:center;font-size:20px;font-weight:bold">أوردر #{{no}}</div><div style="text-align:center">{{type}} · {{date}} {{time}}</div>{{#delivery}}<hr><div>العميل: {{custName}}</div><div>الهاتف: {{custPhone}}</div><div>العنوان: {{custAddr}}</div>{{/delivery}}<hr>{{items}}<hr><div>المجموع: {{subtotal}} ج</div><div>الخصم: {{discount}} ج</div><div>التوصيل: {{feeAmt}} ج</div><div style="font-size:18px;font-weight:bold">الإجمالي: {{total}} ج</div>{{#comment}}<p>{{comment}}</p>{{/comment}}<div style="text-align:center;margin-top:12px">{{footer}}</div>`;
        const defaultKitchen = `<div style="text-align:center;font-size:22px;font-weight:bold">أوردر #{{no}}</div><div style="text-align:center;font-size:18px">{{type}} · {{time}}</div>{{#delivery}}<div>العميل: {{custName}} · {{custPhone}}</div><div>{{custAddr}}</div>{{/delivery}}<hr>{{items}}<hr>{{comment}}`;
        let template = String(kitchen ? (cfg.tplKitchen || defaultKitchen) : (cfg.tplCustomer || defaultCustomer));
        template = template.replace(/\{\{([#^])(\w+)\}\}([\s\S]*?)\{\{\/\2\}\}/g, (_m, op, key, body) => ((op === '#') === !!flags[key] ? body : ''));
        const values: Record<string,string> = {name:esc(cfg.name || 'إستاكوزا POS'),phone:esc(cfg.phone || ''),no:esc(sale.no),type:esc(sale.type === 'delivery' ? 'دليفري' : sale.type === 'pickup' ? 'استلام فرع' : 'كاشير'),date:esc(sale.day || ''),time:esc(sale.time || ''),custName:esc(sale.cust?.name || ''),custPhone:esc(sale.cust?.phone || ''),custAddr:esc(sale.cust?.addr || ''),items:rows,subtotal:money(Number(sale.sub || 0)),discount:money(Number(sale.disc || 0)),feeAmt:money(Number(sale.fee || 0)),total:money(Number(sale.total || 0)),comment:esc(sale.comment || ''),footer:esc(cfg.foot || 'شكراً لتعاملكم معنا')};
        template = template.replace(/\{\{(\w+)\}\}/g, (m, key) => key in values ? values[key] : m);
        const popup = window.open('', '_blank', 'width=420,height=700');
        if (!popup) return toast('اسمح بفتح نافذة الطباعة من المتصفح');
        const width = Math.max(58, Math.min(110, Number(cfg.paperWidth || 80)));
        const font = Math.max(8, Math.min(28, Number(cfg.printFontSize || 14)));
        const feed = Math.max(0, Math.min(80, Number(cfg.feed ?? 10)));
        popup.document.write(`<!doctype html><html dir="rtl"><meta charset="utf-8"><title>فاتورة #${esc(sale.no)}</title><style>@page{size:${width}mm auto;margin:2mm}body{font:${font}px Tahoma,Arial;padding:2mm;color:#000;width:${width - 8}mm;box-sizing:border-box}img{max-width:100%}table{width:100%;border-collapse:collapse}td{padding:4px 2px;border-bottom:1px dashed #aaa}.total{font-weight:bold;font-size:18px}</style><main>${template}</main><div style="height:${feed}mm"></div><script>window.onload=()=>{window.print()}</script></html>`);
        popup.document.close();
      }
    } catch (e: any) { toast(e.message || 'تعذرت إعادة الطباعة'); }
  };

  const closeDay = async () => {
    if (!can('close') || !window.confirm('قفل يوم العمل وأرشفة الفواتير؟')) return;
    try { const ids = (state?.sales || []).filter((s: any) => !s.void).map((s: any) => s.id); await api('/api/close', {ids}); toast('تم قفل يوم العمل'); await load(); } catch (e: any) { toast(e.message); }
  };
  const submitReturn = async () => {
    if (!returnSale) return toast('اختار فاتورة أولاً');
    const items = (returnSale.items || []).map((i: any) => ({id: i.id, qty: Number(returnQtys[i.id] || 0)})).filter((i: any) => i.qty > 0);
    if (!items.length) return toast('أدخل كمية مرتجع واحدة على الأقل');
    try { await api('/api/return', {sid: returnSale.id, items, reason: returnReason, pw: managerPw}); toast('تم تسجيل المرتجع كمديونية'); setReturnOpen(false); setReturnSale(null); setManagerPw(''); await load(); } catch (e: any) { toast(e.message); }
  };
  const openEdit = (s: any) => { setEditSale(s); setEditItems((s.items || []).map((x: any) => ({...x}))); setEditReason(''); };
  const saveEdit = async () => {
    if (!editSale || !editItems.length || !editReason.trim()) return toast('اكتب سبب التعديل واترك صنفًا واحدًا على الأقل');
    const sub = editItems.reduce((a, x) => a + Number(x.qty || 0) * Number(x.price || 0), 0);
    try { await api('/api/edit', {id: editSale.id, reason: editReason.trim(), items: editItems.map(x => ({id: x.id, name: x.name, cat: x.cat || '', unit: x.unit, qty: Number(x.qty), price: Number(x.price)})), sub, disc: 0, tax: 0, fee: Number(editSale.fee || 0), total: sub + Number(editSale.fee || 0), cust: editSale.cust || {}, comment: editSale.comment || ''}); toast('تم تعديل الفاتورة'); setEditSale(null); await load(); } catch (e: any) { toast(e.message); }
  };

  const logout = () => {
    sessionStorage.removeItem('istakoza-token');
    setAuthed(false); setState(null); clearCart();
  };

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  if (!state) {
    return (
      <main className="login">
        <div className="login-card">
          <p>جاري تحميل البيانات من SQL...</p>
          <button className="primary" onClick={load}><RefreshCw size={16} /> إعادة المحاولة</button>
          <button className="ghost" onClick={logout}>خروج</button>
        </div>
      </main>
    );
  }

  const typeLabel =
    orderType === 'table' ? (tableNo ? `طاولة ${tableNo}` : 'صالة')
    : orderType === 'delivery' ? 'دليفري'
    : orderType === 'pickup' ? 'استلام فرع'
    : 'كاشير';

  return (
    <div className="app">
      <header className="top">
        <div className="modes">
          <button className={orderType === 'cash' ? 'active primary' : ''} onClick={() => { setOrderType('cash'); setTableNo(null); }}>
            <ShoppingCart size={16} /> كاشير
          </button>
          <button className={orderType === 'table' || !!tableNo ? 'active primary' : ''} disabled={!can('tables') && state.me?.role !== 'admin'}
            onClick={() => { setOrderType('table'); setTablesOpen(true); loadTables(); }}>
            <Armchair size={16} /> صالة
          </button>
          <button className={orderType === 'delivery' ? 'active primary' : ''} onClick={() => setOrderType('delivery')}>
            <Truck size={16} /> دليفري
          </button>
          <button className={orderType === 'pickup' ? 'active primary' : ''} onClick={() => setOrderType('pickup')}>
            <Store size={16} /> استلام فرع
          </button>
        </div>
        <div className="user">
          {state.me?.full || state.me?.u} · {state.products?.length || 0} صنف
          <button className="ghost" onClick={() => setOrdersOpen(true)}>أوردرات اليوم</button>
          <button className="ghost" onClick={() => setPickupOpen(true)}>استلام الفرع ({pickupOrders.length})</button>
          <button className="ghost" onClick={() => setCashDeliveryOpen(true)}>تسليم الكاشير ({cashOrdersToDeliver.length})</button>
          {can('return') && <button className="ghost" onClick={() => setReturnOpen(true)}>مرتجعات</button>}
          {can('close') && <button className="warn" onClick={closeDay}>قفل اليوم</button>}
          <button className="ghost" style={{marginInlineStart: 8}} onClick={load} title="تحديث من SQL"><RefreshCw size={14} /></button>
          <button className="ghost" onClick={logout} title="خروج"><LogOut size={14} /></button>
        </div>
      </header>

      <div className="layout">
        <section className="catalog">
          <div className="toolbar">
            <input placeholder="بحث عن صنف / كود" value={search} onChange={e => setSearch(e.target.value)} />
          </div>
          <div className="chips">
            {cats.map(c => (
              <button key={c} className={cat === c ? 'active' : ''} onClick={() => setCat(c)}>{c}</button>
            ))}
          </div>
          <div className="products">
            {products.map(p => (
              <button className="product" key={p.id} onClick={() => add(p)}>
                <span>{p.icon || '🍴'}</span>
                <b>{p.name}</b>
                <small>{money(p.price)}{p.unit === 'kg' ? ' / كجم' : ''}</small>
              </button>
            ))}
            {!products.length && <div className="empty">لا توجد أصناف — تحقق من قاعدة البيانات</div>}
          </div>
        </section>

        <aside className="invoice">
          <div className="invoice-head">
            <strong>{typeLabel}</strong>
            <span className="badge">SQL · React</span>
          </div>
          {!!tableNo && (
            <div className="badge">ويتر: {tableMeta.waiter || '—'} | أشخاص: {tableMeta.guests || 0}</div>
          )}
          {(orderType === 'delivery' || orderType === 'pickup') && (
            <div className="cust">
              <input placeholder="اسم العميل" value={cust.name} onChange={e => setCust({...cust, name: e.target.value})} />
              <input placeholder="الهاتف" value={cust.phone} onChange={e => setCust({...cust, phone: e.target.value})} />
              {orderType === 'delivery' && (
                <input placeholder="العنوان" value={cust.addr} onChange={e => setCust({...cust, addr: e.target.value})} />
              )}
            </div>
          )}

          <div className="lines">
            {!cart.length && <div className="empty">الفاتورة فارغة<br /><small>اضغط على صنف لإضافته</small></div>}
            {cart.map(x => (
              <div className="line" key={x.id}>
                <div>
                  <b>{x.name}</b>
                  <div><small>{money(x.price)} × {x.qty} = {money(x.price * x.qty)}</small></div>
                </div>
                <div className="qty">
                  <button onClick={() => change(x.id, -1)}><Minus size={14} /></button>
                  <b>{x.qty}</b>
                  <button onClick={() => change(x.id, 1)}><Plus size={14} /></button>
                </div>
              </div>
            ))}
          </div>

          <div className="totals">
            <div><span>المجموع</span><span>{money(subtotal)}</span></div>
            {!!disc && <div><span>{adjustment?.opName || 'خصم'}</span><span>-{money(disc)}</span></div>}
            {!!fee && <div><span>توصيل</span><span>{money(fee)}</span></div>}
            <div className="grand"><span>الإجمالي</span><span>{money(total)}</span></div>
            {lastNo != null && <div><span>آخر فاتورة</span><span>#{lastNo}</span></div>}
          </div>

          {adjustment && (
            <div className="badge" style={{marginBottom: 8}}>
              {adjustment.opName} — {adjustment.note}
              <button className="ghost" style={{marginInlineStart: 8}} onClick={() => setAdjustment(null)}>إلغاء</button>
            </div>
          )}
          <input className="invoice-note" placeholder="ملاحظة الطلب (بدون ثوم، العنوان...)" value={note} onChange={e => setNote(e.target.value)} />

          <div className="actions">
            <button className="wide ghost" onClick={clearCart}><Trash2 size={16} /> تفريغ</button>
            {(can('tables') || state.me?.role === 'admin') && (
              <button onClick={() => { setTablesOpen(true); loadTables(); }}><Armchair size={16} /> طاولات</button>
            )}
            {(can('tables') || state.me?.role === 'admin') && (
              <button disabled={busy || !tableNo} onClick={sendKitchen}><Send size={16} /> مطبخ</button>
            )}
            {(can('disc') || can('complimentary') || state.me?.role === 'admin') && (
              <button onClick={() => setOptions(true)}><Percent size={16} /> خيارات</button>
            )}
            <button className="ok" disabled={busy || !cart.length} onClick={payCashNow}>
              <CreditCard size={16} /> دفع كاش مباشرة
            </button>
            <button className="ok wide" disabled={busy || !cart.length} onClick={openPayments}>
              <CreditCard size={16} /> Invoice / طرق الدفع ({money(total)})
            </button>
          </div>
        </aside>
      </div>

      {notice && <div className="toast" onClick={() => setNotice('')}>{notice}</div>}

      {ordersOpen && (
        <div className="modal" onClick={() => setOrdersOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>أوردرات اليوم</h2>
            <input autoFocus placeholder="بحث برقم الفاتورة أو اسم/هاتف العميل" value={orderQuery} onChange={e => setOrderQuery(e.target.value)} />
            <div className="order-list">{orders.map((s: any) => <div className={`order-card ${s.void ? 'void' : ''}`} key={s.id}>
              <div><b>#{s.no}</b> · {s.type || 'cash'} · {money(s.total)}<small>{s.day || ''} {s.time || ''}</small><small>{s.cust?.name || ''} {s.cust?.phone || ''}</small></div>
              <div className="order-actions"><button onClick={() => setSelectedSale(s)}>عرض الفاتورة</button><button disabled={!can('reprint')} onClick={() => reprintSale(s, 'customer')}>طباعة العميل</button><button disabled={!can('reprint')} onClick={() => reprintSale(s, 'kitchen')}>طباعة المطبخ</button>{s.void ? <span className="badge">ملغاة</span> : <><button disabled={!can('edit')} onClick={() => openEdit(s)}>تعديل</button> <button className="danger" disabled={!can('void')} onClick={() => voidSale(s)}>إلغاء</button></>}</div>
            </div>)}{!orders.length && <div className="empty">لا توجد أوردرات</div>}</div>
            <button className="ghost" onClick={() => setOrdersOpen(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {pickupOpen && (
        <div className="modal" onClick={() => setPickupOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>طلبات استلام الفرع</h2>
            <p className="hint">طلبات الاستلام التي لم يتم تسجيل تسليمها بعد.</p>
            <div className="order-list">{pickupOrders.map((s: any) => <div className="order-card" key={s.id}>
              <div><b>#{s.no}</b> · {money(s.total)}<small>{s.day || ''} {s.time || ''}</small><small>{s.cust?.name || ''} {s.cust?.phone || ''}</small><small>{(s.items || []).map((i: any) => `${i.name} ×${i.qty}`).join('، ')}</small></div>
              <button className="primary" disabled={busy} onClick={() => markSaleDelivered(s)}>تم التسليم</button>
            </div>)}{!pickupOrders.length && <div className="empty">لا توجد طلبات استلام معلقة</div>}</div>
            <button className="ghost" onClick={() => setPickupOpen(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {cashDeliveryOpen && (
        <div className="modal" onClick={() => setCashDeliveryOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>تسليم طلبات الكاشير</h2>
            <p className="hint">طلبات الكاشير المسجلة باسم المستخدم الحالي والتي لم يتم تأكيد تسليمها.</p>
            <div className="order-list">{cashOrdersToDeliver.map((s: any) => <div className="order-card" key={s.id}>
              <div><b>#{s.no}</b> · {s.orderType || s.type || 'كاشير'} · {money(s.total)}<small>{s.day || ''} {s.time || ''}</small><small>{(s.items || []).map((i: any) => `${i.name} ×${i.qty}`).join('، ')}</small></div>
              <button className="primary" disabled={busy} onClick={() => markSaleDelivered(s)}>تم التسليم</button>
            </div>)}{!cashOrdersToDeliver.length && <div className="empty">لا توجد طلبات تحتاج إلى تسليم</div>}</div>
            <button className="ghost" onClick={() => setCashDeliveryOpen(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {selectedSale && (
        <div className="modal" onClick={() => setSelectedSale(null)}>
          <div className="modal-card receipt-preview" onClick={e => e.stopPropagation()}>
            <h2>فاتورة رقم #{selectedSale.no}</h2>
            <div className="receipt-meta"><span>{selectedSale.day || ''} {selectedSale.time || ''}</span><span>{selectedSale.type || 'cash'}</span><span>{selectedSale.cust?.name || ''} {selectedSale.cust?.phone || ''}</span>{selectedSale.cust?.addr && <span>{selectedSale.cust.addr}</span>}</div>
            <div className="receipt-lines">{(selectedSale.items || []).map((it: any, i: number) => <div key={`${it.id}-${i}`}><span>{it.name} × {it.qty}</span><b>{money(Number(it.qty || 0) * Number(it.price || 0))}</b></div>)}</div>
            <div className="receipt-totals"><div><span>المجموع</span><b>{money(selectedSale.sub || 0)}</b></div><div><span>الخصم</span><b>{money(selectedSale.disc || 0)}</b></div><div><span>التوصيل</span><b>{money(selectedSale.fee || 0)}</b></div><div className="grand"><span>الإجمالي</span><b>{money(selectedSale.total || 0)}</b></div></div>
            {selectedSale.comment && <p>ملاحظة: {selectedSale.comment}</p>}
            <button className="primary" onClick={() => reprintSale(selectedSale, 'customer')}>🖨️ طباعة فاتورة العميل</button>
            <button onClick={() => reprintSale(selectedSale, 'kitchen')}>🍳 إعادة طباعة المطبخ</button>
            <button className="ghost" onClick={() => setSelectedSale(null)}>إغلاق</button>
          </div>
        </div>
      )}

      {returnOpen && (
        <div className="modal" onClick={() => setReturnOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>مرتجع من فاتورة</h2>
            {!returnSale ? <>
              <input placeholder="بحث برقم الفاتورة" value={orderQuery} onChange={e => setOrderQuery(e.target.value)} />
              <div className="order-list">{orders.filter((s: any) => !s.void && s.returned !== 2).map((s: any) => <div className="order-card" key={s.id}><span>#{s.no} · {money(s.total)}</span><button className="primary" onClick={() => {setReturnSale(s); setReturnQtys({});}}>اختيار</button></div>)}</div>
            </> : <>
              <div className="badge">الفاتورة #{returnSale.no} · {money(returnSale.total)}</div>
              {(returnSale.items || []).map((i: any) => <div className="payment-line" key={i.id}><span>{i.name} · المتاح {i.qty}</span><input type="number" min="0" max={i.qty} step="0.001" placeholder="كمية المرتجع" value={returnQtys[i.id] || ''} onChange={e => setReturnQtys({...returnQtys, [i.id]: e.target.value})} /></div>)}
              <select value={returnReason} onChange={e => setReturnReason(e.target.value)}><option>أوردر غلط</option><option>جودة سيئة</option><option>الزبون لغى</option><option>تأخير في التوصيل</option><option>صنف ناقص</option><option>أخرى</option></select>
              <input type="password" placeholder="كلمة سر المدير لاعتماد المرتجع" value={managerPw} onChange={e => setManagerPw(e.target.value)} />
              <button className="primary" onClick={submitReturn}>تأكيد المرتجع</button><button onClick={() => setReturnSale(null)}>اختيار فاتورة أخرى</button>
            </>}
            <button className="ghost" onClick={() => setReturnOpen(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {editSale && (
        <div className="modal" onClick={() => setEditSale(null)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>تعديل الفاتورة #{editSale.no}</h2>
            {editItems.map((x, i) => <div className="payment-line" key={i}><span>{x.name}</span><input type="number" min="0" step="0.001" value={x.qty} onChange={e => setEditItems(xs => xs.map((v, n) => n === i ? {...v, qty: e.target.value} : v))}/><input type="number" min="0" step="0.01" value={x.price} onChange={e => setEditItems(xs => xs.map((v, n) => n === i ? {...v, price: e.target.value} : v))}/><button className="danger" onClick={() => setEditItems(xs => xs.filter((_, n) => n !== i))}>حذف</button></div>)}
            <textarea placeholder="سبب التعديل (إجباري)" value={editReason} onChange={e => setEditReason(e.target.value)} />
            <button className="primary" onClick={saveEdit}>حفظ التعديل</button><button className="ghost" onClick={() => setEditSale(null)}>إلغاء</button>
          </div>
        </div>
      )}

      {payOpen && (
        <div className="modal" onClick={() => setPayOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>Invoice / طرق الدفع — {money(total)}</h2>
            <p className="hint">يمكن تقسيم قيمة الفاتورة بين أكثر من طريقة دفع.</p>
            {payments.map((line, i) => (
              <div className="payment-line" key={i}>
                <select value={line.method} onChange={e => setPayment(i, {method: e.target.value})}>
                  {payMethods.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
                <input type="number" min="0" step="0.01" value={line.amount} onChange={e => setPayment(i, {amount: e.target.value})} />
                {payments.length > 1 && <button className="danger" onClick={() => setPayments(xs => xs.filter((_, n) => n !== i))}>حذف</button>}
              </div>
            ))}
            <div className="payment-summary"><span>الإجمالي: <b>{money(total)}</b></span><span>المحدد: <b>{money(payments.reduce((a, x) => a + (Number(x.amount) || 0), 0))}</b></span></div>
            <div className="row">
              <button onClick={addPayment}>+ إضافة طريقة</button>
              <button className="primary" disabled={busy} onClick={() => completePay(payments)}>تأكيد الدفع</button>
              <button className="ghost" onClick={() => setPayOpen(false)}>إغلاق</button>
            </div>
          </div>
        </div>
      )}

      {weightProduct && (
        <div className="modal" onClick={() => setWeightProduct(null)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <h2>{weightProduct.name} — {money(weightProduct.price)} / كجم</h2>
            <label>الوزن بالجرام</label>
            <input autoFocus type="number" min="1" step="1" value={weightGrams} onChange={e => setWeightGrams(e.target.value)} />
            <div className="chips">
              {[250, 500, 750, 1000, 1500, 2000].map(g => <button key={g} onClick={() => addWeight(g)}>{g >= 1000 ? `${g / 1000} كجم` : `${g} جم`}</button>)}
            </div>
            <button className="primary" onClick={() => addWeight(Number(weightGrams))}>إضافة للفاتورة</button>
            <button className="ghost" onClick={() => setWeightProduct(null)}>إلغاء</button>
          </div>
        </div>
      )}

      {options && (
        <div className="modal" onClick={() => setOptions(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <h2>خيارات الفاتورة</h2>
            {(can('disc') || state.me?.role === 'admin') && (
              <button className="warn" onClick={() => { setAdjType('discount'); setAdjOpen(true); setOptions(false); }}>
                <Percent size={16} /> خصم
              </button>
            )}
            {(can('complimentary') || state.me?.role === 'admin') && (
              <button onClick={() => { setAdjType('complimentary'); setAdjOpen(true); setOptions(false); }}>
                <Gift size={16} /> Complimentary
              </button>
            )}
            <button className="ghost" onClick={() => setOptions(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {adjOpen && (
        <div className="modal">
          <div className="modal-card">
            <h2>{adjType === 'complimentary' ? 'Complimentary' : 'خصم'}</h2>
            <label>النطاق
              <select value={adjScope} onChange={e => setAdjScope(e.target.value as any)}>
                <option value="invoice">الفاتورة كاملة</option>
                <option value="item">صنف أو عدة أصناف</option>
              </select>
            </label>
            {adjScope === 'item' && (
              <label>الأصناف
                <select multiple size={Math.min(5, Math.max(2, cart.length))} value={adjPids.map(String)}
                  onChange={e => setAdjPids(Array.from(e.target.selectedOptions).map(o => Number(o.value)))}>
                  {cart.map(x => <option key={x.id} value={x.id}>{x.name} — {money(x.qty * x.price)}</option>)}
                </select>
              </label>
            )}
            {adjType === 'discount' && (
              <>
                <label>نوع الخصم
                  <select value={adjMode} onChange={e => setAdjMode(e.target.value as any)}>
                    <option value="amount">مبلغ</option>
                    <option value="percent">نسبة %</option>
                  </select>
                </label>
                <label>القيمة
                  <input type="number" min={0} value={adjValue || ''} onChange={e => setAdjValue(Number(e.target.value))} />
                </label>
              </>
            )}
            <button className="primary" onClick={applyAdjustment}>تطبيق</button>
            <button className="ghost" onClick={() => setAdjOpen(false)}>إلغاء</button>
          </div>
        </div>
      )}

      {tablesOpen && (
        <div className="modal" onClick={() => setTablesOpen(false)}>
          <div className="modal-card wide" onClick={e => e.stopPropagation()}>
            <h2>الطاولات</h2>
            <div className="chips">
              {areas.map(a => (
                <button key={a} className={area === a ? 'active' : ''} onClick={() => setArea(a)}>{a}</button>
              ))}
            </div>
            <div className="table-grid">
              {tables.filter(t => !area || t.area === area).map(t => (
                <button key={t.no} disabled={t.status === 'closed'} className={`table ${t.status}`} onClick={() => openTable(t)}>
                  <b>{t.no}</b>
                  <small>
                    {t.status === 'guest' ? 'بها ضيف' : t.status === 'reserved' ? 'محجوزة' : t.status === 'closed' ? 'مغلقة' : 'مفتوحة'}
                    <br />{t.waiter || ''}
                  </small>
                </button>
              ))}
            </div>
            {!tables.length && <div className="empty">لا توجد طاولات في SQL</div>}
            <button className="ghost" onClick={() => setTablesOpen(false)}>إغلاق</button>
          </div>
        </div>
      )}

      {tableSetup && (
        <div className="modal">
          <div className="modal-card">
            <h2>فتح الطاولة {tableSetup.no}</h2>
            <label>الويتر
              <select value={tableMeta.waiter} onChange={e => setTableMeta({...tableMeta, waiter: e.target.value})}>
                <option value="">اختر الويتر</option>
                {waiters.map(w => <option key={w.empNo} value={w.name}>{w.name}</option>)}
              </select>
            </label>
            <label>عدد الأشخاص
              <input type="number" min={1} value={tableMeta.guests || ''} onChange={e => setTableMeta({...tableMeta, guests: Number(e.target.value)})} />
            </label>
            {!waiters.length && <small className="error">لا يوجد ويتر — أضف موظفين بوظيفة ويتر من الباك أوفيس</small>}
            <button className="primary" onClick={confirmOpen}>فتح الطاولة</button>
            <button className="ghost" onClick={() => setTableSetup(null)}>إلغاء</button>
          </div>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
