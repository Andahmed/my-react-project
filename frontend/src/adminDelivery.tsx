import React, {useEffect, useMemo, useState} from 'react';
import {api} from './adminApi';

const money = (n: number) => (Math.round((n || 0) * 100) / 100).toString();
const hhmm = (ms: number) => { const d = new Date(ms); return d.toLocaleTimeString('ar-EG', {hour: '2-digit', minute: '2-digit'}); };

// ---------------------------------------------------------------- طباعة إيصال الأوردر عند التحميل
// الإيصال بتاريخ وساعة الويندوز (وقت الطباعة الفعلي)، ورقم الأوردر ووقته زي ما اتسجّلوا.
const esc = (v: any) => String(v ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c] as string));
const slipHtml = (o: any, rider: string) => {
  const now = new Date();
  const stamp = now.toLocaleDateString('en-GB') + ' ' + now.toLocaleTimeString('en-GB');
  const rows = (o.items || []).map((i: any) => `<tr><td>${esc(i.n)}</td><td>${esc(i.q)}</td><td>${money(i.p * i.q)}</td></tr>`).join('');
  const sub = (o.total || 0) - (o.fee || 0);
  return `<div style="text-align:center;font-size:18px">🛵 أوردر دليفري #${esc(o.no)}</div>
    <div>المندوب: <b>${esc(rider)}</b></div>
    <div>العميل: ${esc(o.name)} ${esc(o.phone)}</div>
    <div>العنوان: ${esc(o.addr)}</div>
    <div>وقت الأوردر: ${esc(o.time)}</div>
    <hr/><table style="width:100%;border-collapse:collapse"><tbody>${rows}</tbody></table><hr/>
    <div>المجموع: ${money(sub)}</div><div>التوصيل: ${money(o.fee)}</div>
    <div style="font-size:18px">المطلوب: <b>${money(o.total)} ج</b></div>
    <div style="text-align:center;font-size:11px;margin-top:6px">طُبع ${stamp}</div>`;
};
let slipCfg: any = null;
const printSlip = async (o: any, rider: string) => {
  if (!slipCfg) { try { slipCfg = (await api('/api/state')).cfg || {}; } catch { slipCfg = {}; } }
  const pw = slipCfg.pw || 72, mg = slipCfg.mg ?? 2, fd = Math.max(0, +(slipCfg.feed ?? 10) || 0);
  const f = document.createElement('iframe');
  f.style.cssText = `position:fixed;left:-9999px;top:0;height:2000px;border:0;width:${pw}mm`;
  document.body.appendChild(f);
  const d = f.contentWindow!.document;
  d.open();
  d.write(`<html dir="rtl"><head><meta charset="UTF-8"><style>*{box-sizing:border-box}body{margin:0 auto;width:${pw - 2 * mg}mm;padding:${mg}mm 0 ${fd}mm;font-family:Tahoma,Arial;font-size:${slipCfg.fs || 14}px;font-weight:bold;color:#000}td{padding:1px 0}hr{border:0;border-top:1px dashed #000}</style></head><body>${slipHtml(o, rider)}</body></html>`);
  d.close();
  await new Promise(r => setTimeout(r, 300));
  f.contentWindow!.focus(); f.contentWindow!.print();
  setTimeout(() => f.remove(), 3000);
};

export function Delivery() {
  const [sub, setSub] = useState<'ops' | 'riders' | 'shifts'>('ops');
  return (
    <div>
      <div className="row" style={{justifyContent: 'flex-start'}}>
        <button className={sub === 'ops' ? 'primary' : ''} onClick={() => setSub('ops')}>التشغيل</button>
        <button className={sub === 'riders' ? 'primary' : ''} onClick={() => setSub('riders')}>المناديب</button>
        <button className={sub === 'shifts' ? 'primary' : ''} onClick={() => setSub('shifts')}>سجل الشفتات</button>
      </div>
      {sub === 'ops' && <Ops />}
      {sub === 'riders' && <Riders />}
      {sub === 'shifts' && <Shifts />}
    </div>
  );
}

