/**
 * pages/review.js — страница проверки поста
 */

import { api } from '../api.js';
import { showToast, hideDrawer, setFloatBar } from '../app.js';

let currentPost = null;

export function renderReview(container) {
  container.innerHTML = `
    <div id="review-empty" class="empty-state">
      <div class="empty-icon">📭</div>
      <div class="empty-title">Нет активного поста</div>
      <div class="empty-sub">Нажми кнопку чтобы взять следующий</div>
      <button class="btn-primary" id="btn-get-post">Взять пост</button>
    </div>
    <div id="review-post" style="display:none;">
      <div class="post-header" id="post-header"></div>
      <div class="post-text-block" id="post-text"></div>
      <div id="ai-result-block"></div>
    </div>
  `;
  document.getElementById('btn-get-post').addEventListener('click', loadNextPost);
  setFloatBar(false);
}

export async function onReviewActivate() {
  try {
    const post = await api.getNextPost();
    setPost(post);
  } catch (e) {
    if (e.message !== 'No posts available' && e.message !== 'No active post') {
      showToast('Ошибка: ' + e.message);
    }
    clearPost();
  }
}

async function loadNextPost() {
  const btn = document.getElementById('btn-get-post');
  if (btn) { btn.disabled = true; btn.textContent = 'Загрузка...'; }
  try {
    const post = await api.getNextPost();
    setPost(post);
  } catch (e) {
    showToast(e.message === 'No posts available' ? 'Все посты проверены ✅' : 'Ошибка: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Взять пост'; }
  }
}

function setPost(post) {
  currentPost = post;
  document.getElementById('review-empty').style.display = 'none';
  document.getElementById('review-post').style.display  = 'block';
  document.getElementById('ai-result-block').innerHTML  = '';

  document.getElementById('post-header').innerHTML = `
    <span class="chip chip-author">${esc(post.author)}</span>
    <span class="chip chip-words">${post.bot_words ?? '—'} сл.</span>
    <a class="chip chip-link" href="${esc(post.url)}" target="_blank">открыть ↗</a>
    <button id="btn-ai" style="margin-left:auto;background:none;border:none;color:var(--accent);font-size:12px;font-weight:600;cursor:pointer;padding:2px 6px;">🤖 ИИ</button>
  `;
  document.getElementById('btn-ai').addEventListener('click', runAiCheck);

  const textEl = document.getElementById('post-text');
  textEl.innerHTML = '<div class="loader"><div class="spinner"></div> загружаю текст...</div>';
  api.getPostText().then(data => {
    textEl.textContent = data.text || '(текст не найден)';
  }).catch(() => {
    textEl.textContent = '(не удалось загрузить текст)';
  });

  setFloatBar(true);
  bindDrawerConfirm();
}

async function runAiCheck() {
  const btn   = document.getElementById('btn-ai');
  const block = document.getElementById('ai-result-block');
  if (!btn) return;
  btn.textContent = '⏳';
  btn.disabled = true;
  block.innerHTML = '<div class="loader"><div class="spinner"></div> ИИ проверяет...</div>';
  try {
    const data = await api.aiCheck();
    block.innerHTML = data.results.map(r => `<div class="ai-result">${esc(r)}</div>`).join('');
  } catch (e) {
    block.innerHTML = `<div class="ai-result">Ошибка: ${esc(e.message)}</div>`;
  } finally {
    btn.textContent = '🤖 ИИ';
    btn.disabled = false;
  }
}

function bindDrawerConfirm() {
  window._onDrawerConfirm = async (type, data) => {
    if (!currentPost) return;
    try {
      if (type === 'accept') {
        await api.submitResult(data.words, data.errors);
        showToast('✅ Сохранено!');
      } else if (type === 'reject') {
        await api.rejectPost(data.reason);
        showToast('❌ Отклонено');
      } else if (type === 'skip') {
        await api.skipPost(data.reason);
        showToast('⏭ Пропущено');
      }
      clearPost();
      hideDrawer();
    } catch (e) {
      showToast('Ошибка: ' + e.message);
    }
  };
}

function clearPost() {
  currentPost = null;
  setFloatBar(false);
  const empty = document.getElementById('review-empty');
  const post  = document.getElementById('review-post');
  if (empty) empty.style.display = '';
  if (post)  post.style.display  = 'none';
  const btn = document.getElementById('btn-get-post');
  if (btn)   { btn.disabled = false; btn.textContent = 'Взять пост'; }
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
