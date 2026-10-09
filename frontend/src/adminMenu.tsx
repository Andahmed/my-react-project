import React, {useEffect, useMemo, useState} from 'react';
import {api} from './adminApi';

const blank = () => ({id: 0, name: '', cat: '', unit: 'pc', price: '', cost: '', icon: '', code: '', hidden: false, printer_ids: [] as number[]});

type Printer = {id: number; no: number; name: string; type: string; enabled: boolean};

export function Menu() {
  const [data, setData] = useState<any>({products: [], recipes: [], materials: []});
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('');
  const [edit, setEdit] = useState<any>(null);
  const [recipe, setRecipe] = useState<any[] | null>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    try {
      const [m, p] = await Promise.all([api('/api/products'), api('/api/printers')]);
      setData(m); setPrinters(p.printers || []);
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const cats = useMemo(() => Array.from(new Set<string>(data.products.map((p: any) => p.cat).filter(Boolean))), [data.products]);
  const rows = data.products.filter((p: any) => (!cat || p.cat === cat) && (!q || (p.name + ' ' + p.code).includes(q)));

  const run = async (fn: () => Promise<any>, done: string, after?: () => void) => {
    setMsg(''); setErr('');
    try { await fn(); setMsg(done); after && after(); await load(); } catch (e: any) { setErr(e.message); }
  };

  const open = (p: any) => {
    setEdit({...p, printer_ids: Array.isArray(p.printer_ids) ? p.printer_ids : []});
    setRecipe((data.recipes[String(p.id)] || []).map((r: any) => ({code: r.code, qty: String(r.qty)})));
  };
  const togglePrinter = (no: number) => {
    if (!edit) return;
    const ids = edit.printer_ids || [];
    setEdit({...edit, printer_ids: ids.includes(no) ? ids.filter((x: number) => x !== no) : [...ids, no]});
  };
  const save = () => run(async () => {
    const r = await api('/api/product/save', edit);
    if (recipe) {
      await api('/api/recipe', {pid: r.id, cost: Number(edit.cost) || 0,
        rows: recipe.filter(x => x.code && Number(x.qty) > 0).map(x => ({code: x.code, qty: Number(x.qty)}))});
    }
  }, 'تم حفظ الصنف وربط الطابعات', () => { setEdit(null); setRecipe(null); });

  const setR = (n: number, k: string, v: string) => setRecipe((recipe || []).map((x, i) => i === n ? {...x, [k]: v} : x));

  return (
    <div className="card">
      <div className="row">
        <h3 style={{margin: 0}}>الأصناف والمنيو</h3>
        <input placeholder="بحث بالاسم أو الكود" value={q} onChange={e => setQ(e.target.value)} />
        <select value={cat} onChange={e => setCat(e.target.value)}>
          <option value="">كل التصنيفات</option>
          {cats.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="primary" onClick={() => { setEdit(blank()); setRecipe([]); }}>+ صنف</button>
      </div>
      {msg && <div className="ok">{msg}</div>}{err && <div className="error">{err}</div>}

      <div style={{overflowX: 'auto'}}>
        <table>
          <thead><tr><th></th><th>الصنف</th><th>التصنيف</th><th>الوحدة</th><th>السعر</th><th>التكلفة</th><th>الهامش</th><th>الحالة</th><th></th></tr></thead>
          <tbody>
            {rows.map((p: any) => {
              const m = p.price > 0 ? Math.round((p.price - p.cost) / p.price * 100) : 0;
              return <tr key={p.id} style={{opacity: p.hidden ? 0.5 : 1}}>
                <td>{p.icon}</td><td>{p.name}</td><td>{p.cat}</td><td>{p.unit}</td><td>{p.price}</td><td>{p.cost}</td>
                <td style={{color: m < 0 ? '#dc3545' : undefined}}>{p.cost ? m + '%' : '—'}</td>
                <td>{p.hidden ? 'مخفي' : 'ظاهر'}</td><td><button onClick={() => open(p)}>تعديل</button></td>
              </tr>;
            })}
            {!rows.length && <tr><td colSpan={9}>مفيش أصناف</td></tr>}
          </tbody>
        </table>
      </div>

      {edit && <div className="card" style={{border: '1px solid #e5e7eb', marginTop: 16}}>
        <h3 style={{marginTop: 0}}>{edit.id ? 'تعديل: ' + edit.name : 'صنف جديد'}</h3>
        <div className="row">
          <input placeholder="الاسم" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})} />
          <input list="cat-list" placeholder="التصنيف / الجروب" value={edit.cat} onChange={e => setEdit({...edit, cat: e.target.value})} />
          <datalist id="cat-list">{cats.map(c => <option key={c} value={c} />)}</datalist>
          <select value={edit.unit} onChange={e => setEdit({...edit, unit: e.target.value})}><option value="pc">قطعة</option><option value="kg">كيلو</option></select>
        </div>
        <div className="row">
          <input type="number" placeholder="السعر" value={edit.price} onChange={e => setEdit({...edit, price: e.target.value})} />
          <input type="number" placeholder="التكلفة" value={edit.cost} onChange={e => setEdit({...edit, cost: e.target.value})} />
          <input placeholder="أيقونة (إيموجي)" value={edit.icon} onChange={e => setEdit({...edit, icon: e.target.value})} />
          <input placeholder="الكود (للأسماك = كود الخامة)" value={edit.code} onChange={e => setEdit({...edit, code: e.target.value})} />
          <label><input type="checkbox" style={{width: 'auto'}} checked={edit.hidden} onChange={e => setEdit({...edit, hidden: e.target.checked})} /> مخفي من الكاشير</label>
        </div>
        <h3>طباعة الصنف</h3>
        <div className="notice">بدون اختيار = طابعة الجروب. اختيار واحد أو أكثر يعمل Override للصنف ويطبعه على الطابعات المحددة فقط.</div>
        <div className="perms">{printers.filter(p => p.enabled && p.type === 'kitchen').map(p => <label key={p.no}><input type="checkbox" checked={(edit.printer_ids || []).includes(p.no)} onChange={() => togglePrinter(p.no)} /> رقم {p.no} — {p.name}</label>)}</div>
        {!printers.filter(p => p.enabled && p.type === 'kitchen').length && <div className="notice">أضف طابعات الأقسام أولًا من تبويب الطابعات.</div>}

        <h3>الريسبي (الخامات المستهلكة لكل وحدة بيع)</h3>
        {(recipe || []).map((r, n) => <div className="row" key={n}>
          <select value={r.code} onChange={e => setR(n, 'code', e.target.value)}><option value="">— خامة —</option>{data.materials.map((m: any) => <option key={m.code} value={m.code}>{m.code} — {m.name} ({m.unit})</option>)}</select>
          <input type="number" placeholder="الكمية" value={r.qty} onChange={e => setR(n, 'qty', e.target.value)} /><button onClick={() => setRecipe((recipe || []).filter((_, i) => i !== n))}>حذف</button>
        </div>)}
        <div className="row">
          <button onClick={() => setRecipe([...(recipe || []), {code: '', qty: ''}])}>+ خامة</button><button className="primary" onClick={save}>حفظ</button>
          {edit.id ? <button className="danger" onClick={() => { if (window.confirm('حذف الصنف نهائياً؟ الفواتير القديمة مش هتتأثر.')) run(() => api('/api/product/save', {id: edit.id, del: 1}), 'تم الحذف', () => { setEdit(null); setRecipe(null); }); }}>حذف</button> : null}
          <button onClick={() => { setEdit(null); setRecipe(null); }}>إلغاء</button>
        </div>
      </div>}
    </div>
  );
}
