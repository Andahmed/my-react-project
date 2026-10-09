import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './admin.css';

import {api, TOKEN_KEY} from './adminApi';
import {Stock, PurchaseOrders, Stocktake} from './adminStore';
import {Menu} from './adminMenu';
import {Delivery} from './adminDelivery';
import {Printers} from './adminPrinters';
import {Branches} from './adminBranches';
import {Reports, TablesAdmin, Backups, SystemSettings, LoginLog, SystemTools, ReceiptDesigner} from './adminOperations';

const FRONT: [string, string][] = [
  ['invoice_options', 'خيارات الفاتورة'], ['disc', 'خصم'], ['complimentary', 'ضيافة'], ['tables', 'الطاولات'],
  ['del', 'الدليفري'], ['reprint', 'إعادة طباعة'], ['price', 'تعديل السعر'], ['void', 'إلغاء فاتورة'],
  ['edit', 'تعديل فاتورة'], ['return', 'مرتجع'], ['cash', 'دفع كاش'], ['visa', 'دفع فيزا'],
  ['mastercard', 'دفع ماستركارد'], ['meeza', 'دفع ميزة'], ['wallet', 'محفظة إلكترونية'],
  ['transfer', 'تحويل بنكي'], ['other', 'دفع آخر'], ['send_kitchen', 'إرسال للمطبخ'],
];
const BACK: [string, string][] = [
  ['tables_manage', 'إدارة الطاولات'], ['stock', 'المخزن'], ['rep', 'التقارير'], ['close', 'قفل اليوم'],
  ['printer', 'الطابعات'], ['po', 'أوامر الشراء والفروع'], ['dlv', 'الدليفري'], ['dlvPay', 'محاسبة المناديب'],
  ['users_manage', 'إدارة الموظفين'], ['permissions_manage', 'إدارة الصلاحيات'], ['backup', 'النسخ الاحتياطي'],
  ['payment_reports', 'تقارير الدفع'], ['adjustment_reports', 'تقارير الخصومات'],
];

