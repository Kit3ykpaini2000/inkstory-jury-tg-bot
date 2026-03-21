/**
 * pages/stats.js — страница статистики
 */

import { api } from '../api.js';
import { showToast, setFloatBar } from '../app.js';

export function renderStats(container) {
  container.innerHTML = '<div class="loader"><div class="spinner"></div> загрузка...</div>';
  setFloatBar(false);
  loadStats(container);
}

async function loadStats(container) {
  try {
    const [me, full] = await Promise.all([api.getMe(), api.getFullStats()]);

    const ep1k = me.total_words
      ? (me.total_errors / me.total_words * 1000).toFixed(1)
      : '0';

    container.innerHTML = `
      <div class="section-label">Моя статистика</div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-card-label">Проверено</div>
          <div class="stat-card-value green">${me.checked}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Отклонено</div>
          <div class="stat-card-value red">${me.rejected}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Всего слов</div>
          <div class="stat-card-value">${fmtNum(me.total_words)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Ош / 1000</div>
          <div class="stat-card-value accent">${ep1k}</div>
        </div>
      </div>

      <div class="section-label">Общая статистика</div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-card-label">В очереди</div>
          <div class="stat-card-value accent">${full.in_queue}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Проверено</div>
          <div class="stat-card-value green">${full.done}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Отклонено</div>
          <div class="stat-card-value red">${full.rejected}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-label">Всего постов</div>
          <div class="stat-card-value">${full.total}</div>
        </div>
      </div>
    `;
  } catch (e) {
    showToast('Ошибка загрузки: ' + e.message);
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-title">Ошибка</div><div class="empty-sub">${e.message}</div></div>`;
  }
}

function fmtNum(n) {
  if (!n) return '0';
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}
