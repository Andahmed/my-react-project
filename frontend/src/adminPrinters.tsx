import React, {useEffect, useState} from 'react';
import {api} from './adminApi';

type Printer = {id?: number; no?: number; name: string; ip: string; port: number; type: string; enabled: boolean; groups: string[]};
type Data = {printers: Printer[]; groups: string[]; products: Record<string, number[]>};
const blank = (): Printer => ({name: '', ip: '', port: 9100, type: 'kitchen', enabled: true, groups: []});

export function Printers() {
  const [data, setData] = useState<Data>({printers: [], groups: [], products: {}});
  const [edit, setEdit] = useState<Printer | null>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const load = async () => { try { setData(await api('/api/printers')); setErr(''); } catch (e: any) { setErr(e.message); } };
  useEffect(() => { load(); }, []);
  const save = async () => { if (!edit) return; setMsg(''); setErr(''); try { await api('/api/printer/save', edit); setEdit(null); setMsg('تم حفظ الطابعة وترقيمها تلقائيًا'); await load(); } catch (e: any) { setErr(e.message); } };
  const remove = async (p: Printer) => { if (!window.confirm(`حذف الطابعة ${p.name} رقم ${p.no}؟`)) return; try { await api('/api/printer/save', {id: p.id, del: 1}); setMsg('تم حذف الطابعة'); await load(); } catch (e: any) { setErr(e.message); } };
  const test = async (p: Printer) => { setMsg(''); setErr(''); try { const raw = String.fromCharCode(27, 64, 27, 97, 1) + `ISTAKOZA PRINTER #${p.no}\n${p.ip}:${p.port}\n` + String.fromCharCode(27, 100, 3, 29, 86, 0); await api('/api/print', {ip: p.ip, port: p.port || 9100, data: btoa(raw)}); setMsg(`تم إرسال اختبار إلى ${p.name}`); } catch (e: any) { setErr(e.message); } };
  const toggleGroup = (g: string) => { if (!edit) return; const groups = edit.groups.includes(g) ? edit.groups.filter(x => x !== g) : [...edit.groups, g]; setEdit({...edit, groups}); };

  return <div className="card">
    <div className="row"><h3 style={{margin: 0}}>طابعات الأقسام</h3><button className="primary" onClick={() => setEdit(blank())}>+ إضافة طابعة</button></div>
    <p className="notice">الترقيم تلقائيًا من 1 ويزيد مع كل طابعة جديدة. طابعة المطبخ تطبع الأصناف المرتبطة بجروباتها فقط.</p>
    {msg && <div className="ok">{msg}</div>}{err && <div className="error">{err}</div>}
    {edit && <div className="card" style={{background: '#f8fafc', margin: '12px 0'}}>
      <h3 style={{marginTop: 0}}>{edit.id ? `تعديل الطابعة رقم ${edit.no}` : 'طابعة جديدة'}</h3>
      <div className="row"><input placeholder="اسم الطابعة / القسم" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} /><input placeholder="IP الطابعة" value={edit.ip} onChange={e => setEdit({...edit, ip: e.target.value})} /><input type="number" min={1} max={65535} placeholder="Port" value={edit.port} onChange={e => setEdit({...edit, port: Number(e.target.value)})} /><select value={edit.type} onChange={e => setEdit({...edit, type: e.target.value})}><option value="kitchen">مطبخ / قسم</option><option value="customer">كاشير / عميل</option></select><label><input type="checkbox" style={{width: 'auto'}} checked={edit.enabled} onChange={e => setEdit({...edit, enabled: e.target.checked})} /> مفعلة</label></div>
      {edit.type === 'kitchen' && <><b>الجروبات التي تطبع على هذه الطابعة</b><div className="perms" style={{marginTop: 8}}>{data.groups.map(g => <label key={g}><input type="checkbox" checked={edit.groups.includes(g)} onChange={() => toggleGroup(g)} /> {g}</label>)}</div><div className="notice">لو لم تختر جروبات، ستعمل الطابعة كطابعة مطبخ عامة. ويمكن تغيير طابعة صنف بعينه من شاشة المنيو.</div></>}
      <div className="row"><button className="primary" disabled={!edit.name.trim()} onClick={save}>حفظ</button><button onClick={() => setEdit(null)}>إلغاء</button></div>
    </div>}
    <table><thead><tr><th>الترتيب</th><th>الاسم / القسم</th><th>IP</th><th>Port</th><th>النوع</th><th>الجروبات</th><th>الحالة</th><th>إجراءات</th></tr></thead><tbody>{data.printers.map(p => <tr key={p.id}><td><b>{p.no}</b></td><td>{p.name}</td><td>{p.ip || '—'}</td><td>{p.port}</td><td>{p.type === 'kitchen' ? 'مطبخ' : 'كاشير'}</td><td>{p.groups.join('، ') || (p.type === 'kitchen' ? 'كل الجروبات' : '—')}</td><td>{p.enabled ? 'مفعلة' : 'متوقفة'}</td><td><button onClick={() => test(p)} disabled={!p.ip}>اختبار</button>{' '}<button onClick={() => setEdit({...p})}>تعديل</button>{' '}<button className="danger" onClick={() => remove(p)}>حذف</button></td></tr>)}{!data.printers.length && <tr><td colSpan={8}>لا توجد طابعات مضافة</td></tr>}</tbody></table>
  </div>;
}
