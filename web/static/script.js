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
    } else {
      addLog(`Gagal: ${data.message || 'Unknown error'}`, 'err');
    }
  } catch (err) {
    if (err.message !== 'unauthorized') addLog('Tidak dapat terhubung ke server bot.', 'err');
  }

  btn.disabled = false;
  btn.innerHTML = '▶ RECAP NOW';
}