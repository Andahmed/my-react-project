import React, {useEffect, useMemo, useState} from 'react';
import {api} from './adminApi';

const uid = () => (crypto as any).randomUUID ? (crypto as any).randomUUID() : 'po-' + Date.now() + '-' + Math.random().toString(16).slice(2);
const STAGE: Record<string, string> = {progress: 'جاري التنفيذ', received: 'تم الاستلام', cancelled: 'ملغاة'};
const SENT: Record<string, string> = {saved: 'محفوظة', sent: 'اتبعتت', pending: 'في الانتظار'};

function Msg({msg, err}: {msg: string; err: string}) {
  return <>{msg && <div className="ok">{msg}</div>}{err && <div className="error">{err}</div>}</>;
}

function useStore() {
  const [st, setSt] = useState<any>({items: [], suppliers: [], orders: [], templates: []});
  const [err, setErr] = useState('');
  const load = async () => { try { setSt(await api('/api/store')); setErr(''); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  return {st, load, err, setErr};
}

// ---------------------------------------------------------------- المخزن
export function Stock() {
  const {st, load, err: loadErr} = useStore();
  const [q, setQ] = useState('');
  const [edit, setEdit] = useState<any>(null);
  const [sup, setSup] = useState<any>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const rows = useMemo(() => st.items.filter((i: any) => !q || (i.name + ' ' + i.code + ' ' + i.cat).includes(q)), [st.items, q]);
  const supName = (id: number) => (st.suppliers.find((s: any) => s.id === id) || {}).name || '';

  const run = async (url: string, body: any, done: string, after?: () => void) => {
    setMsg(''); setErr('');
    try { await api(url, body); setMsg(done); after && after(); await load(); } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="card">
      <h3 style={{marginTop: 0}}>أرصدة المخزن</h3>
      <div className="row">
        <input placeholder="بحث بالاسم أو الكود أو التصنيف" value={q} onChange={e => setQ(e.target.value)} />
        <button className="primary" onClick={() => setEdit({old_code: '', code: '', name: '', unit: 'kg', cat: '', price: 0, supid: 0})}>+ صنف</button>
        <button onClick={() => setSup({id: 0, name: '', phone: '', note: ''})}>+ مورد</button>
      </div>
      <Msg msg={msg} err={err || loadErr} />
      <div style={{overflowX: 'auto'}}>
        <table>
          <thead><tr><th>الكود</th><th>الاسم</th><th>الوحدة</th><th>التصنيف</th><th>الرصيد</th><th>السعر</th><th>آخر شراء</th><th>المورد</th><th></th></tr></thead>
          <tbody>
            {rows.map((i: any) => (
              <tr key={i.code}>
                <td>{i.code}</td><td>{i.name}</td><td>{i.unit}</td><td>{i.cat}</td>
                <td style={{color: i.bal < 0 ? '#dc3545' : undefined, fontWeight: 700}}>{i.bal}</td>
                <td>{i.price}</td><td>{i.last}</td><td>{supName(i.supid)}</td>
                <td><button onClick={() => setEdit({...i, old_code: i.code})}>تعديل</button></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={9}>مفيش أصناف</td></tr>}
          </tbody>
        </table>
      </div>

      {edit && (
        <>
          <h3>{edit.old_code ? 'تعديل صنف' : 'صنف جديد'}</h3>
          <div className="row">
            <input placeholder="الكود" value={edit.code} onChange={e => setEdit({...edit, code: e.target.value})} />
            <input placeholder="الاسم" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} />
            <input placeholder="الوحدة" value={edit.unit} onChange={e => setEdit({...edit, unit: e.target.value})} />
            <input placeholder="التصنيف" value={edit.cat} onChange={e => setEdit({...edit, cat: e.target.value})} />
            <input type="number" placeholder="السعر" value={edit.price} onChange={e => setEdit({...edit, price: e.target.value})} />
            <select value={edit.supid} onChange={e => setEdit({...edit, supid: Number(e.target.value)})}>
              <option value={0}>— بدون مورد —</option>
              {st.suppliers.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="row">
            <button className="primary" onClick={() => run('/api/store/item', edit, 'تم حفظ الصنف', () => setEdit(null))}>حفظ</button>
            {edit.old_code && <button className="danger" onClick={() => { if (window.confirm('حذف الصنف؟')) run('/api/store/item', {del: 1, code: edit.old_code}, 'تم الحذف', () => setEdit(null)); }}>حذف</button>}
            <button onClick={() => setEdit(null)}>إلغاء</button>
          </div>
        </>
      )}

      <h3>الموردين</h3>
      <table>
        <tbody>
          {st.suppliers.map((s: any) => (
            <tr key={s.id}><td>{s.name}</td><td>{s.phone}</td><td>{s.note}</td>
              <td><button onClick={() => setSup({...s})}>تعديل</button></td></tr>
          ))}
        </tbody>
      </table>
      {sup && (
        <div className="row">
          <input placeholder="اسم المورد" value={sup.name} onChange={e => setSup({...sup, name: e.target.value})} />
          <input placeholder="التليفون" value={sup.phone} onChange={e => setSup({...sup, phone: e.target.value})} />
          <input placeholder="ملاحظة" value={sup.note} onChange={e => setSup({...sup, note: e.target.value})} />
          <button className="primary" onClick={() => run('/api/store/supplier', sup, 'تم حفظ المورد', () => setSup(null))}>حفظ</button>
          {sup.id ? <button className="danger" onClick={() => { if (window.confirm('حذف المورد؟')) run('/api/store/supplier', {del: 1, id: sup.id}, 'تم الحذف', () => setSup(null)); }}>حذف</button> : null}
          <button onClick={() => setSup(null)}>إلغاء</button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- أوامر الشراء
export function PurchaseOrders() {
  const {st, load, err: loadErr} = useStore();
  const [form, setForm] = useState<any>(null);
  const [open, setOpen] = useState('');
  const [recv, setRecv] = useState<Record<string, string>>({});
  const [tplName, setTplName] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const run = async (url: string, body: any, done: string, after?: () => void) => {
    setMsg(''); setErr('');
    try { const r = await api(url, body); setMsg(r && r.status === 'pending' ? 'اتحفظت في الانتظار: ' + (r.err || '') : done); after && after(); await load(); } catch (e: any) { setErr(e.message); }
  };

  const newPO = () => { setTplName(''); setForm({id: uid(), supplier: '', note: '', items: [{code: '', qty: ''}]}); };
  const setLine = (n: number, k: string, v: string) => setForm({...form, items: form.items.map((l: any, i: number) => i === n ? {...l, [k]: v} : l)});

  // قوالب الطلبيات: نفس endpoint وصيغة البيانات المستخدمة في HTML القديم.
  const useTemplate = (t: any) => {
    if (!form) return;
    const valid = (t.items || []).filter((line: any) => st.items.some((item: any) => String(item.code) === String(line.code)) && Number(line.qty) > 0);
    if (!valid.length) { setErr('القالب لا يحتوي على أصناف مخزنية صالحة.'); return; }
    const merged = [...form.items.filter((line: any) => line.code && Number(line.qty) > 0)];
    valid.forEach((line: any) => {
      const existing = merged.find((x: any) => String(x.code) === String(line.code));
      if (existing) existing.qty = Number(existing.qty || 0) + Number(line.qty || 0);
      else merged.push({code: String(line.code), qty: Number(line.qty)});
    });
    setForm({...form, items: merged.length ? merged : [{code: '', qty: ''}]});
    setErr(''); setMsg('تم تحميل القالب: ' + t.name);
  };

  const saveTemplate = async () => {
    const name = tplName.trim();
    const items = (form?.items || []).filter((line: any) => line.code && Number(line.qty) > 0).map((line: any) => ({code: String(line.code), qty: Number(line.qty)}));
    if (!name) { setErr('اكتب اسم القالب أولاً.'); return; }
    if (!items.length) { setErr('أضف صنفًا واحدًا على الأقل بكمية أكبر من صفر.'); return; }
    setErr(''); setMsg('');
    try {
      await api('/api/store/template', {name, items});
      setTplName(''); setMsg('تم حفظ القالب: ' + name); await load();
    } catch (e: any) { setErr(e.message || 'تعذر حفظ القالب'); }
  };

  const deleteTemplate = async (t: any) => {
    if (!window.confirm('حذف قالب "' + t.name + '"؟ لن تُحذف أي طلبيات سابقة.')) return;
    setErr(''); setMsg('');
    try {
      await api('/api/store/template', {id: t.id, del: 1});
      setMsg('تم حذف القالب: ' + t.name); await load();
    } catch (e: any) { setErr(e.message || 'تعذر حذف القالب'); }
  };
  const total = form ? form.items.reduce((a: number, l: any) => {
    const it = st.items.find((x: any) => String(x.code) === String(l.code));
    return a + (it ? it.price * (Number(l.qty) || 0) : 0);
  }, 0) : 0;

  const save = () => run('/api/po', {
    id: form.id, supplier: form.supplier, note: form.note,
    items: form.items.filter((l: any) => l.code && Number(l.qty) > 0).map((l: any) => ({code: l.code, qty: Number(l.qty)})),
  }, 'تم إنشاء الطلبية', () => setForm(null));

  const receive = (o: any) => {
    const got: Record<string, number> = {};
    o.items.forEach((i: any) => { const v = recv[o.id + ':' + i.code]; got[i.code] = v === undefined || v === '' ? i.qty : Number(v); });
    if (!window.confirm('تأكيد الاستلام؟ هيتزوّد المخزن ومينفعش يتعدّل بعد كده.')) return;
    run('/api/po/stage', {id: o.id, stage: 'received', recv: got}, 'تم الاستلام وزيادة المخزن');
  };

  return (
    <div className="card">
      <div className="row"><h3 style={{margin: 0}}>أوامر الشراء</h3><button className="primary" onClick={newPO}>+ طلبية جديدة</button></div>
      <Msg msg={msg} err={err || loadErr} />

      <div className="card" style={{margin: '12px 0', background: '#f8fafc'}}>
        <h4 style={{marginTop: 0}}>قوالب الطلبيات المحفوظة</h4>
        {!st.templates.length && <div className="muted">لا توجد قوالب محفوظة بعد. أنشئ طلبية ثم احفظها كقالب.</div>}
        {st.templates.map((t: any) => <div className="row" key={t.id} style={{borderBottom: '1px solid #e5e7eb', padding: '6px 0'}}>
          <span><b>{t.name}</b> — {(t.items || []).length} صنف</span>
          <button disabled={!form} onClick={() => useTemplate(t)}>تحميل في الطلبية الحالية</button>
          <button className="danger" onClick={() => deleteTemplate(t)}>حذف</button>
        </div>)}
      </div>

      {form && (
        <div className="card" style={{border: '1px solid #e5e7eb'}}>
          <div className="row">
            <input list="sup-list" placeholder="المورد" value={form.supplier} onChange={e => setForm({...form, supplier: e.target.value})} />
            <datalist id="sup-list">{st.suppliers.map((s: any) => <option key={s.id} value={s.name} />)}</datalist>
            <input placeholder="ملاحظة" value={form.note} onChange={e => setForm({...form, note: e.target.value})} />
          </div>
          {form.items.map((l: any, n: number) => (
            <div className="row" key={n}>
              <select value={l.code} onChange={e => setLine(n, 'code', e.target.value)}>
                <option value="">— اختار خامة —</option>
                {st.items.map((i: any) => <option key={i.code} value={i.code}>{i.code} — {i.name} ({i.unit})</option>)}
              </select>
              <input type="number" placeholder="الكمية" value={l.qty} onChange={e => setLine(n, 'qty', e.target.value)} />
              <button onClick={() => setForm({...form, items: form.items.filter((_: any, i: number) => i !== n)})}>حذف</button>
            </div>
          ))}
          <div className="row">
            <button onClick={() => setForm({...form, items: [...form.items, {code: '', qty: ''}]})}>+ سطر</button>
            <strong>الإجمالي التقديري: {total.toFixed(2)}</strong>
            <button className="primary" onClick={save}>حفظ الطلبية</button>
            <input placeholder="اسم قالب لحفظ الأصناف والكميات" value={tplName} onChange={e => setTplName(e.target.value)} />
            <button onClick={saveTemplate}>حفظ كقالب</button>
            <button onClick={() => setForm(null)}>إلغاء</button>
          </div>
        </div>
      )}

      <div style={{overflowX: 'auto'}}>
        <table>
          <thead><tr><th>الرقم</th><th>اليوم</th><th>المورد</th><th>الإجمالي</th><th>الإرسال</th><th>الحالة</th><th></th></tr></thead>
          <tbody>
            {st.orders.map((o: any) => (
              <React.Fragment key={o.id}>
                <tr>
                  <td>{o.no}</td><td>{o.day} {o.time}</td><td>{o.supplier}</td><td>{o.total}</td>
                  <td>{SENT[o.status] || o.status}{o.err ? ' ⚠' : ''}</td><td>{STAGE[o.stage] || o.stage}</td>
                  <td><button onClick={() => setOpen(open === o.id ? '' : o.id)}>{open === o.id ? 'إخفاء' : 'تفاصيل'}</button></td>
                </tr>
                {open === o.id && (
                  <tr><td colSpan={7} style={{background: '#f8fafc'}}>
                    <table>
                      <thead><tr><th>الكود</th><th>الصنف</th><th>المطلوب</th><th>السعر</th><th>المستلم</th></tr></thead>
                      <tbody>
                        {o.items.map((i: any) => (
                          <tr key={i.code}>
                            <td>{i.code}</td><td>{i.name}</td><td>{i.qty} {i.unit}</td><td>{i.price}</td>
                            <td>{o.stage === 'received' ? i.rqty : o.stage === 'progress'
                              ? <input type="number" style={{width: 90}} defaultValue={i.qty} onChange={e => setRecv({...recv, [o.id + ':' + i.code]: e.target.value})} /> : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {o.note && <div>ملاحظة: {o.note}</div>}
                    {o.err && <div className="error">آخر خطأ إرسال: {o.err}</div>}
                    {o.recvAt && <div>استلمها {o.recvBy} — {o.recvAt}</div>}
                    <div className="row">
                      {o.stage === 'progress' && <button className="primary" onClick={() => receive(o)}>تم الاستلام</button>}
                      {o.stage === 'progress' && <button onClick={() => run('/api/po/stage', {id: o.id, stage: 'cancelled'}, 'اتلغت')}>إلغاء</button>}
                      {o.stage === 'cancelled' && <button onClick={() => run('/api/po/stage', {id: o.id, stage: 'progress'}, 'اتفتحت تاني')}>إعادة فتح</button>}
                      {o.status !== 'sent' && <button onClick={() => run('/api/po/send', {id: o.id}, 'تم الإرسال')}>إرسال للمورد</button>}
                      <button className="danger" onClick={() => { if (window.confirm('حذف الطلبية نهائياً؟')) run('/api/po/del', {id: o.id}, 'تم الحذف'); }}>حذف</button>
                    </div>
                  </td></tr>
                )}
              </React.Fragment>
            ))}
            {!st.orders.length && <tr><td colSpan={7}>مفيش طلبيات</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- جرد الشهر
export function Stocktake() {
  // الشهر الافتراضي من يوم العمل (جدول BusinessDay) مش من ساعة الجهاز
  const [month, setMonth] = useState('');
  useEffect(() => { api('/api/businessday').then((d: any) => setMonth(String(d.day || '').slice(0, 7))).catch(() => {}); }, []);
  const [data, setData] = useState<any>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    setErr(''); setMsg('');
    if (!month) return;
    try { const d = await api('/api/stocktake?month=' + month); setData(d); setVals({}); } catch (e: any) { setErr(e.message); setData(null); }
  };
  useEffect(() => { load(); }, [month]);

  const closed = data && data.status === 'closed';
  const act = async (url: string, body: any, done: string, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setMsg(''); setErr('');
    try { await api(url, body); setMsg(done); await load(); } catch (e: any) { setErr(e.message); }
  };
  const save = () => act('/api/stocktake/save', {
    month,
    rows: Object.keys(vals).map(code => ({code, actual: vals[code] === '' ? null : Number(vals[code])})),
  }, 'تم حفظ الجرد');

  return (
    <div className="card">
      <div className="row">
        <h3 style={{margin: 0}}>جرد الشهر</h3>
        <input type="month" value={month} onChange={e => setMonth(e.target.value)} />
        {data && <span>{closed ? `معتمد — ${data.closedBy} ${data.closedAt}` : 'مفتوح'}</span>}
      </div>
      <Msg msg={msg} err={err} />
      {data && (
        <>
          <div style={{overflowX: 'auto'}}>
            <table>
              <thead><tr><th>الكود</th><th>الصنف</th><th>أول الشهر</th><th>مستلم</th><th>مستخدم</th><th>المتوقع</th><th>الفعلي</th><th>الفرق</th><th>القيمة</th></tr></thead>
              <tbody>
                {data.rows.map((r: any) => {
                  const cur = vals[r.code] !== undefined ? vals[r.code] : (r.actual ?? '');
                  const diff = cur === '' ? '' : (Number(cur) - r.expected).toFixed(3);
                  return (
                    <tr key={r.code}>
                      <td>{r.code}</td><td>{r.name} ({r.unit})</td><td>{r.opening}</td><td>{r.received}</td><td>{r.used}</td>
                      <td>{r.expected}</td>
                      <td><input type="number" style={{width: 100}} disabled={closed} value={cur} onChange={e => setVals({...vals, [r.code]: e.target.value})} /></td>
                      <td style={{color: diff && Number(diff) < 0 ? '#dc3545' : undefined}}>{diff}</td>
                      <td>{r.value}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row">
            {!closed && <button onClick={save} disabled={!Object.keys(vals).length}>حفظ</button>}
            {!closed && <button className="primary" onClick={async () => { if (Object.keys(vals).length) await save(); act('/api/stocktake/close', {month}, 'تم اعتماد الجرد', 'اعتماد الجرد بيسجّل الفروق كتسوية في المخزن. متأكد؟'); }}>اعتماد الجرد</button>}
            {closed && <button className="danger" onClick={() => act('/api/stocktake/reopen', {month}, 'اتفتح الجرد', 'فتح الجرد هيمسح تسوية الفروق. متأكد؟')}>إعادة فتح (مدير)</button>}
          </div>
        </>
      )}
    </div>
  );
}
