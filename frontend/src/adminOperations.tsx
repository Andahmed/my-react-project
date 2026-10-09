import React, {useEffect, useState} from 'react';
import {api} from './adminApi';

const today = () => new Date().toISOString().slice(0, 10);
const money = (n: any) => `${Number(n || 0).toFixed(2)} ج`;

export function Reports() {
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState(today());
  const [kind, setKind] = useState('summary');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const load = async () => { try { setErr(''); setData(await api(`/api/report/advanced?type=${encodeURIComponent(kind)}&from=${from}&to=${to}`)); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  const rows = data?.rows || data?.data || [];
  return <div className="card">
    <div className="row"><h3 style={{margin: 0}}>التقارير</h3><button className="primary" onClick={load}>تحديث التقرير</button></div>
    <div className="row"><label>من<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label><label>إلى<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label><label>نوع التقرير<select value={kind} onChange={e => setKind(e.target.value)}><option value="summary">ملخص المبيعات</option><option value="sales">المبيعات</option><option value="types">أنواع الطلبات</option><option value="payplace">طرق ومكان الدفع</option><option value="returns">المرتجعات</option><option value="adjustments">الخصومات والضيافة</option><option value="riders">المناديب</option><option value="employees">الموظفون</option></select></label></div>
    {err && <div className="error">{err}</div>}
    {data && <div className="stats"><div>صافي المبيعات<b>{money(data.net ?? data.total)}</b></div><div>عدد الفواتير<b>{data.count ?? data.n ?? rows.length}</b></div><div>الخصم<b>{money(data.discount)}</b></div><div>المرتجعات<b>{money(data.returns)}</b></div></div>}
    {Array.isArray(rows) && rows.length > 0 && <div className="table-wrap"><table><thead><tr>{Object.keys(rows[0]).map(k => <th key={k}>{k}</th>)}</tr></thead><tbody>{rows.map((r: any, i: number) => <tr key={i}>{Object.values(r).map((v: any, n) => <td key={n}>{typeof v === 'number' ? money(v) : String(v ?? '—')}</td>)}</tr>)}</tbody></table></div>}
    {data && !rows.length && <div className="notice">تم تحميل التقرير، ولا توجد تفاصيل صفوف لهذا النوع.</div>}
  </div>;
}

export function TablesAdmin() {
  const [tables, setTables] = useState<any[]>([]); const [no, setNo] = useState(''); const [area, setArea] = useState('داخلي'); const [err, setErr] = useState('');
  const load = async () => { try { setTables(await api('/api/tables')); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  const add = async () => { try { const r = await api('/api/table/admin', {action: 'add', no: Number(no), area}); setTables(r.tables || []); setNo(''); } catch (e: any) { setErr(e.message); } };
  const toggle = async (t: any) => { try { await api('/api/table', {no: t.no, area: t.area, status: t.status === 'closed' ? 'open' : 'closed', waiter: '', guests: 0}); load(); } catch (e: any) { setErr(e.message); } };
  const del = async (t: any) => { if (!window.confirm(`حذف الطاولة ${t.no}؟`)) return; try { await api('/api/table/admin', {action: 'delete', no: t.no}); load(); } catch (e: any) { setErr(e.message); } };
  return <div className="card"><div className="row"><h3 style={{margin: 0}}>إدارة الطاولات</h3><button onClick={load}>تحديث</button></div><div className="row"><input type="number" placeholder="رقم الطاولة" value={no} onChange={e => setNo(e.target.value)} /><select value={area} onChange={e => setArea(e.target.value)}><option>داخلي</option><option>خارجي</option></select><button className="primary" disabled={!no} onClick={add}>+ إضافة</button></div>{err && <div className="error">{err}</div>}<table><thead><tr><th>الرقم</th><th>المنطقة</th><th>الحالة</th><th></th></tr></thead><tbody>{tables.map(t => <tr key={t.no}><td>{t.no}</td><td>{t.area}</td><td>{t.status}</td><td><button onClick={() => toggle(t)}>{t.status === 'closed' ? 'تفعيل' : 'تعطيل'}</button> <button className="danger" onClick={() => del(t)}>حذف</button></td></tr>)}</tbody></table></div>;
}

export function Backups() {
  const [data, setData] = useState<any>({files: []}); const [days, setDays] = useState('30'); const [msg, setMsg] = useState(''); const [err, setErr] = useState('');
  const load = async () => { try { setData(await api('/api/backups')); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  const now = async () => { try { const r = await api('/api/backup', {}); setMsg(`تم إنشاء ${r.name}`); load(); } catch (e: any) { setErr(e.message); } };
  const clean = async () => { if (!window.confirm(`حذف النسخ الأقدم من ${days} يوم؟`)) return; try { const r = await api('/api/backup/clean', {days: Number(days)}); setMsg(`تم حذف ${r.deleted || 0} نسخة`); load(); } catch (e: any) { setErr(e.message); } };
  return <div className="card"><div className="row"><h3 style={{margin: 0}}>النسخ الاحتياطي</h3><button className="primary" onClick={now}>أخذ نسخة الآن</button></div><p className="muted">مجلد النسخ: <span dir="ltr">{data.dir || '—'}</span></p><div className="row"><input type="number" value={days} onChange={e => setDays(e.target.value)} /><button onClick={clean}>تنظيف النسخ القديمة</button></div>{msg && <div className="notice">{msg}</div>}{err && <div className="error">{err}</div>}<table><thead><tr><th>الملف</th><th>التاريخ</th><th>الحجم</th><th></th></tr></thead><tbody>{(data.files || []).map((f: any) => <tr key={f.name}><td dir="ltr">{f.name}</td><td>{f.at}</td><td>{Math.round(f.size / 1024)} KB</td><td><a href={`/api/backup/download?name=${encodeURIComponent(f.name)}`} target="_blank" rel="noreferrer">تنزيل</a></td></tr>)}</tbody></table></div>;
}

export function SystemSettings() {
  const [s, setS] = useState<any>(null); const [msg, setMsg] = useState(''); const [err, setErr] = useState('');
  useEffect(() => { api('/api/state').then(setS).catch((e: any) => setErr(e.message)); }, []);
  if (!s) return <div className="card">جاري تحميل الإعدادات...</div>;
  const cfg = s.cfg || {};
  const save = async () => { try { await api('/api/state', {products: s.products || [], stock: s.stock || {}, cfg}); setMsg('تم حفظ الإعدادات'); } catch (e: any) { setErr(e.message); } };
  return <div className="card"><h3>إعدادات الفرع والنظام</h3><div className="form-grid"><label>اسم المطعم<input value={cfg.name || ''} onChange={e => setS({...s, cfg: {...cfg, name: e.target.value}})} /></label><label>اسم الفرع<input value={cfg.branchName || ''} onChange={e => setS({...s, cfg: {...cfg, branchName: e.target.value}})} /></label><label>كود الفرع<input value={cfg.branchCode || ''} onChange={e => setS({...s, cfg: {...cfg, branchCode: e.target.value}})} /></label><label>IP السيرفر<input dir="ltr" value={cfg.serverIp || ''} onChange={e => setS({...s, cfg: {...cfg, serverIp: e.target.value}})} /></label><label>تليفون المطعم<input value={cfg.phone || ''} onChange={e => setS({...s, cfg: {...cfg, phone: e.target.value}})} /></label><label>رسالة الفاتورة<input value={cfg.foot || ''} onChange={e => setS({...s, cfg: {...cfg, foot: e.target.value}})} /></label></div><button className="primary" onClick={save}>حفظ الإعدادات</button>{msg && <div className="notice">{msg}</div>}{err && <div className="error">{err}</div>}</div>;
}

export function LoginLog() {
  const [rows, setRows] = useState<any[]>([]); const [err, setErr] = useState('');
  const load = async () => { try { setRows(await api('/api/loginlog')); setErr(''); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  return <div className="card"><div className="row"><h3 style={{margin: 0}}>سجل الدخول</h3><button onClick={load}>تحديث</button></div>{err && <div className="error">{err}</div>}<div style={{overflowX:'auto'}}><table><thead><tr><th>التاريخ والوقت</th><th>المستخدم</th><th>الحدث</th><th>التفاصيل</th></tr></thead><tbody>{rows.map((r:any,i)=><tr key={r.id || i}>{Object.values(r).slice(0,4).map((v:any,j)=><td key={j}>{String(v ?? '—')}</td>)}</tr>)}{!rows.length && <tr><td colSpan={4}>لا توجد سجلات أو تعذر تحميلها.</td></tr>}</tbody></table></div></div>;
}

export function SystemTools() {
  const [msg, setMsg] = useState(''); const [err, setErr] = useState(''); const [info, setInfo] = useState<any>(null); const [busy, setBusy] = useState(false);
  useEffect(() => { api('/api/netinfo').then(setInfo).catch((e:any) => setErr(e.message)); }, []);
  const exportData = async () => {
    setErr(''); setMsg('');
    try {
      const d = await api('/api/state');
      const payload = {products:d.products || [], sales:d.sales || [], archive:d.archive || [], stock:d.stock || {}, cfg:d.cfg || {}};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json;charset=utf-8'});
      const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href=url; a.download=`istakoza-export-${new Date().toISOString().slice(0,10)}.json`; a.click(); URL.revokeObjectURL(url); setMsg('تم تجهيز ملف التصدير. احتفظ به في مكان آمن.');
    } catch(e:any) { setErr(e.message); }
  };
  const importData = async (file?: File) => {
    if (!file) return; setErr(''); setMsg('');
    try {
      const d = JSON.parse(await file.text());
      if (!Array.isArray(d.products) || !d.cfg || typeof d.stock !== 'object') throw new Error('صيغة الملف غير صحيحة أو الملف لا يحتوي على products وcfg وstock');
      if (!window.confirm('سيتم استيراد البيانات واستبدال بيانات التشغيل التي يدعمها الاستيراد. يفضّل أخذ نسخة احتياطية أولاً. متابعة؟')) return;
      setBusy(true); await api('/api/import', d); setMsg('تم إرسال البيانات للاستيراد. حدّث الشاشة للتحقق من النتيجة.');
    } catch(e:any) { setErr(e.message || 'ملف غير صالح'); } finally { setBusy(false); }
  };
  const reset = async () => {
    if (!window.confirm('تحذير نهائي: تصفير البيانات التشغيلية قد يمسح المبيعات والمخزون. هل أخذت نسخة احتياطية وتريد المتابعة؟')) return;
    if (!window.confirm('تأكيد أخير: تنفيذ التصفير الآن؟')) return;
    setErr(''); setMsg(''); setBusy(true); try { await api('/api/reset', {}); setMsg('تم تنفيذ طلب التصفير.'); } catch(e:any) { setErr(e.message); } finally { setBusy(false); }
  };
  return <div className="card"><h3 style={{marginTop:0}}>معلومات النظام وأدوات البيانات</h3>{err && <div className="error">{err}</div>}{msg && <div className="notice">{msg}</div>}
    {info && <table><tbody><tr><th>الإصدار</th><td>{info.ver || '—'}</td></tr><tr><th>اسم الجهاز</th><td>{info.host || '—'}</td></tr><tr><th>المنفذ</th><td>{info.port || '—'}</td></tr><tr><th>قاعدة البيانات</th><td dir="ltr">{info.db || '—'}</td></tr><tr><th>عنوان الخادم</th><td dir="ltr">{(info.ips || []).join(', ') || '—'}</td></tr></tbody></table>}
    <div className="row" style={{marginTop:16}}><button className="primary" onClick={exportData} disabled={busy}>تصدير JSON</button><label className="button-like">استيراد JSON<input type="file" accept="application/json,.json" disabled={busy} style={{display:'block',maxWidth:230}} onChange={e=>importData(e.target.files?.[0])}/></label><button className="danger" onClick={reset} disabled={busy}>تصفير البيانات</button></div>
    <p className="muted">التصدير من واجهة React يصدر الحقول التي يعيدها /api/state فقط؛ استخدم النسخ الاحتياطي الكامل قبل الاستيراد أو التصفير.</p>
  </div>;
}


const DEFAULT_CUSTOMER_TEMPLATE = `<div style="text-align:center"><h2>{{name}}</h2><div>{{phone}}</div></div>
<div style="text-align:center;font-size:20px;font-weight:bold">أوردر #{{no}}</div>
<div style="text-align:center">{{type}} · {{date}} {{time}}</div>
{{#delivery}}<hr><div>العميل: {{custName}}</div><div>الهاتف: {{custPhone}}</div><div>العنوان: {{custAddr}}</div>{{/delivery}}
<hr>{{items}}<hr>
<div>المجموع: {{subtotal}} ج</div><div>الخصم: {{discount}} ج</div><div>التوصيل: {{feeAmt}} ج</div><div style="font-size:18px;font-weight:bold">الإجمالي: {{total}} ج</div>
<div style="text-align:center;margin-top:12px">{{footer}}</div>`;
const DEFAULT_KITCHEN_TEMPLATE = `<div style="text-align:center;font-size:22px;font-weight:bold">أوردر #{{no}}</div>
<div style="text-align:center;font-size:18px">{{type}} · {{time}}</div>
{{#delivery}}<div>العميل: {{custName}} · {{custPhone}}</div><div>{{custAddr}}</div>{{/delivery}}
<hr>{{items}}<hr>{{comment}}`;
const TEMPLATE_TOKENS: [string,string][] = [
  ['{{name}}','اسم المطعم'],['{{phone}}','هاتف المطعم'],['{{no}}','رقم الفاتورة'],['{{type}}','نوع الطلب'],['{{date}}','التاريخ'],['{{time}}','الوقت'],
  ['{{custName}}','اسم العميل'],['{{custPhone}}','هاتف العميل'],['{{custAddr}}','عنوان العميل'],['{{items}}','الأصناف والكميات'],['{{subtotal}}','المجموع'],['{{discount}}','الخصم'],['{{feeAmt}}','رسوم التوصيل'],['{{total}}','الإجمالي'],['{{comment}}','ملاحظة الطلب'],['{{footer}}','رسالة أسفل الفاتورة'],['{{#delivery}}...{{/delivery}}','قسم يظهر لطلبات الدليفري والاستلام']
];
export function ReceiptDesigner() {
  const [data, setData] = useState<any>(null);
  const [kind, setKind] = useState<'customer'|'kitchen'>('customer');
  const [msg, setMsg] = useState(''); const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api('/api/state').then((d:any) => {
    const cfg = {...(d.cfg || {})};
    if (!cfg.tplCustomer) cfg.tplCustomer = DEFAULT_CUSTOMER_TEMPLATE;
    if (!cfg.tplKitchen) cfg.tplKitchen = DEFAULT_KITCHEN_TEMPLATE;
    if (!cfg.paperWidth) cfg.paperWidth = 80;
    if (!cfg.printFontSize) cfg.printFontSize = 14;
    if (cfg.feed === undefined) cfg.feed = 10;
    setData({...d, cfg});
  }).catch((e:any) => setErr(e.message)); }, []);
  if (!data) return <div className="card">جاري تحميل إعدادات الطباعة...</div>;
  const cfg = data.cfg || {};
  const field = kind === 'customer' ? 'tplCustomer' : 'tplKitchen';
  const sample = (cfg[field] || '').replace(/\{\{name\}\}/g, cfg.name || 'إستاكوزا POS').replace(/\{\{phone\}\}/g, cfg.phone || '01000000000').replace(/\{\{no\}\}/g, '0123').replace(/\{\{type\}\}/g, 'دليفري').replace(/\{\{date\}\}/g, new Date().toLocaleDateString('ar-EG')).replace(/\{\{time\}\}/g, '08:30 م').replace(/\{\{custName\}\}/g, 'أحمد محمد').replace(/\{\{custPhone\}\}/g, '01000000000').replace(/\{\{custAddr\}\}/g, 'شارع التحرير').replace(/\{\{subtotal\}\}/g, '250.00').replace(/\{\{discount\}\}/g, '10.00').replace(/\{\{feeAmt\}\}/g, '20.00').replace(/\{\{total\}\}/g, '260.00').replace(/\{\{footer\}\}/g, cfg.foot || 'شكراً لتعاملكم معنا').replace(/\{\{comment\}\}/g, 'بدون شطة').replace(/\{\{items\}\}/g, '<div style="display:flex;justify-content:space-between;border-bottom:1px dashed #aaa;padding:5px 0"><span>سمك بلطي × 2</span><b>240.00</b></div><div style="display:flex;justify-content:space-between;padding:5px 0"><span>طحينة × 1</span><b>10.00</b></div>').replace(/\{\{#\w+\}\}|\{\{\/\w+\}\}/g, '');
  const save = async () => { setBusy(true); setErr(''); setMsg(''); try { await api('/api/state', {products:data.products || [], stock:data.stock || {}, cfg}); setMsg('تم حفظ تصميم وإعدادات الطباعة في قاعدة البيانات الحالية.'); } catch(e:any) { setErr(e.message); } finally { setBusy(false); } };
  const reset = () => { if (!window.confirm('استعادة التصميم الافتراضي لهذا النوع؟')) return; setData({...data, cfg:{...cfg, [field]: kind === 'customer' ? DEFAULT_CUSTOMER_TEMPLATE : DEFAULT_KITCHEN_TEMPLATE}}); };
  return <div className="card">
    <div className="row"><h3 style={{margin:0}}>مصمم رسيت العميل والمطبخ</h3><button className="primary" disabled={busy} onClick={save}>حفظ التصميم</button></div>
    <p className="muted">اكتب HTML بسيطًا في القالب، واستخدم الرموز المتاحة أدناه. المعاينة تجريبية ولا تطبع فاتورة حقيقية.</p>
    {err && <div className="error">{err}</div>}{msg && <div className="notice">{msg}</div>}
    <div className="row"><button className={kind==='customer'?'primary':''} onClick={()=>setKind('customer')}>رسيت العميل</button><button className={kind==='kitchen'?'primary':''} onClick={()=>setKind('kitchen')}>رسيت المطبخ</button><button onClick={reset}>استعادة الافتراضي</button></div>
    <div className="form-grid" style={{marginTop:12}}>
      <label>عرض الورق (مم)<select value={Number(cfg.paperWidth || 80)} onChange={e=>setData({...data,cfg:{...cfg,paperWidth:Number(e.target.value)}})}><option value={58}>58 مم</option><option value={80}>80 مم</option><option value={110}>110 مم</option></select></label>
      <label>حجم الخط (px)<input type="number" min={8} max={28} value={Number(cfg.printFontSize || 14)} onChange={e=>setData({...data,cfg:{...cfg,printFontSize:Math.max(8,Math.min(28,Number(e.target.value)||14))}})}/></label>
      <label>مسافة قبل القص (مم)<input type="number" min={0} max={80} value={Number(cfg.feed ?? 10)} onChange={e=>setData({...data,cfg:{...cfg,feed:Math.max(0,Math.min(80,Number(e.target.value)||0))}})}/></label>
      <label>عرض اسم الكاشير في الفاتورة<select value={String(cfg.rcpCashier ?? 1)} onChange={e=>setData({...data,cfg:{...cfg,rcpCashier:Number(e.target.value)}})}><option value="1">نعم</option><option value="0">لا</option></select></label>
    </div>
    <div className="form-grid" style={{marginTop:12,alignItems:'start'}}>
      <div><label>قالب HTML — {kind==='customer'?'العميل':'المطبخ'}<textarea rows={16} dir="ltr" spellCheck={false} style={{fontFamily:'monospace',width:'100%',minHeight:300}} value={cfg[field] || ''} onChange={e=>setData({...data,cfg:{...cfg,[field]:e.target.value}})}/></label><div className="perms" style={{marginTop:8}}>{TEMPLATE_TOKENS.map(([token,label])=><button key={token} type="button" onClick={()=>setData({...data,cfg:{...cfg,[field]:(cfg[field]||'')+'\n'+token}})} title={token}>{label}</button>)}</div></div>
      <div><b>معاينة تقريبية</b><div style={{background:'#fff',color:'#111',border:'1px dashed #94a3b8',padding:12,marginTop:8,width:`${Math.min(100,Number(cfg.paperWidth||80)*1.1)}%`,maxWidth:420,minHeight:300,fontSize:Number(cfg.printFontSize||14),overflowWrap:'anywhere'}} dangerouslySetInnerHTML={{__html:sample}}/><p className="muted">الرموز الشرطية المدعومة: <code>{'{{#delivery}}'}</code> و<code>{'{{/delivery}}'}</code>. يتم حفظ القالب كنص داخل إعدادات الفرع، ولا تُنشأ قاعدة بيانات جديدة.</p></div>
    </div>
  </div>;
}
