/**
 * app.js — точка входа Mini App
 */

import { renderReview, onReviewActivate } from './pages/review.js';
import { renderStats }  from './pages/stats.js';
import { renderAdmin }  from './pages/admin.js';

const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

// ── Тема ──────────────────────────────────────────────────────────────────
let isDark = false;

function applyTheme(dark) {
  isDark = dark;
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const btn = document.getElementById('btn-theme');
  if (btn) btn.textContent = dark ? '☀️' : '🌙';
  localStorage.setItem('theme', dark ? 'dark' : 'light');
}

function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved) { applyTheme(saved === 'dark'); return; }
  if (tg?.colorScheme) { applyTheme(tg.colorScheme === 'dark'); return; }
  applyTheme(window.matchMedia('(prefers-color-scheme: dark)').matches);
}

// ── Toast ─────────────────────────────────────────────────────────────────
let toastTimer = null;
export function showToast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  let text = msg;
  if (msg instanceof Error) text = msg.message;
  else if (typeof msg === 'object') text = JSON.stringify(msg);
  el.textContent = String(text);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

// ── Float bar ─────────────────────────────────────────────────────────────
export function setFloatBar(visible) {
  const bar = document.getElementById('float-bar');
  if (bar) bar.classList.toggle('hidden', !visible);
}

// ── Drawer ────────────────────────────────────────────────────────────────
export function showDrawer(type) {
  window._quickReason = null;

  const rejectBody = `
    <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px;">
      <button class="quick-reason" data-reason="few_words"
        style="padding:9px;border-radius:10px;border:0.5px solid var(--border);
               background:var(--input-bg);color:var(--text);font-size:13px;
               font-weight:600;cursor:pointer;text-align:left;">
        📏 Мало слов
      </button>
      <button class="quick-reason" data-reason="ai_used"
        style="padding:9px;border-radius:10px;border:0.5px solid var(--border);
               background:var(--input-bg);color:var(--text);font-size:13px;
               font-weight:600;cursor:pointer;text-align:left;">
        🤖 Использовал ИИ
      </button>
    </div>
    <label>Другое</label>
    <textarea rows="3" id="d-reason" placeholder="Своя причина..."></textarea>
  `;

  const configs = {
    reject: {
      title: 'Отклонение', sub: 'Выбери причину',
      btnClass: 'reject', btnText: 'Отклонить',
      body: rejectBody,
    },
    skip: {
      title: 'Пропуск', sub: 'Укажи причину пропуска',
      btnClass: 'skip', btnText: 'Пропустить',
      body: `<label>Причина</label><textarea rows="4" id="d-reason" placeholder="Почему пропускаешь?"></textarea>`,
    },
    accept: {
      title: 'Принятие', sub: 'Введи результат проверки',
      btnClass: 'accept', btnText: 'Сохранить',
      body: `
        <label>Количество слов</label>
        <input type="number" id="d-words" min="0" max="100000" placeholder="например 842">
        <label>Количество ошибок</label>
        <input type="number" id="d-errors" min="0" max="10000" placeholder="например 5">
      `,
    },
  };

  const cfg = configs[type];
  if (!cfg) return;

  document.getElementById('drawer-title').textContent = cfg.title;
  document.getElementById('drawer-sub').textContent   = cfg.sub;
  document.getElementById('drawer-body').innerHTML    = cfg.body;

  const btn = document.getElementById('drawer-confirm');
  btn.className   = 'drawer-confirm ' + cfg.btnClass;
  btn.textContent = cfg.btnText;
  btn.onclick     = () => onDrawerConfirm(type);

  document.getElementById('drawer').classList.add('open');
  document.getElementById('drawer-overlay').classList.add('open');

  setTimeout(() => {
    const first = document.querySelector('#drawer-body textarea, #drawer-body input');
    if (first) first.focus();
  }, 280);
}

export function hideDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-overlay').classList.remove('open');
  window._quickReason = null;
}

