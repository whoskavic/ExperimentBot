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

// Init on load
window.addEventListener('DOMContentLoaded', () => setMode('today'));

// ── Log ──
function addLog(msg, type = 'inf') {
  const area = document.getElementById('log-area');
  const now  = new Date().toLocaleTimeString('id-ID', { hour12: false });
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="log-time">${now}</span><span class="log-msg ${type}">${msg}</span>`;
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
    const res  = await fetch('/recap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date_from: dateFrom, date_to: dateTo })
    });
    const data = await res.json();

    if (res.ok && data.status === 'ok') {
      addLog('Recap berhasil dijalankan.', 'ok');
      (data.files || []).forEach(f => addLog(`  ✓ ${f}`, 'ok'));
      addLog('File dikirim ke destination channel Discord.', 'ok');
    } else {
      addLog(`Gagal: ${data.message || 'Unknown error'}`, 'err');
    }
  } catch (err) {
    addLog('Tidak dapat terhubung ke server bot.', 'err');
  }

  btn.disabled = false;
  btn.innerHTML = '▶ RECAP NOW';
}