function useDv() {
  const [dv, setDv] = useState<any>({riders: [], shifts: {}, dispatch: [], done: []});
  const [orders, setOrders] = useState<any[]>([]);
  const [err, setErr] = useState('');
  const load = async () => {
    try {
      const [a, b] = await Promise.all([api('/api/delivery'), api('/api/delivery/orders')]);
      setDv(a); setOrders(b); setErr('');
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);
  return {dv, orders, load, err};
}

function Ops() {
  const {dv, orders, load, err: loadErr} = useDv();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [rid, setRid] = useState(0);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [cash, setCash] = useState<Record<number, string>>({});
  const [actual, setActual] = useState<Record<number, string>>({});

  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const byId = useMemo(() => { const m: Record<string, any> = {}; orders.forEach(o => { m[o.id] = o; }); return m; }, [orders]);
  const doneSet = useMemo(() => new Set<string>(dv.done), [dv.done]);
  const pending = orders.filter(o => !doneSet.has(o.id) && o.state !== 'F');
  const active = dv.riders.filter((r: any) => r.active);

  const act = async (url: string, body: any, done: string, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return null;
    setMsg(''); setErr('');
    try { const r = await api(url, body); setMsg(done); await load(); return r || true; } catch (e: any) { setErr(e.message); return null; }
  };

  const toggle = (id: string) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n); };
  const dispatch = async () => {
    if (!rid) return setErr('اختار مندوب محضّر الأول');
    if (!sel.size) return setErr('اختار أوردر واحد على الأقل');
    const picked = Array.from(sel).map(id => byId[id]).filter(Boolean);
    const rname = (dv.riders.find((r: any) => r.id === rid) || {}).name || '';
    const ok = await act('/api/delivery/dispatch', {rid, sids: Array.from(sel)}, 'اتحمّل ' + sel.size + ' أوردر');
    if (ok) {
      setSel(new Set()); setRid(0);
      for (const o of picked) { try { await printSlip(o, rname); } catch (e: any) { setErr('تعذّرت طباعة إيصال #' + o.no + ': ' + e.message); } await new Promise(r => setTimeout(r, 1600)); }
    }
  };

  return (
    <div>
      {msg && <div className="ok">{msg}</div>}{(err || loadErr) && <div className="error">{err || loadErr}</div>}
      <div className="card" style={{marginBottom: 16}}>
        <h3 style={{marginTop: 0}}>أوردرات معلّقة ({pending.length})</h3>
        <div style={{overflowX: 'auto'}}>
          <table>
            <thead><tr><th></th><th>#</th><th>الوقت</th><th>العميل</th><th>العنوان</th><th>الإجمالي</th></tr></thead>
            <tbody>
              {pending.map(o => (
                <tr key={o.id}>
                  <td><input type="checkbox" style={{width: 'auto'}} checked={sel.has(o.id)} onChange={() => toggle(o.id)} /></td>
                  <td>{o.no}</td><td>{o.time}</td><td>{o.name} {o.phone}</td><td>{o.addr}</td><td>{money(o.total)}</td>
                </tr>
              ))}
              {!pending.length && <tr><td colSpan={6}>مفيش أوردرات معلّقة</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="row">
          <select value={rid} onChange={e => setRid(Number(e.target.value))}>
            <option value={0}>— اختار مندوب محضّر —</option>
            {active.filter((r: any) => dv.shifts[String(r.id)]).map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <button className="primary" onClick={dispatch}>تحميل ({sel.size}) على المندوب</button>
        </div>
      </div>

      <div className="card">
        <h3 style={{marginTop: 0}}>المناديب</h3>
        {active.map((r: any) => {
          const sh = dv.shifts[String(r.id)];
          const out = dv.dispatch.filter((d: any) => d.rid === r.id && d.status === 'out');
          const del = dv.dispatch.filter((d: any) => d.rid === r.id && d.status === 'delivered');
          const coll = del.reduce((a: number, d: any) => a + ((byId[d.sid] || {}).total || 0), 0);
          const exp = sh ? (sh.cash || 0) + coll : 0;
          return (
            <div key={r.id} style={{borderBottom: '1px solid #e5e7eb', padding: '10px 0'}}>
              <div className="row">
                <b>🛵 {r.name}</b>
                <span>{!sh ? 'مش محضّر' : out.length ? 'بره بـ ' + out.length + ' أوردر' : 'محضّر وفاضي'}</span>
              </div>
              {!sh && (
                <div className="row">
                  <input type="number" placeholder="العهدة" value={cash[r.id] || ''} onChange={e => setCash({...cash, [r.id]: e.target.value})} />
                  <button className="primary" onClick={() => act('/api/delivery/checkin', {rid: r.id, cash: Number(cash[r.id]) || 0}, 'تم تحضير ' + r.name)}>تحضير</button>
                </div>
              )}
              {sh && out.map((d: any) => {
                const o = byId[d.sid] || {};
                return (
                  <div className="row" key={d.id}>
                    <span>#{o.no} — {o.name} — {money(o.total)} ج — من {hhmm(d.outAt)}</span>
                    <button onClick={() => act('/api/dispatch/deliver', {dispid: d.id}, 'اتسلّم', 'تأكيد تسليم الأوردر للزبون؟')}>اتسلّم</button>
                    <button onClick={() => act('/api/dispatch/refuse', {dispid: d.id}, 'اتسجّل رفض', 'الزبون رفض الأوردر؟ هيرجع للمعلّقة.')}>رفض</button>
                    <button onClick={() => act('/api/dispatch/pull', {dispid: d.id}, 'اتسحب', 'سحب الأوردر من المندوب؟')}>سحب</button>
                  </div>
                );
              })}
              {sh && out.length > 0 && (
                <div className="row">
                  <button className="primary" onClick={() => act('/api/delivery/return', {rid: r.id}, 'المندوب رجع', 'المندوب رجع وسلّم كل الأوردرات؟')}>رجع (سلّم الكل)</button>
                  <button onClick={() => act('/api/delivery/recall', {rid: r.id}, 'رجعت للمعلّقة', 'هترجّع كل أوردرات المندوب للمعلّقة؟')}>إرجاع الكل للمعلّقة</button>
                </div>
              )}
              {sh && !out.length && (
                <div className="row">
                  <span>عهدة {money(sh.cash)} + تحصيل {money(coll)} ({del.length} أوردر) = متوقع <b>{money(exp)}</b></span>
                  <input type="number" placeholder="المبلغ الفعلي" value={actual[r.id] || ''} onChange={e => setActual({...actual, [r.id]: e.target.value})} />
                  <button className="primary" disabled={actual[r.id] === undefined || actual[r.id] === ''} onClick={async () => {
                    const res = await act('/api/delivery/settle', {rid: r.id, actual: Number(actual[r.id])}, 'تمت المحاسبة', 'قفل شفت ' + r.name + ' بمبلغ فعلي ' + actual[r.id] + '؟');
                    if (res && res.shift) setMsg('تمت المحاسبة — الفرق: ' + money(res.shift.diff));
                  }}>محاسبة وقفل الشفت</button>
                </div>
              )}
            </div>
          );
        })}
        {!active.length && <div>مفيش مناديب — ضيفهم من تاب «المناديب».</div>}
      </div>
    </div>
  );
}

function Riders() {
  const {dv, load, err: loadErr} = useDv();
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const run = async (body: any, done: string) => {
    setMsg(''); setErr('');
    try { await api('/api/delivery', body); setMsg(done); setEdit(null); await load(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="card">
      <div className="row"><h3 style={{margin: 0}}>المناديب</h3><button className="primary" onClick={() => setEdit({id: 0, name: '', phone: '', active: true})}>+ مندوب</button></div>
      {msg && <div className="ok">{msg}</div>}{(err || loadErr) && <div className="error">{err || loadErr}</div>}
      <table>
        <thead><tr><th>الاسم</th><th>التليفون</th><th>الحالة</th><th></th></tr></thead>
        <tbody>
          {dv.riders.map((r: any) => (
            <tr key={r.id}><td>{r.name}</td><td>{r.phone}</td><td>{r.active ? 'شغال' : 'موقوف'}</td>
              <td><button onClick={() => setEdit({...r})}>تعديل</button></td></tr>
          ))}
          {!dv.riders.length && <tr><td colSpan={4}>مفيش مناديب</td></tr>}
        </tbody>
      </table>
      {edit && (
        <div className="row">
          <input placeholder="الاسم" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} />
          <input placeholder="التليفون" value={edit.phone} onChange={e => setEdit({...edit, phone: e.target.value})} />
          {edit.id ? <label><input type="checkbox" style={{width: 'auto'}} checked={edit.active} onChange={e => setEdit({...edit, active: e.target.checked})} /> شغال</label> : null}
          <button className="primary" onClick={() => run(edit, 'تم الحفظ')}>حفظ</button>
          {edit.id ? <button className="danger" onClick={() => { if (window.confirm('حذف المندوب؟ لو ليه شفتات قديمة هيتوقف بس.')) run({id: edit.id, del: 1}, 'تم'); }}>حذف</button> : null}
          <button onClick={() => setEdit(null)}>إلغاء</button>
        </div>
      )}
    </div>
  );
}

function Shifts() {
  const [bday, setBday] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState('');
  // التاريخ الافتراضي من يوم العمل (جدول BusinessDay) مش من ساعة الجهاز
  useEffect(() => { api('/api/delivery').then(d => { const b = d.businessDay || ''; setBday(b); setFrom(b.slice(0, 8) + '01'); setTo(b); }).catch((e: any) => setErr(e.message)); }, []);
  const load = async () => { if (!from || !to) return; try { setRows(await api('/api/delivery/shifts?from=' + from + '&to=' + to)); setErr(''); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, [from, to]);
  const tot = rows.filter(r => r.status === 'closed').reduce((a, r) => ({orders: a.orders + r.orders, coll: a.coll + r.collected, diff: a.diff + r.diff}), {orders: 0, coll: 0, diff: 0});
  return (
    <div className="card">
      <div className="row">
        <h3 style={{margin: 0}}>سجل الشفتات</h3>{bday && <span>يوم العمل: {bday}</span>}
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} />
        <input type="date" value={to} onChange={e => setTo(e.target.value)} />
      </div>
      {err && <div className="error">{err}</div>}
      <div style={{overflowX: 'auto'}}>
        <table>
          <thead><tr><th>يوم العمل</th><th>المندوب</th><th>البداية</th><th>النهاية</th><th>عهدة</th><th>أوردرات</th><th>تحصيل</th><th>رسوم</th><th>متوقع</th><th>فعلي</th><th>الفرق</th><th>الحالة</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td>{r.day}</td><td>{r.rider}</td><td>{r.start}</td><td>{r.end}</td><td>{money(r.cash)}</td><td>{r.orders}</td>
                <td>{money(r.collected)}</td><td>{money(r.fees)}</td><td>{money(r.expected)}</td><td>{money(r.actual)}</td>
                <td style={{color: r.diff < 0 ? '#dc3545' : undefined, fontWeight: 700}}>{money(r.diff)}</td>
                <td>{r.status === 'open' ? 'مفتوح' : 'مقفول'}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={12}>مفيش شفتات في الفترة دي</td></tr>}
          </tbody>
          {rows.length > 0 && <tfoot><tr><th colSpan={5}>إجمالي المقفول</th><th>{tot.orders}</th><th>{money(tot.coll)}</th><th colSpan={3}></th><th>{money(tot.diff)}</th><th></th></tr></tfoot>}
        </table>
      </div>
    </div>
  );
}
