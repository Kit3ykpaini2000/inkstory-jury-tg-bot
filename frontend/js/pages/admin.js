/**
 * pages/admin.js — страница администратора
 */

import { api } from '../api.js';
import { showToast, setFloatBar } from '../app.js';

export function renderAdmin(container) {
  container.innerHTML = '<div class="loader"><div class="spinner"></div> загрузка...</div>';
  setFloatBar(false);
  loadAdmin(container);
}

async function loadAdmin(container) {
  try {
    const [reviewers, stats] = await Promise.all([api.getReviewers(), api.getAdminStats()]);
    renderAdminPage(container, reviewers, stats);
  } catch (e) {
    if (e.message === 'Admin access required') {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🔒</div>
          <div class="empty-title">Нет доступа</div>
          <div class="empty-sub">Эта страница только для администраторов</div>
        </div>`;
    } else {
      showToast('Ошибка: ' + e.message);
    }
  }
}

function renderAdminPage(container, reviewers, stats) {
  const verified   = reviewers.filter(r => r.verified);
  const unverified = reviewers.filter(r => !r.verified);

  container.innerHTML = `
    <div class="section-label">Статистика</div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card-label">В очереди</div>
        <div class="stat-card-value accent">${stats.in_queue}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Проверено</div>
        <div class="stat-card-value green">${stats.done}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Отклонено</div>
        <div class="stat-card-value red">${stats.rejected}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">Всего</div>
        <div class="stat-card-value">${stats.total}</div>
      </div>
    </div>

    ${unverified.length ? `
    <div class="section-label" style="color:var(--amber-text);">Ожидают верификации (${unverified.length})</div>
    <div id="unverified-list">
      ${unverified.map(r => reviewerRow(r)).join('')}
    </div>` : ''}

    <div class="section-label">Жюри (${verified.length})</div>
    <div id="verified-list">
      ${verified.map(r => reviewerRow(r)).join('')}
    </div>

    <div class="section-label">Управление</div>
    <div class="action-grid">
      <button class="action-btn" id="btn-export">📤 Экспорт Excel</button>
      <button class="action-btn" id="btn-days">📅 Дни</button>
      <button class="action-btn" id="btn-posts">📋 Посты</button>
      <button class="action-btn" id="btn-logs">📄 Логи</button>
    </div>
    <div id="admin-sub-content" style="margin-top:12px;"></div>
  `;

  // Клики по жюри
  container.querySelectorAll('.reviewer-row').forEach(row => {
    row.addEventListener('click', () => {
      const tgid    = row.dataset.tgid;
      const name    = row.dataset.name;
      const verified = row.dataset.verified === 'true';
      const isAdmin  = row.dataset.admin === 'true';
      showReviewerActions(tgid, name, verified, isAdmin, container, reviewers, stats);
    });
  });

  document.getElementById('btn-export').addEventListener('click', doExport);
  document.getElementById('btn-days').addEventListener('click',   () => loadDays(container));
  document.getElementById('btn-posts').addEventListener('click',  () => loadPosts(container));
  document.getElementById('btn-logs').addEventListener('click',   () => loadLogs(container));
}

function reviewerRow(r) {
  const badges = [];
  if (r.is_admin)  badges.push('<span class="badge badge-admin">админ</span>');
  if (r.verified)  badges.push('<span class="badge badge-ok">верифицирован</span>');
  else             badges.push('<span class="badge badge-wait">ожидает</span>');
  return `
    <div class="reviewer-row"
      data-tgid="${esc(r.tgid)}"
      data-name="${esc(r.name)}"
      data-verified="${r.verified}"
      data-admin="${r.is_admin}">
      <div>
        <div class="rv-name">${esc(r.name)}</div>
        <div class="rv-meta">проверено: ${r.checked}</div>
      </div>
      <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;">${badges.join('')}</div>
    </div>`;
}

function showReviewerActions(tgid, name, isVerified, isAdmin, container, reviewers, stats) {
  const sub = document.getElementById('admin-sub-content');
  sub.innerHTML = `
    <div class="post-row">
      <div style="font-size:14px;font-weight:700;margin-bottom:10px;">${esc(name)}</div>
      <div style="display:flex;flex-direction:column;gap:8px;">
        ${isVerified
          ? `<button class="action-btn" data-action="unverify">❌ Снять верификацию</button>`
          : `<button class="action-btn" data-action="verify">✅ Верифицировать</button>`}
        ${isAdmin
          ? `<button class="action-btn" data-action="remove-admin">👤 Снять права админа</button>`
          : `<button class="action-btn" data-action="make-admin">👑 Назначить админом</button>`}
        <button class="action-btn" data-action="delete" style="color:var(--red);">🗑 Удалить</button>
      </div>
    </div>`;

  sub.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;
      try {
        if (action === 'verify')        await api.verifyReviewer(tgid);
        if (action === 'unverify')      await api.unverifyReviewer(tgid);
        if (action === 'make-admin')    await api.makeAdmin(tgid);
        if (action === 'remove-admin')  await api.removeAdmin(tgid);
        if (action === 'delete') {
          if (!confirm(`Удалить жюри ${name}?`)) return;
          await api.deleteReviewer(tgid);
        }
        showToast('Готово ✅');
        loadAdmin(container);
      } catch (e) {
        showToast('Ошибка: ' + e.message);
      }
    });
  });
}

async function doExport() {
  showToast('⏳ Формирую файл...');
  try {
    await api.exportExcel();
    showToast('✅ Файл отправлен в личку!');
  } catch (e) {
    showToast('Ошибка: ' + e.message);
  }
}

async function loadDays(container) {
  const sub = document.getElementById('admin-sub-content');
  sub.innerHTML = '<div class="loader"><div class="spinner"></div> загрузка...</div>';
  try {
    const days = await api.getDays();
    sub.innerHTML = `
      <div class="section-label">Дни конкурса</div>
      ${days.map(d => `
        <div class="reviewer-row">
          <div><div class="rv-name">День ${d.day}</div><div class="rv-meta">${esc(d.data)}</div></div>
        </div>`).join('') || '<div class="rv-meta" style="padding:8px 0;">Дней нет</div>'}
      <button class="btn-primary" id="btn-new-day" style="margin-top:12px;width:100%;">+ Создать новый день</button>
    `;
    document.getElementById('btn-new-day').addEventListener('click', async () => {
      await api.createDay();
      showToast('✅ День создан');
      loadDays(container);
    });
  } catch (e) {
    sub.innerHTML = `<div class="rv-meta">Ошибка: ${esc(e.message)}</div>`;
  }
}

async function loadPosts(container) {
  const sub = document.getElementById('admin-sub-content');
  sub.innerHTML = `
    <div class="section-label">Посты</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">
      <button class="action-btn filter-btn active-filter" data-status="">Все</button>
      <button class="action-btn filter-btn" data-status="pending">Ожидают</button>
      <button class="action-btn filter-btn" data-status="done">Проверены</button>
      <button class="action-btn filter-btn" data-status="rejected">Отклонены</button>
    </div>
    <div id="posts-list"><div class="loader"><div class="spinner"></div></div></div>
  `;

  const loadPostsList = async (status) => {
    const list = document.getElementById('posts-list');
    list.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
    try {
      const posts = await api.getPosts(status || null);
      if (!posts.length) {
        list.innerHTML = '<div class="rv-meta" style="padding:8px 0;">Нет постов</div>';
        return;
      }
      list.innerHTML = posts.map(p => `
        <div class="post-row">
          <div class="post-row-top">
            <span class="post-row-author">${esc(p.author)}</span>
            <span class="post-row-meta">
              <span class="status-dot dot-${p.status}"></span>${statusLabel(p.status)}
            </span>
          </div>
          <div class="post-row-meta">
            ${p.human_words ? `${p.human_words} сл. · ${p.errors_per_1000} ош/1000` : ''}
            ${p.reject_reason ? `· причина: ${esc(p.reject_reason)}` : ''}
          </div>
        </div>`).join('');
    } catch (e) {
      list.innerHTML = `<div class="rv-meta">Ошибка: ${esc(e.message)}</div>`;
    }
  };

  sub.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      sub.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active-filter'));
      btn.classList.add('active-filter');
      loadPostsList(btn.dataset.status);
    });
  });

  loadPostsList('');
}

async function loadLogs(container) {
  const sub = document.getElementById('admin-sub-content');
  sub.innerHTML = '<div class="loader"><div class="spinner"></div> загружаю логи...</div>';
  try {
    const data = await api.getLogs(100);
    sub.innerHTML = `
      <div class="section-label">Последние 100 строк</div>
      <div class="logs-block">${esc(data.lines)}</div>`;
    sub.querySelector('.logs-block').scrollTop = 9999;
  } catch (e) {
    sub.innerHTML = `<div class="rv-meta">Ошибка: ${esc(e.message)}</div>`;
  }
}

function statusLabel(s) {
  return {pending:'ожидает',checking:'проверяется',done:'проверен',rejected:'отклонён'}[s] || s;
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
