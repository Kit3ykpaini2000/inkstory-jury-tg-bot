/**
 * api.js — обёртка над fetch для запросов к FastAPI
 */

const BASE = '';

function getInitData() {
  if (window.Telegram?.WebApp?.initData) {
    return window.Telegram.WebApp.initData;
  }
  return window.__DEV_INIT_DATA__ || '';
}

async function request(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' };
  const initData = getInitData();
  if (initData) headers['X-Telegram-Init-Data'] = initData;

  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);

  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (e) {
    throw new Error('Нет соединения с сервером');
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {}
    throw new Error(String(detail));
  }

  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res;
}

export const api = {
  // ── Жюри ──
  getMe()              { return request('GET',  '/api/jury/me'); },
  getFullStats()       { return request('GET',  '/api/jury/stats'); },
  getActivePost()      { return request('GET',  '/api/jury/active'); },
  getNextPost()        { return request('GET',  '/api/jury/next'); },
  getPostText()        { return request('GET',  '/api/jury/post-text'); },
  submitResult(w, e)   { return request('POST', '/api/jury/submit',  { words: w, errors: e }); },
  skipPost(reason)     { return request('POST', '/api/jury/skip',    { reason }); },
  rejectPost(reason)   { return request('POST', '/api/jury/reject',  { reason }); },
  aiCheck()            { return request('GET',  '/api/jury/ai-check'); },

  // ── Админ — жюри ──
  getReviewers()             { return request('GET',    '/api/admin/reviewers'); },
  verifyReviewer(tgid)       { return request('POST',   `/api/admin/reviewers/${tgid}/verify`); },
  unverifyReviewer(tgid)     { return request('POST',   `/api/admin/reviewers/${tgid}/unverify`); },
  makeAdmin(tgid)            { return request('POST',   `/api/admin/reviewers/${tgid}/make-admin`); },
  removeAdmin(tgid)          { return request('POST',   `/api/admin/reviewers/${tgid}/remove-admin`); },
  deleteReviewer(tgid)       { return request('DELETE', `/api/admin/reviewers/${tgid}`); },

  // ── Админ — посты / очередь ──
  getPosts(status, limit = 50) {
    const q = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    return request('GET', `/api/admin/posts${q}`);
  },
  getQueue()           { return request('GET',  '/api/admin/queue'); },

  // ── Админ — дни ──
  getDays()            { return request('GET',  '/api/admin/days'); },
  createDay(label)     { return request('POST', '/api/admin/days', { label: label || null }); },
  deleteDay(id, to)    {
    const q = to ? `?transfer_to=${to}` : '';
    return request('DELETE', `/api/admin/days/${id}${q}`);
  },

  // ── Админ — прочее ──
  getAdminStats()      { return request('GET',  '/api/admin/stats'); },
  getLogs(n = 100)     { return request('GET',  `/api/admin/logs?n=${n}`); },
  exportExcel()        { return request('GET',  '/api/admin/export'); },
};
