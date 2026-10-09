import React, {useEffect, useMemo, useState} from 'react';
import {api} from './adminApi';
import {Activity, ArrowDownToLine, ArrowUpFromLine, CheckCircle2, EthernetPort, Link2, Plus, RefreshCw, Save, Send, Server, Settings2, ShieldCheck, Trash2, Wifi, X} from 'lucide-react';

type Branch = {code: string; name: string; ip: string; port: number; isLocal: number; active: number; note?: string};
type BranchItem = {code: string; iname: string; unit: string; qty: number; rqty: number};
type BranchOrder = {BOID: string; BONo: string; BODay: string; BOTime: string; FromCode: string; FromName: string; ToCode: string; ToName: string; Status: string; CreatedBy?: string; SentAt?: string; RecvAt?: string; RecvBy?: string; Note?: string; Err?: string; items: BranchItem[]};
type StoreItem = {code: string; name: string; unit: string; bal?: number};

const STATUS: Record<string, string> = {draft: 'مسودة', sent: 'مُرسلة / في الانتظار', received: 'تم الاستلام', done: 'اكتملت المزامنة', cancelled: 'ملغاة'};
const UNIT: Record<string, string> = {kg: 'كجم', pc: 'قطعة', l: 'لتر'};
const unit = (v: string) => UNIT[v] || v || '';
const qty = (n: number) => Number(n || 0).toLocaleString('ar-EG', {maximumFractionDigits: 3});

function Message({msg, err}: {msg: string; err: string}) {
  return <>{msg && <div className="ok inline-message"><CheckCircle2 size={16}/> {msg}</div>}{err && <div className="error inline-message"><X size={16}/> {err}</div>}</>;
}
function StatusBadge({status}: {status: string}) {
  return <span className={'status status-' + status}><span className="status-dot"/>{STATUS[status] || status}</span>;
}

