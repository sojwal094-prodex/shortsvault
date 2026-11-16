/* ViralClipper — Frontend JS */

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

let settings = {
  brand_kit: {
    channel_name:     '',
    font:             'Arial',
    caption_color:    '#ffffff',
    highlight_color:  '#ffff00',
    watermark_color:  '#ffffff',
  }
};
let activePolls = {};  // job_id → intervalId
let batchMode   = false;

// ─────────────────────────────────────────────────────────────────────────────
// Process video
// ─────────────────────────────────────────────────────────────────────────────

async function processVideo() {
  const goBtn = document.getElementById('goBtn');

  // Collect URLs
  let urls = [];
  if (batchMode) {
    const raw = document.getElementById('batchUrls').value.trim();
    urls = raw.split('\n').map(u => u.trim()).filter(Boolean);
  } else {
    const u = document.getElementById('urlInput').value.trim();
    if (u) urls = [u];
  }

  if (!urls.length) {
    shakeInput();
    return;
  }

  // Disable button
  goBtn.disabled = true;
  goBtn.innerHTML = '<span class="spinner"></span><span>Processing…</span>';

  try {
    const res  = await fetch('/api/process', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({ urls, brand_kit: settings.brand_kit }),
    });
    const data = await res.json();

    if (data.error) throw new Error(data.error);

    // Create job cards and start polling
    const ids = data.job_ids || [data.job_id];
    ids.forEach(createJobCard);

    // Scroll to jobs
    document.getElementById('jobsSection').scrollIntoView({ behavior: 'smooth' });

    // Clear input
    document.getElementById('urlInput').value = '';
    document.getElementById('batchUrls').value = '';

  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    goBtn.disabled = false;
    goBtn.innerHTML = '<span>Generate Clips</span><span class="btn-arrow">→</span>';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Job card creation
// ─────────────────────────────────────────────────────────────────────────────

function createJobCard(jobId) {
  const tpl  = document.getElementById('jobTemplate');
  const card = tpl.content.cloneNode(true).querySelector('.job-card');
  card.id    = `job-${jobId}`;

  document.getElementById('jobsList').prepend(card);
  updateJobCard(jobId, { status: 'queued', progress: 0, message: 'Queued…', url: '' });

  // Poll every 1.5 seconds
  activePolls[jobId] = setInterval(() => pollJob(jobId), 1500);
}

async function pollJob(jobId) {
  try {
    const res  = await fetch(`/api/status/${jobId}`);
    const data = await res.json();
    updateJobCard(jobId, data);

    if (data.status === 'done' || data.status === 'error') {
      clearInterval(activePolls[jobId]);
      delete activePolls[jobId];
    }
  } catch (e) {
    // Network blip — keep polling
  }
}

function updateJobCard(jobId, data) {
  const card = document.getElementById(`job-${jobId}`);
  if (!card) return;

  // Title
  const titleEl = card.querySelector('.job-title');
  const vi       = data.video_info;
  titleEl.textContent = vi ? vi.title : (data.url ? truncate(data.url, 60) : 'Processing video…');

  // URL
  card.querySelector('.job-url').textContent = data.url || '';

  // Status badge
  const badge = card.querySelector('.job-status-badge');
  badge.textContent = data.status || 'queued';
  badge.className   = `job-status-badge status-${data.status || 'queued'}`;

  // Progress bar
  const fill = card.querySelector('.job-progress-fill');
  const pct  = data.progress || 0;
  fill.style.width = pct + '%';

  // Message
  card.querySelector('.job-progress-msg').textContent = data.message || '';

  // Render clips when done
  if (data.status === 'done' && data.clips && data.clips.length) {
    renderClips(card, data.clips, jobId);
    card.querySelector('.job-actions').classList.remove('hidden');
    card.querySelector('.btn-dl-all').onclick = () => downloadAll(jobId);
  }

  if (data.status === 'error') {
    card.querySelector('.job-progress-msg').style.color = '#ef4444';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Clip rendering
// ─────────────────────────────────────────────────────────────────────────────

function renderClips(card, clips, jobId) {
  const grid = card.querySelector('.job-clips-grid');
  if (grid.dataset.rendered) return;
  grid.dataset.rendered = '1';
  grid.innerHTML = '';

  clips.forEach((clip, idx) => {
    const tpl     = document.getElementById('clipCardTemplate');
    const clipEl  = tpl.content.cloneNode(true).querySelector('.clip-card');

    clipEl.querySelector('.clip-num').textContent = `Clip ${clip.index || idx + 1}`;

    const score = clip.viral_score || 0;
    const scoreEl = clipEl.querySelector('.clip-score');
    scoreEl.textContent = score;
    // Color by score
    if (score >= 80) scoreEl.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
    else if (score >= 60) scoreEl.style.background = 'linear-gradient(135deg,#8b5cf6,#ec4899)';
    else scoreEl.style.background = 'linear-gradient(135deg,#9090a8,#606075)';
    scoreEl.style['-webkit-background-clip'] = 'text';
    scoreEl.style['-webkit-text-fill-color'] = 'transparent';
    scoreEl.style['background-clip'] = 'text';

    clipEl.querySelector('.clip-title').textContent   = clip.title    || `Clip ${idx+1}`;
    clipEl.querySelector('.clip-duration').textContent= formatDur(clip.duration || (clip.end - clip.start));
    clipEl.querySelector('.clip-type-badge').textContent = clip.type  || 'insight';
    clipEl.querySelector('.clip-hook').textContent    = clip.hook     || '';
    clipEl.querySelector('.clip-caption-preview').textContent = clip.caption || '';
    clipEl.querySelector('.clip-reason').textContent  = clip.reason   || '';

    // Download
    clipEl.querySelector('.btn-download').onclick = () => downloadClip(jobId, idx);

    // Copy caption
    clipEl.querySelector('.btn-copy-caption').onclick = () => {
      navigator.clipboard.writeText(clip.caption || '').then(() => {
        const btn = clipEl.querySelector('.btn-copy-caption');
        btn.textContent = '✓ Copied!';
        setTimeout(() => btn.textContent = '📋 Copy Caption', 2000);
      });
    };

    grid.appendChild(clipEl);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Downloads
// ─────────────────────────────────────────────────────────────────────────────

function downloadClip(jobId, clipIdx) {
  window.location.href = `/api/download/${jobId}/${clipIdx}`;
}

function downloadAll(jobId) {
  window.location.href = `/api/download-all/${jobId}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Batch mode toggle
// ─────────────────────────────────────────────────────────────────────────────

function toggleBatch() {
  batchMode = !batchMode;
  document.getElementById('batchArea').classList.toggle('hidden', !batchMode);
  document.getElementById('batchToggleIcon').textContent = batchMode ? '−' : '+';
  if (batchMode) {
    document.getElementById('urlInput').disabled    = true;
    document.getElementById('urlInput').placeholder = 'Using batch mode ↓';
  } else {
    document.getElementById('urlInput').disabled    = false;
    document.getElementById('urlInput').placeholder = 'Paste video URL here…';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings modal
// ─────────────────────────────────────────────────────────────────────────────

function openSettings() {
  document.getElementById('settingsModal').classList.add('open');
  loadSettingsIntoForm();
}

function closeSettings(e) {
  if (!e || e.target === document.getElementById('settingsModal')) {
    document.getElementById('settingsModal').classList.remove('open');
  }
}

function loadSettingsIntoForm() {
  const bk = settings.brand_kit || {};
  setVal('s_channel',         bk.channel_name    || '');
  setVal('s_font',            bk.font            || 'Arial');
  setVal('s_caption_color',   bk.caption_color   || '#ffffff');
  setVal('s_highlight_color', bk.highlight_color || '#ffff00');
  setVal('s_watermark_color', bk.watermark_color || '#ffffff');

  // Load from .env values (from API)
  fetch('/api/settings').then(r => r.json()).then(s => {
    setVal('s_whisper_mode',  s.whisper_mode  || 'local');
    setVal('s_whisper_model', s.whisper_model || 'base');
    setVal('s_max_clips',     s.max_clips     || 7);
    setVal('s_min_dur',       s.min_duration  || 15);
    setVal('s_max_dur',       s.max_duration  || 90);
  }).catch(() => {});
}

function saveSettings() {
  settings.brand_kit = {
    channel_name:    getVal('s_channel'),
    font:            getVal('s_font'),
    caption_color:   hexToAss(getVal('s_caption_color')),
    highlight_color: hexToAss(getVal('s_highlight_color')),
    watermark_color: getVal('s_watermark_color'),
    _caption_hex:    getVal('s_caption_color'),
    _highlight_hex:  getVal('s_highlight_color'),
  };

  localStorage.setItem('viralclipper_settings', JSON.stringify(settings));
  closeSettings();
  showToast('Settings saved ✓');
}

// Convert #rrggbb to ASS colour &H00BBGGRR
function hexToAss(hex) {
  if (!hex || hex[0] !== '#') return '&H00FFFFFF';
  const r = hex.slice(1,3);
  const g = hex.slice(3,5);
  const b = hex.slice(5,7);
  return `&H00${b}${g}${r}`.toUpperCase();
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function formatDur(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function truncate(str, n) {
  return str.length > n ? str.slice(0, n) + '…' : str;
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function getVal(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

function shakeInput() {
  const wrap = document.querySelector('.input-wrap');
  wrap.style.animation = 'none';
  wrap.offsetHeight;  // reflow
  wrap.style.animation = 'shake 0.4s ease';
}

function showToast(msg) {
  const t = document.createElement('div');
  t.style.cssText = `
    position:fixed;bottom:24px;right:24px;z-index:999;
    background:#22c55e;color:white;padding:12px 20px;
    border-radius:10px;font-weight:600;font-size:13px;
    animation:slideIn 0.3s ease;
  `;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Restore saved settings
  try {
    const saved = localStorage.getItem('viralclipper_settings');
    if (saved) settings = JSON.parse(saved);
  } catch (e) {}

  // Enter key on URL input
  document.getElementById('urlInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') processVideo();
  });

  // Keyboard shortcut: Escape closes modal
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeSettings();
  });

  // Add shake keyframe
  const style = document.createElement('style');
  style.textContent = `
    @keyframes shake {
      0%,100%{transform:translateX(0)}
      20%{transform:translateX(-8px)}
      40%{transform:translateX(8px)}
      60%{transform:translateX(-5px)}
      80%{transform:translateX(5px)}
    }
  `;
  document.head.appendChild(style);
});