function onDrawerConfirm(type) {
  if (!window._onDrawerConfirm) return;
  let data = {};

  if (type === 'accept') {
    const words  = parseInt(document.getElementById('d-words')?.value  || '0');
    const errors = parseInt(document.getElementById('d-errors')?.value || '0');
    if (isNaN(words)  || words  < 0) { showToast('Введи количество слов');   return; }
    if (isNaN(errors) || errors < 0) { showToast('Введи количество ошибок'); return; }
    data = { words, errors };
  } else if (type === 'reject') {
    if (window._quickReason) {
      data = { reason: window._quickReason };
    } else {
      const custom = document.getElementById('d-reason')?.value?.trim();
      if (!custom) { showToast('Укажи причину'); return; }
      data = { reason: `other: ${custom}` };
    }
    window._quickReason = null;
  } else {
    const reason = document.getElementById('d-reason')?.value?.trim();
    if (!reason) { showToast('Укажи причину'); return; }
    data = { reason };
  }

  window._onDrawerConfirm(type, data);
}

// Быстрые кнопки причины отклонения
document.addEventListener('click', e => {
  const btn = e.target.closest('.quick-reason');
  if (!btn) return;
  window._quickReason = btn.dataset.reason;
  document.querySelectorAll('.quick-reason').forEach(b => {
    const active = b === btn;
    b.style.background   = active ? 'var(--accent-light)' : 'var(--input-bg)';
    b.style.borderColor  = active ? 'var(--accent)'       : 'var(--border)';
    b.style.color        = active ? 'var(--accent)'       : 'var(--text)';
  });
  const textarea = document.getElementById('d-reason');
  if (textarea) textarea.value = '';
});

// ── Навигация ─────────────────────────────────────────────────────────────
const PAGES = ['review', 'stats', 'admin'];

function navigateTo(page) {
  PAGES.forEach(p => {
    document.getElementById(`page-${p}`).classList.toggle('active', p === page);
    document.getElementById(`nav-${p}`).classList.toggle('active', p === page);
  });
  hideDrawer();
  setFloatBar(false);

  const container = document.getElementById(`page-${page}`);
  if (page === 'review') { renderReview(container); onReviewActivate(); }
  if (page === 'stats')  { renderStats(container);  }
  if (page === 'admin')  { renderAdmin(container);  }
}

// ── Инициализация ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTheme();

  document.getElementById('btn-theme').addEventListener('click', () => applyTheme(!isDark));

  PAGES.forEach(p => {
    document.getElementById(`nav-${p}`).addEventListener('click', () => navigateTo(p));
  });

  document.getElementById('fab-reject').addEventListener('click', () => showDrawer('reject'));
  document.getElementById('fab-skip').addEventListener('click',   () => showDrawer('skip'));
  document.getElementById('fab-accept').addEventListener('click', () => showDrawer('accept'));

  document.getElementById('drawer-overlay').addEventListener('click', hideDrawer);
  document.getElementById('drawer-close').addEventListener('click',   hideDrawer);

  // Проверяем initData — Telegram иногда передаёт его с задержкой
  // Делаем несколько попыток прежде чем показать баннер
  let bannerShown = false;
  function checkInitData(attempt) {
    const tgApp = window.Telegram?.WebApp;
    if (tgApp?.initData && tgApp.initData.length > 0) {
      // initData есть — убираем баннер если он был показан
      if (bannerShown) {
        const b = document.getElementById('tg-banner');
        if (b) b.remove();
        bannerShown = false;
      }
      return;
    }
    if (attempt >= 5) {
      // 5 попыток не помогли — показываем баннер
      if (!bannerShown) {
        const banner = document.createElement('div');
        banner.id = 'tg-banner';
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#E24B4A;color:#fff;text-align:center;font-size:12px;padding:4px;z-index:999;';
        banner.textContent = 'Откройте через Telegram для полной работы';
        document.body.appendChild(banner);
        bannerShown = true;
      }
      return;
    }
    setTimeout(() => checkInitData(attempt + 1), 600);
  }
  checkInitData(0);

  navigateTo('review');
});

