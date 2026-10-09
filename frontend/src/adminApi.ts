export const TOKEN_KEY = 'istakoza-admin-token';

export async function api(url: string, body?: any): Promise<any> {
  const token = sessionStorage.getItem(TOKEN_KEY) || '';
  const headers: Record<string, string> = {};
  if (token) headers['X-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(url, {method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body)});
  let data: any = {};
  try { data = await r.json(); } catch { /* رد فاضي */ }
  if (r.status === 401) { sessionStorage.removeItem(TOKEN_KEY); throw new Error('انتهت الجلسة — سجّل الدخول تاني'); }
  if (!r.ok) throw new Error(data.error || ('خطأ ' + r.status));
  return data;
}
