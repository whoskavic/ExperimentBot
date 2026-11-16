// ── Nav ──
function goTo(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Date helpers ──
function todayStr() {
  return new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD
}

// ── Mode ──
let currentMode = 'today';

function setMode(mode) {
  currentMode = mode;
  document.getElementById('btn-mode-today').classList.toggle('active', mode === 'today');
  document.getElementById('btn-mode-range').classList.toggle('active', mode === 'range');

  const fromInput = document.getElementById('input-from');
  const toInput   = document.getElementById('input-to');

  if (mode === 'today') {
    const t = todayStr();
    fromInput.value = t;
    toInput.value   = t;
    fromInput.disabled = true;
    toInput.disabled   = true;
  } else {
    fromInput.disabled = false;
    toInput.disabled   = false;
  }
}

// ── Auth ──
// Token CSRF diambil dari /api/me dan dikirim di header X-CSRFToken
// pada setiap POST. Respons 401 → kembali ke halaman login.
let csrfToken = null;

function goToLogin() {
  const next = window.location.pathname + window.location.search;
  window.location.href = '/login' + (next && next !== '/' ? '?next=' + encodeURIComponent(next) : '');
}

async function loadMe() {
  try {
    const res = await fetch('/api/me', { credentials: 'same-origin' });
    if (res.status === 401) return goToLogin();
    const me = await res.json();
    csrfToken = me.csrf_token;
    document.getElementById('current-user').textContent = me.username;
  } catch (err) {
    addLog('Tidak dapat memuat sesi login.', 'err');
  }
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken || '' },
    body: JSON.stringify(body || {}),
  });
  if (res.status === 401) {
    goToLogin();
    throw new Error('unauthorized');
  }
  return res;
}

async function logout() {
  try {
    await fetch('/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken || '' },
    });
  } finally {
    window.location.href = '/login';
  }
}

// Init on load
window.addEventListener('DOMContentLoaded', () => {
  setMode('today');
  loadMe();
});

// ── Log ──
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// status simpan DB → tipe log
const SAVE_LOG_TYPE = { created: 'ok', updated: 'ok', unchanged: 'inf', skipped: 'inf', error: 'err' };

function logSaveReport(saved) {
  if (!saved || !saved.length) return;
  addLog('Hasil simpan ke database:', 'inf');
  saved.forEach(src => {
    (src.dates || []).forEach(d => {
      let extra = '';
      if (d.status === 'updated')   extra = ` (revisi #${d.revision_no})`;
      if (d.names_updated)          extra += ` (${d.names_updated} nama diperbarui)`;
      if (d.status === 'error')     extra = ` — ${d.error}`;
      addLog(`  #${src.source} ${d.recap_date}: ${d.status}${extra}`, SAVE_LOG_TYPE[d.status] || 'inf');
    });
  });
}