function Login({onDone}: {onDone: () => void}) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api('/api/login', {u, p});
      if (r.role !== 'admin') throw new Error('الدخول للمدير فقط');
      sessionStorage.setItem(TOKEN_KEY, r.token);
      onDone();
    } catch (e: any) {
      setErr(e.message || 'فشل الدخول');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <div className="card">
        <h1>إستاكوزا — لوحة التحكم</h1>
        <input placeholder="كود الموظف" value={u} onChange={e => setU(e.target.value)} autoFocus />
        <input type="password" placeholder="كلمة السر" value={p} onChange={e => setP(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') go(); }} />
        <button className="primary" disabled={busy || !u} onClick={go}>دخول</button>
        {err && <div className="error">{err}</div>}
      </div>
    </div>
  );
}

function Employees() {
  const [users, setUsers] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    try {
      setUsers(await api('/api/users'));
      setJobs(await api('/api/jobs'));
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const blank = () => setEdit({emp: null, name: '', job: jobs.length ? String(jobs[jobs.length - 1].job) : '', phone: ''});

  const save = async (extra: any = {}) => {
    setErr(''); setMsg('');
    try {
      const body: any = {name: edit.name, job: edit.job, phone: edit.phone || '', ...extra};
      if (edit.emp) body.emp = edit.emp;
      const r = await api('/api/users', body);
      setMsg(r.pw ? `تم الحفظ — كود الموظف: ${r.emp} — كلمة السر: ${r.pw} (اكتبها دلوقتي، مش هتظهر تاني)` : 'تم الحفظ');
      setEdit(null);
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  const del = async (u: any) => {
    if (!window.confirm(`حذف الموظف ${u.full || u.u}؟`)) return;
    setErr(''); setMsg('');
    try {
      await api('/api/users', {emp: u.code, del: 1});
      setMsg('تم الحذف');
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="card">
      <div className="row">
        <h3 style={{margin: 0}}>الموظفين ({users.length})</h3>
        <button className="primary" onClick={blank}>+ موظف جديد</button>
      </div>
      {msg && <div className="notice">{msg}</div>}
      {err && <div className="error">{err}</div>}
      {edit && (
        <div className="card" style={{background: '#f8fafc', margin: '10px 0'}}>
          <b>{edit.emp ? `تعديل الموظف ${edit.emp}` : 'موظف جديد'}</b>
          <div className="row">
            <input placeholder="الاسم" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} />
            <select value={edit.job} onChange={e => setEdit({...edit, job: e.target.value})}>
              <option value="">— الوظيفة —</option>
              {jobs.map(j => <option key={j.job} value={String(j.job)}>{j.name}</option>)}
            </select>
            <input placeholder="التليفون (اختياري)" value={edit.phone} onChange={e => setEdit({...edit, phone: e.target.value})} />
          </div>
          <div className="row">
            <button className="primary" disabled={!edit.name || !edit.job} onClick={() => save()}>حفظ</button>
            {edit.emp && <button onClick={() => { if (window.confirm('توليد كلمة سر جديدة؟')) save({regen: 1}); }}>كلمة سر جديدة</button>}
            <button onClick={() => setEdit(null)}>إلغاء</button>
          </div>
        </div>
      )}
      <table>
        <thead><tr><th>الكود</th><th>الاسم</th><th>الوظيفة</th><th>التليفون</th><th></th></tr></thead>
        <tbody>
          {users.map(u => (
            <tr key={u.code}>
              <td>{u.code}</td><td>{u.full}</td><td>{u.jobName}</td><td>{u.phone}</td>
              <td>
                <button onClick={() => setEdit({emp: u.code, name: u.full, job: String(u.job), phone: u.phone || ''})}>تعديل</button>{' '}
                <button className="danger" onClick={() => del(u)}>حذف</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PermGroup({title, list, allowed, setAllowed}: {title: string; list: [string, string][]; allowed: string[]; setAllowed: (a: string[]) => void}) {
  const toggle = (k: string) => setAllowed(allowed.includes(k) ? allowed.filter(x => x !== k) : [...allowed, k]);
  return (
    <>
      <h3>{title}</h3>
      <div className="perms">
        {list.map(([k, label]) => (
          <label key={k}><input type="checkbox" checked={allowed.includes(k)} onChange={() => toggle(k)} /> {label}</label>
        ))}
      </div>
    </>
  );
}

function Permissions() {
  const [users, setUsers] = useState<any[]>([]);
  const [emp, setEmp] = useState('');
  const [front, setFront] = useState<string[]>([]);
  const [back, setBack] = useState<string[]>([]);
  const [src, setSrc] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => { api('/api/users').then(setUsers).catch((e: any) => setErr(e.message)); }, []);

  const apply = (r: any) => {
    const on = (g: any) => Object.keys(g || {}).filter(k => Number(g[k]) === 1);
    setFront(on(r.front)); setBack(on(r.back));
  };

  const pick = async (v: string) => {
    setEmp(v); setMsg(''); setErr(''); setFront([]); setBack([]);
    if (!v) return;
    try { apply(await api('/api/permissions?emp=' + encodeURIComponent(v))); } catch (e: any) { setErr(e.message); }
  };

  const save = async () => {
    setMsg(''); setErr('');
    try {
      await api('/api/permissions', {emp: Number(emp), group: 'front', allowed: front});
      const r = await api('/api/permissions', {emp: Number(emp), group: 'back', allowed: back});
      apply(r);
      setMsg('تم حفظ الصلاحيات');
    } catch (e: any) { setErr(e.message); }
  };

  const copy = async () => {
    if (!src || !emp) return;
    if (!window.confirm('نسخ كل صلاحيات الموظف المختار فوق صلاحيات الموظف الحالي؟')) return;
    setMsg(''); setErr('');
    try {
      apply(await api('/api/permissions', {action: 'copy', src: Number(src), dst: Number(emp)}));
      setMsg('تم نسخ الصلاحيات');
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="card">
      <h3 style={{marginTop: 0}}>صلاحيات الموظفين</h3>
      <div className="row">
        <select value={emp} onChange={e => pick(e.target.value)}>
          <option value="">— اختار الموظف —</option>
          {users.map(u => <option key={u.code} value={String(u.code)}>{u.code} — {u.full} ({u.jobName})</option>)}
        </select>
      </div>
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
      {emp && front.length === 0 && back.length === 0 && (
        <div className="notice">مفيش صلاحيات خاصة محفوظة للموظف ده — بياخد صلاحيات وظيفته الافتراضية. أول ما تضغط «حفظ» هتتحول لصلاحيات خاصة بيه.</div>
      )}
      {emp && (
        <>
          <PermGroup title="صلاحيات الكاشير" list={FRONT} allowed={front} setAllowed={setFront} />
          <PermGroup title="صلاحيات الإدارة" list={BACK} allowed={back} setAllowed={setBack} />
          <div className="row">
            <button className="primary" onClick={save}>حفظ الصلاحيات</button>
            <select value={src} onChange={e => setSrc(e.target.value)}>
              <option value="">— انسخ صلاحيات من موظف —</option>
              {users.filter(u => String(u.code) !== emp).map(u => <option key={u.code} value={String(u.code)}>{u.code} — {u.full}</option>)}
            </select>
            <button disabled={!src} onClick={copy}>نسخ</button>
          </div>
        </>
      )}
    </div>
  );
}

function App() {
  const [authed, setAuthed] = useState(!!sessionStorage.getItem(TOKEN_KEY));
  const [tab, setTab] = useState<'emp' | 'perm' | 'menu' | 'dlv' | 'stock' | 'po' | 'branches' | 'count' | 'printers' | 'reports' | 'tables' | 'backup' | 'settings' | 'receipt' | 'loginlog' | 'system'>('emp');
  const logout = () => { sessionStorage.removeItem(TOKEN_KEY); setAuthed(false); };
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return (
    <>
      <header>
        <h2>إستاكوزا — لوحة التحكم (React)</h2>
        <span>
          <a href="/" style={{color: '#cbd5e1', marginInlineEnd: 16}}>الشاشة القديمة</a>
          <button onClick={logout}>خروج</button>
        </span>
      </header>
      <nav>
        <button className={tab === 'emp' ? 'active' : ''} onClick={() => setTab('emp')}>الموظفين</button>
        <button className={tab === 'perm' ? 'active' : ''} onClick={() => setTab('perm')}>الصلاحيات</button>
        <button className={tab === 'menu' ? 'active' : ''} onClick={() => setTab('menu')}>المنيو</button>
        <button className={tab === 'dlv' ? 'active' : ''} onClick={() => setTab('dlv')}>الدليفري</button>
        <button className={tab === 'stock' ? 'active' : ''} onClick={() => setTab('stock')}>المخزن</button>
        <button className={tab === 'po' ? 'active' : ''} onClick={() => setTab('po')}>أوامر الشراء</button>
        <button className={tab === 'branches' ? 'active' : ''} onClick={() => setTab('branches')}>الفروع والتحويلات</button>
        <button className={tab === 'count' ? 'active' : ''} onClick={() => setTab('count')}>جرد الشهر</button>
        <button className={tab === 'printers' ? 'active' : ''} onClick={() => setTab('printers')}>الطابعات</button>
        <button className={tab === 'receipt' ? 'active' : ''} onClick={() => setTab('receipt')}>تصميم الرسيت</button>
        <button className={tab === 'reports' ? 'active' : ''} onClick={() => setTab('reports')}>التقارير</button>
        <button className={tab === 'tables' ? 'active' : ''} onClick={() => setTab('tables')}>الطاولات</button>
        <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>إعدادات الفرع</button>
        <button className={tab === 'backup' ? 'active' : ''} onClick={() => setTab('backup')}>النسخ الاحتياطي</button>
        <button className={tab === 'loginlog' ? 'active' : ''} onClick={() => setTab('loginlog')}>سجل الدخول</button>
        <button className={tab === 'system' ? 'active' : ''} onClick={() => setTab('system')}>أدوات النظام</button>
      </nav>
      <main>
        {tab === 'emp' && <Employees />}
        {tab === 'perm' && <Permissions />}
        {tab === 'menu' && <Menu />}
        {tab === 'dlv' && <Delivery />}
        {tab === 'stock' && <Stock />}
        {tab === 'po' && <PurchaseOrders />}
        {tab === 'branches' && <Branches />}
        {tab === 'count' && <Stocktake />}
        {tab === 'printers' && <Printers />}
        {tab === 'receipt' && <ReceiptDesigner />}
        {tab === 'reports' && <Reports />}
        {tab === 'tables' && <TablesAdmin />}
        {tab === 'settings' && <SystemSettings />}
        {tab === 'backup' && <Backups />}
        {tab === 'loginlog' && <LoginLog />}
        {tab === 'system' && <SystemTools />}
      </main>
    </>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