export function Branches() {
  const [view, setView] = useState<'overview' | 'branches' | 'in' | 'out' | 'new'>('overview');
  const [branches, setBranches] = useState<Branch[]>([]);
  const [local, setLocal] = useState<any>(null);
  const [key, setKey] = useState('');
  const [orders, setOrders] = useState<{in: BranchOrder[]; out: BranchOrder[]}>({in: [], out: []});
  const [store, setStore] = useState<StoreItem[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [form, setForm] = useState<any>(null);
  const [open, setOpen] = useState('');
  const [recv, setRecv] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [b, bo, st] = await Promise.all([api('/api/branches'), api('/api/bo'), api('/api/store')]);
      setBranches(b.branches || []); setLocal(b.local); setKey(b.key || '');
      setOrders({in: bo.in || [], out: bo.out || []}); setStore(st.items || []);
      setErr('');
    } catch (e: any) { setErr(e.message); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const run = async (fn: () => Promise<any>, done: string, after?: () => void) => {
    setMsg(''); setErr('');
    try { await fn(); setMsg(done); after?.(); await load(true); } catch (e: any) { setErr(e.message); }
  };
  const saveBranch = () => run(() => api('/api/branch/save', {...edit, port: Number(edit.port) || 8080, active: edit.active ? 1 : 0, isLocal: edit.isLocal ? 1 : 0}), 'تم حفظ بيانات الفرع', () => setEdit(null));
  const ping = async (b: Branch) => {
    setMsg(''); setErr('');
    try { const r = await api('/api/bo/ping?code=' + encodeURIComponent(b.code)); r.ok ? setMsg(`الاتصال بفرع «${b.name}» ناجح`) : setErr(`فشل الاتصال بفرع «${b.name}»: ${r.err || 'غير متاح'}`); } catch (e: any) { setErr(e.message); }
  };
  const delBranch = (b: Branch) => { if (!window.confirm(`حذف الفرع «${b.name}»؟`)) return; run(() => api('/api/branch/del', {code: b.code}), 'تم حذف الفرع'); };
  const saveKey = () => run(() => api('/api/branch/key', {key}), 'تم حفظ المفتاح المشترك');
  const newOrder = () => setForm({toCode: '', note: '', items: [{code: '', qty: ''}]});
  const setLine = (i: number, k: string, v: string) => setForm({...form, items: form.items.map((x: any, n: number) => n === i ? {...x, [k]: v} : x)});
  const saveOrder = () => run(async () => {
    const items = form.items.filter((x: any) => x.code && Number(x.qty) > 0).map((x: any) => { const it = store.find(s => String(s.code) === String(x.code)); return {code: x.code, iname: it?.name || x.code, unit: it?.unit || '', qty: Number(x.qty)}; });
    const r = await api('/api/bo', {toCode: form.toCode, note: form.note, items});
    return r;
  }, 'تم حفظ طلبية التحويل كمسودة', () => { setForm(null); setView('out'); });
  const send = (o: BranchOrder) => run(() => api('/api/bo/send', {boid: o.BOID}), 'تم إرسال الطلبية للفرع المستلم');
  const cancel = (o: BranchOrder) => { if (!window.confirm(`إلغاء الطلبية رقم ${o.BONo}؟`)) return; run(() => api('/api/bo/cancel', {boid: o.BOID}), 'تم إلغاء الطلبية'); };
  const receive = (o: BranchOrder) => run(() => api('/api/bo/receive', {boid: o.BOID, items: o.items.map(i => ({code: i.code, rqty: recv[o.BOID + ':' + i.code] === undefined || recv[o.BOID + ':' + i.code] === '' ? i.qty : Number(recv[o.BOID + ':' + i.code])}))}), 'تم الاستلام وتحديث المخزن');
  const others = branches.filter(b => !b.isLocal && b.active);
  const pendingIn = orders.in.filter(o => o.Status === 'sent').length;
  const pendingOut = orders.out.filter(o => ['draft', 'sent'].includes(o.Status)).length;
  const allOrders = useMemo(() => [...orders.in, ...orders.out].filter((x, i, a) => a.findIndex(y => y.BOID === x.BOID) === i), [orders]);

  return <div className="branches-page">
    <div className="page-hero">
      <div><div className="eyebrow"><EthernetPort size={15}/> إدارة الشبكة والمخزون</div><h1>الفروع وطلبيات التحويل</h1><p>اربط الفروع ببعضها وتابع حركة الخامات بدون فقدان أو تسجيل يدوي.</p></div>
      <button className="secondary" onClick={() => load()} disabled={loading}><RefreshCw size={17} className={loading ? 'spin' : ''}/> تحديث البيانات</button>
    </div>
    <Message msg={msg} err={err}/>
    <div className="branch-tabs">
      {([['overview','نظرة عامة',Activity], ['branches','الفروع',Server], ['in','الوارد',ArrowDownToLine], ['out','الصادر',ArrowUpFromLine], ['new','طلب تحويل جديد',Plus]] as any[]).map(([k, label, Icon]) => <button key={k} className={view === k ? 'active' : ''} onClick={() => { setView(k); if (k === 'new') newOrder(); }}><Icon size={17}/>{label}{k === 'in' && pendingIn > 0 ? <b className="tab-count">{pendingIn}</b> : null}{k === 'out' && pendingOut > 0 ? <b className="tab-count">{pendingOut}</b> : null}</button>)}
    </div>

    {view === 'overview' && <>
      <div className="metric-grid">
        <div className="metric-card"><div className="metric-icon blue"><Server size={20}/></div><div><small>الفروع المسجلة</small><strong>{branches.length}</strong><span>{others.length} فرع آخر متاح</span></div></div>
        <div className="metric-card"><div className="metric-icon green"><Wifi size={20}/></div><div><small>الفرع الحالي</small><strong>{local?.name || 'غير محدد'}</strong><span>{local?.code || '—'}</span></div></div>
        <div className="metric-card"><div className="metric-icon orange"><ArrowDownToLine size={20}/></div><div><small>وارد بانتظار الاستلام</small><strong>{pendingIn}</strong><span>راجع الكميات قبل التأكيد</span></div></div>
        <div className="metric-card"><div className="metric-icon purple"><ArrowUpFromLine size={20}/></div><div><small>طلبات صادرة معلقة</small><strong>{pendingOut}</strong><span>مسودات أو في انتظار الرد</span></div></div>
      </div>
      <div className="two-col">
        <section className="panel"><div className="panel-title"><div><h2>حالة الاتصال</h2><p>اختبار سريع للفروع النشطة</p></div><button className="ghost" onClick={() => setView('branches')}>إدارة الفروع</button></div>{others.length ? others.slice(0, 5).map(b => <div className="branch-row" key={b.code}><div className="branch-avatar"><Server size={18}/></div><div className="branch-main"><b>{b.name}</b><span dir="ltr">{b.code} · {b.ip || 'بدون IP'}:{b.port}</span></div><button className="outline-small" onClick={() => ping(b)}><Wifi size={15}/> اختبار</button></div>) : <div className="empty-state">أضف أول فرع آخر من تبويب «الفروع».</div>}</section>
        <section className="panel"><div className="panel-title"><div><h2>آخر التحويلات</h2><p>ملخص آخر الطلبات بين الفروع</p></div><button className="ghost" onClick={() => setView('out')}>كل الطلبات</button></div>{allOrders.slice(0, 5).map(o => <div className="order-row" key={o.BOID}><div><b>طلبية #{o.BONo}</b><span>{o.FromName} ← {o.ToName}</span></div><StatusBadge status={o.Status}/></div>)}{!allOrders.length && <div className="empty-state">لا توجد طلبيات تحويل حتى الآن.</div>}</section>
      </div>
    </>}

    {view === 'branches' && <div className="two-col branches-layout"><section className="panel"><div className="panel-title"><div><h2>الفروع المسجلة</h2><p>الفرع المميز بالأزرق هو الفرع الحالي.</p></div><button className="primary" onClick={() => setEdit({code: '', name: '', ip: '', port: 8080, isLocal: false, active: true, note: ''})}><Plus size={17}/> إضافة فرع</button></div><div className="table-wrap"><table><thead><tr><th>الفرع</th><th>العنوان</th><th>الحالة</th><th>إجراءات</th></tr></thead><tbody>{branches.map(b => <tr className={b.isLocal ? 'local-row' : ''} key={b.code}><td><b>{b.name}</b><small>{b.code}{b.isLocal ? ' · هذا الفرع' : ''}</small></td><td dir="ltr">{b.ip || '—'}:{b.port}</td><td>{b.isLocal ? <span className="status status-local">محلي</span> : b.active ? <span className="status status-active">نشط</span> : <span className="status status-off">متوقف</span>}</td><td className="actions"><button className="outline-small" onClick={() => ping(b)}><Wifi size={15}/></button><button className="outline-small" onClick={() => setEdit({...b, isLocal: !!b.isLocal, active: !!b.active})}><Settings2 size={15}/></button>{!b.isLocal && <button className="outline-small danger-icon" onClick={() => delBranch(b)}><Trash2 size={15}/></button>}</td></tr>)}</tbody></table></div></section><section className="panel security-panel"><div className="panel-title"><div><h2>مفتاح الربط</h2><p>يجب أن يكون نفس المفتاح في كل الفروع.</p></div><ShieldCheck size={25} color="#2563eb"/></div><label>المفتاح المشترك</label><input dir="ltr" value={key} onChange={e => setKey(e.target.value)} placeholder="8 - 64 حرفًا أو رقمًا"/><button className="primary full" onClick={saveKey} disabled={!key.trim()}><Save size={16}/> حفظ المفتاح</button><div className="security-note"><Link2 size={16}/><span>لا ترسل هذا المفتاح خارج شبكة الفروع. تغييره يتطلب تحديثه على كل الأجهزة.</span></div>{edit && <BranchEditor edit={edit} setEdit={setEdit} save={saveBranch} close={() => setEdit(null)}/>}</section></div>}

    {view === 'new' && <section className="panel order-form-panel"><div className="panel-title"><div><h2>إنشاء طلب تحويل</h2><p>اطلب خامات من مخزن فرع آخر، ثم أرسلها بعد المراجعة.</p></div><div className="step-pill"><span>1</span> إنشاء <span>2</span> إرسال <span>3</span> استلام</div></div>{form && <><div className="form-grid"><div><label>الفرع المستلم</label><select value={form.toCode} onChange={e => setForm({...form, toCode: e.target.value})}><option value="">— اختار الفرع المستلم —</option>{others.map(b => <option key={b.code} value={b.code}>{b.name} ({b.code})</option>)}</select></div><div><label>ملاحظة</label><input value={form.note} onChange={e => setForm({...form, note: e.target.value})} placeholder="ملاحظة اختيارية"/></div></div><div className="order-lines"><div className="line-head"><h3>الأصناف والكميات</h3><span>{form.items.length} سطر</span></div>{form.items.map((l: any, i: number) => <div className="line-row" key={i}><select value={l.code} onChange={e => setLine(i, 'code', e.target.value)}><option value="">— اختار خامة —</option>{store.map(s => <option key={s.code} value={s.code}>{s.code} — {s.name} ({unit(s.unit)}) · الرصيد {qty(s.bal || 0)}</option>)}</select><input type="number" min="0.001" step="0.001" placeholder="الكمية" value={l.qty} onChange={e => setLine(i, 'qty', e.target.value)}/><button className="outline-small danger-icon" onClick={() => setForm({...form, items: form.items.filter((_: any, n: number) => n !== i)})}><Trash2 size={15}/></button></div>)}<button className="ghost add-line" onClick={() => setForm({...form, items: [...form.items, {code: '', qty: ''}]})}><Plus size={16}/> إضافة سطر</button></div><div className="form-actions"><button className="primary" disabled={!form.toCode || !form.items.some((x: any) => x.code && Number(x.qty) > 0)} onClick={saveOrder}><Save size={17}/> حفظ كمسودة</button><button className="secondary" onClick={() => {setForm(null); setView('overview')}}>إلغاء</button></div></>}</section>}

    {(view === 'in' || view === 'out') && <OrderList kind={view} orders={orders[view]} open={open} setOpen={setOpen} recv={recv} setRecv={setRecv} send={send} receive={receive} cancel={cancel} resend={() => run(() => api('/api/bo/resend'), 'تمت محاولة مزامنة التأكيدات المعلقة')}/>} 
  </div>;
}

function BranchEditor({edit, setEdit, save, close}: any) {
  return <div className="editor-card"><div className="panel-title"><h3>{edit.code ? 'تعديل الفرع' : 'فرع جديد'}</h3><button className="icon-button" onClick={close}><X size={17}/></button></div><label>اسم الفرع</label><input value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} placeholder="مثال: فرع مدينة نصر"/><label>الكود</label><input dir="ltr" value={edit.code} onChange={e => setEdit({...edit, code: e.target.value})} placeholder="nasr-01" disabled={!!edit.isLocal}/><div className="form-grid"><div><label>IP / اسم الجهاز</label><input dir="ltr" value={edit.ip} onChange={e => setEdit({...edit, ip: e.target.value})} placeholder="192.168.1.20"/></div><div><label>المنفذ</label><input dir="ltr" type="number" value={edit.port} onChange={e => setEdit({...edit, port: e.target.value})}/></div></div><label>ملاحظة</label><input value={edit.note || ''} onChange={e => setEdit({...edit, note: e.target.value})} placeholder="اختياري"/><div className="check-row"><label><input type="checkbox" checked={!!edit.isLocal} onChange={e => setEdit({...edit, isLocal: e.target.checked})}/> هذا هو الفرع الحالي</label><label><input type="checkbox" checked={!!edit.active} onChange={e => setEdit({...edit, active: e.target.checked})}/> نشط</label></div><div className="form-actions"><button className="primary" disabled={!edit.code || !edit.name} onClick={save}><Save size={16}/> حفظ</button><button className="secondary" onClick={close}>إلغاء</button></div></div>;
}

function OrderList({kind, orders, open, setOpen, recv, setRecv, send, receive, cancel, resend}: any) {
  return <section className="panel orders-panel"><div className="panel-title"><div><h2>{kind === 'in' ? 'الطلبيات الواردة' : 'الطلبيات الصادرة'}</h2><p>{kind === 'in' ? 'الطلبات المرسلة إليك من الفروع الأخرى.' : 'طلبات التحويل التي أنشأها هذا الفرع.'}</p></div>{kind === 'out' && <button className="secondary" onClick={resend}><RefreshCw size={16}/> مزامنة التأكيدات المعلقة</button>}</div><div className="table-wrap"><table><thead><tr><th>الطلبية</th><th>{kind === 'in' ? 'من فرع' : 'إلى فرع'}</th><th>التاريخ</th><th>الأصناف</th><th>الحالة</th><th></th></tr></thead><tbody>{orders.map((o: BranchOrder) => <React.Fragment key={o.BOID}><tr><td><b>#{o.BONo}</b><small>{o.CreatedBy || ''}</small></td><td>{kind === 'in' ? o.FromName : o.ToName}<small>{kind === 'in' ? o.FromCode : o.ToCode}</small></td><td dir="ltr">{o.BODay}<small>{o.BOTime}</small></td><td>{o.items.length} صنف · {qty(o.items.reduce((a, i) => a + i.qty, 0))}</td><td><StatusBadge status={o.Status}/></td><td><button className="outline-small" onClick={() => setOpen(open === o.BOID ? '' : o.BOID)}>{open === o.BOID ? 'إخفاء' : 'تفاصيل'}</button></td></tr>{open === o.BOID && <tr><td colSpan={6}><div className="order-detail">{o.Err && <div className="error inline-message">{o.Err}</div>}<table><thead><tr><th>الكود</th><th>الصنف</th><th>المطلوب</th><th>المستلم</th></tr></thead><tbody>{o.items.map(i => <tr key={i.code}><td dir="ltr">{i.code}</td><td>{i.iname || i.code}</td><td>{qty(i.qty)} {unit(i.unit)}</td><td>{kind === 'in' && o.Status === 'sent' ? <input type="number" min="0" max={i.qty} step="0.001" value={recv[o.BOID + ':' + i.code] ?? i.qty} onChange={e => setRecv({...recv, [o.BOID + ':' + i.code]: e.target.value})}/> : `${qty(i.rqty)} ${unit(i.unit)}`}</td></tr>)}</tbody></table>{o.Note && <p className="note"><b>ملاحظة:</b> {o.Note}</p>}<div className="detail-actions">{kind === 'in' && o.Status === 'sent' && <button className="primary" onClick={() => receive(o)}><CheckCircle2 size={16}/> تأكيد الاستلام</button>}{kind === 'out' && o.Status === 'draft' && <button className="primary" onClick={() => send(o)}><Send size={16}/> إرسال للفرع</button>}{['draft', 'sent'].includes(o.Status) && <button className="secondary danger-text" onClick={() => cancel(o)}><X size={16}/> إلغاء الطلبية</button>}</div></div></td></tr>}</React.Fragment>)}{!orders.length && <tr><td colSpan={6}><div className="empty-state">لا توجد طلبيات في هذا القسم.</div></td></tr>}</tbody></table></div></section>;
}