function addLog(msg, type = 'inf') {
  const area = document.getElementById('log-area');
  const now  = new Date().toLocaleTimeString('id-ID', { hour12: false });
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="log-time">${now}</span><span class="log-msg ${type}">${escapeHtml(msg)}</span>`;
  area.appendChild(line);
  area.scrollTop = area.scrollHeight;
}

// ── Recap ──
async function triggerRecap() {
  const btn      = document.getElementById('btn-recap');
  const dateFrom = document.getElementById('input-from').value;
  const dateTo   = document.getElementById('input-to').value;

  if (!dateFrom || !dateTo) {
    addLog('Tanggal belum dipilih.', 'err');
    return;
  }
  if (dateFrom > dateTo) {
    addLog('Date From tidak boleh lebih besar dari Date To.', 'err');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> RUNNING...';

  const label = dateFrom === dateTo ? dateFrom : `${dateFrom} s/d ${dateTo}`;
  addLog(`Menjalankan recap: ${label}`, 'run');

  try {
    const res  = await postJson('/recap', { date_from: dateFrom, date_to: dateTo });
    const data = await res.json();

    if (res.ok && data.status === 'ok') {
      addLog('Recap berhasil dijalankan.', 'ok');
      (data.files || []).forEach(f => addLog(`  ✓ ${f}`, 'ok'));
      addLog('File dikirim ke destination channel Discord.', 'ok');
      logSaveReport(data.saved);
      // Rekap bisa menambah source baru → segarkan dropdown Riwayat (tidak memblokir).
      refreshSourceOptions().catch(err => {
        if (err.message !== 'unauthorized') addLog(`Gagal memperbarui daftar source: ${err.message}`, 'err');
      });
    } else {
      addLog(`Gagal: ${data.message || 'Unknown error'}`, 'err');
    }
  } catch (err) {
    if (err.message !== 'unauthorized') addLog('Tidak dapat terhubung ke server bot.', 'err');
  }

  btn.disabled = false;
  btn.innerHTML = '▶ RECAP NOW';
}

// ════════════════════════════════════════════════════════
// Riwayat rekap (data dari database)
// Semua teks dari server dimasukkan lewat textContent (aman dari XSS).
// ════════════════════════════════════════════════════════

const FLAG_LABELS = [
  ['opening', 'Opening'], ['opening_late', 'Opening telat'],
  ['closing', 'Closing'], ['closing_late', 'Closing telat'],
];
const DIFF_LABELS = { same: 'sama', changed: 'berubah', added: 'baru', removed: 'dihapus' };

// datesInitialized: tanggal default (30 hari terakhir) hanya diisi sekali,
// supaya filter yang sudah diubah user tidak ter-reset saat halaman dibuka lagi.
const historyState = { page: 1, pages: 1, datesInitialized: false };

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === 'class') node.className = v;
    else if (k === 'onclick') node.addEventListener('click', v);
    else node.setAttribute(k, v);
  });
  children.flat().forEach(c => {
    if (c === null || c === undefined) return;
    node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  });
  return node;
}

async function getJson(url) {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (res.status === 401) {
    goToLogin();
    throw new Error('unauthorized');
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

function historyMessage(text, isError) {
  const m = document.getElementById('h-message');
  m.textContent = text || '';
  m.classList.toggle('msg-error', !!isError);
}

function historyParams() {
  const p = new URLSearchParams();
  const add = (k, id) => { const v = document.getElementById(id).value; if (v) p.set(k, v); };
  add('date_from', 'h-from');
  add('date_to', 'h-to');
  add('source_id', 'h-source');
  add('team', 'h-team');
  return p;
}

// Isi ulang <select> dari nol: option pertama ("Semua ...") + daftar baru.
// Pilihan sebelumnya dipertahankan kalau masih ada di daftar baru.
function fillSelect(select, firstLabel, options) {
  const previous = select.value;
  select.replaceChildren(
    el('option', { value: '' }, firstLabel),
    ...options.map(([value, label]) => el('option', { value: String(value) }, label)),
  );
  select.value = Array.from(select.options).some(o => o.value === previous) ? previous : '';
}

// Muat ulang daftar source + tim dari /api/sources.
// Dipanggil setiap halaman Riwayat dibuka dan setelah RECAP NOW selesai
// (rekap bisa menambah source baru, mis. thread yang baru pertama kali direkap).
async function refreshSourceOptions() {
  const data = await getJson('/api/sources');
  fillSelect(document.getElementById('h-source'), 'Semua source',
    data.sources.map(s => [s.id, s.kind === 'thread' ? `${s.name} (thread)` : `#${s.name}`]));
  fillSelect(document.getElementById('h-team'), 'Semua tim',
    [...data.teams.map(t => [t, t]), ['__none__', '(tanpa tim)']]);
}

async function openHistory() {
  goTo('page-history');
  if (!historyState.datesInitialized) {
    const to = todayStr();
    const from = new Date(Date.now() - 29 * 86400000).toLocaleDateString('sv-SE');
    document.getElementById('h-from').value = from;
    document.getElementById('h-to').value = to;
    historyState.datesInitialized = true;
  }
  try {
    await refreshSourceOptions();
  } catch (err) {
    if (err.message === 'unauthorized') return;
    historyMessage(`Gagal memuat daftar source: ${err.message}`, true);
  }
  loadHistory(1);
}

async function loadHistory(page) {
  if (page < 1) return;
  const p = historyParams();
  p.set('page', page);
  historyMessage('Memuat...');
  try {
    const data = await getJson('/api/recaps?' + p.toString());
    historyState.page = data.page;
    historyState.pages = data.pages;
    renderHistory(data);
    historyMessage('');
  } catch (err) {
    if (err.message !== 'unauthorized') historyMessage(err.message, true);
  }
}

function renderHistory(data) {
  const tbody = document.querySelector('#h-table tbody');
  tbody.replaceChildren();
  if (!data.items.length) {
    tbody.appendChild(el('tr', {}, el('td', { colspan: 8, class: 'empty' }, 'Tidak ada rekap untuk filter ini.')));
  }
  data.items.forEach(r => {
    tbody.appendChild(el('tr', {},
      el('td', {}, r.recap_date),
      el('td', {}, r.source.name),
      el('td', {}, r.source.team || '–'),
      el('td', { class: 'num' }, r.entry_count),
      el('td', { class: 'num' }, r.revision_count),
      el('td', {}, r.last_triggered_by),
      el('td', { class: 'mono' }, r.updated_at),
      el('td', {}, el('button', { class: 'link-btn', onclick: () => openDetail(r.id) }, 'DETAIL')),
    ));
  });
  const f = data.filter;
  const range = f.date_from === f.date_to ? f.date_from : `${f.date_from} s/d ${f.date_to}`;
  document.getElementById('h-summary').textContent = `Hasil — ${data.total} rekap (${range})`;
  document.getElementById('h-page').textContent = `Halaman ${data.page} / ${data.pages}`;
  document.getElementById('h-prev').disabled = data.page <= 1;
  document.getElementById('h-next').disabled = data.page >= data.pages;
}

function exportHistory() {
  // GET biasa dengan cookie session; file dibuat di server dari database.
  window.location.href = '/api/recaps/export?' + historyParams().toString();
}

function flagCells(flags) {
  return FLAG_LABELS.map(([k]) => el('td', { class: 'num' }, flags ? (flags[k] ? '✓' : '·') : '–'));
}

function entriesTable(entries) {
  if (!entries.length) return el('p', { class: 'action-desc' }, 'Tidak ada entri (tanggal kosong).');
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
    el('thead', {}, el('tr', {}, el('th', {}, 'User'), FLAG_LABELS.map(([, l]) => el('th', {}, l)))),
    el('tbody', {}, entries.map(e => el('tr', {}, el('td', {}, e.display_name), flagCells(e))))));
}

function diffTable(rows) {
  if (!rows.length) return el('p', { class: 'action-desc' }, 'Keduanya kosong.');
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table diff' },
    el('thead', {},
      el('tr', {}, el('th', { rowspan: 2 }, 'User'), el('th', { rowspan: 2 }, 'Status'),
        el('th', { colspan: 4 }, 'Versi lama'), el('th', { colspan: 4 }, 'Sekarang')),
      el('tr', {}, FLAG_LABELS.map(([, l]) => el('th', {}, l)), FLAG_LABELS.map(([, l]) => el('th', {}, l)))),
    el('tbody', {}, rows.map(r => el('tr', { class: 'diff-' + r.status },
      el('td', {}, r.display_name), el('td', {}, DIFF_LABELS[r.status] || r.status),
      flagCells(r.old), flagCells(r.new))))));
}

async function openDetail(id) {
  const box = document.getElementById('h-detail');
  const body = document.getElementById('h-detail-body');
  box.hidden = false;
  body.replaceChildren(el('p', { class: 'action-desc' }, 'Memuat...'));
  try {
    const d = await getJson('/api/recaps/' + id);
    document.getElementById('h-detail-title').textContent = `Detail — ${d.recap_date} · ${d.source.name}`;
    const parts = [
      el('dl', { class: 'meta' },
        el('dt', {}, 'Tim'), el('dd', {}, d.source.team || '–'),
        el('dt', {}, 'Revisi'), el('dd', {}, d.revision_count),
        el('dt', {}, 'Terakhir oleh'), el('dd', {}, d.last_triggered_by),
        el('dt', {}, 'Dibuat'), el('dd', {}, d.created_at),
        el('dt', {}, 'Diperbarui'), el('dd', {}, d.updated_at)),
      el('div', { class: 'action-title' }, `Data sekarang (${d.entry_count} entri)`),
      entriesTable(d.entries),
    ];
    if (!d.revisions.length) {
      parts.push(el('p', { class: 'action-desc' }, 'Belum ada revisi — data ini belum pernah berubah.'));
    }
    d.revisions.forEach(v => {
      parts.push(el('details', { class: 'revision' },
        el('summary', {}, `Revisi #${v.revision_no} — diganti ${v.replaced_at} oleh ${v.replaced_by}`),
        el('p', { class: 'action-desc' },
          `Versi lama: terakhir oleh ${v.old_triggered_by || '–'}, diperbarui ${v.old_updated_at || '–'}. ` +
          'Dibandingkan dengan data sekarang:'),
        diffTable(v.diff_vs_current)));
    });
    body.replaceChildren(...parts);
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    if (err.message !== 'unauthorized') body.replaceChildren(el('p', { class: 'action-desc msg-error' }, err.message));
  }
}

function closeDetail() {
  document.getElementById('h-detail').hidden = true;
}
