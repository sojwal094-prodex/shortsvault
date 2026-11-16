/* ShortSVault — Frontend JS */

// ── Theme ─────────────────────────────────────────────────────────────────
let currentTheme = 'dark';

function toggleTheme() {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  applyTheme(currentTheme);
  localStorage.setItem('viralclipper_theme', currentTheme);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : '');
  const btn = document.getElementById('themeToggle');
  if (btn) btn.textContent = theme === 'light' ? '🌙' : '☀️';
  currentTheme = theme;
}

// ── State ──────────────────────────────────────────────────────────────────
let currentMode = 'auto';
let settings    = { brand_kit: { channel_name:'', font:'Arial',
                    caption_color:'#ffffff', highlight_color:'#ffff00' } };
let activePolls = {};
let batchMode   = false;

const MODE_DESC = {
  auto:  'Uses AI API when available, otherwise the self-learned model',
  local: '🧠 Uses only your self-trained local model — no API calls, free, improves with use',
  api:   '✨ Uses Claude/OpenAI API for best quality (requires API key)',
};

// ── Mode toggle ───────────────────────────────────────────────────────────
function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  document.getElementById('modeDesc').textContent = MODE_DESC[mode] || '';
  localStorage.setItem('viralclipper_mode', mode);
}

// ── Process video ─────────────────────────────────────────────────────────
async function processVideo() {
  const goBtn = document.getElementById('goBtn');
  let urls = [];
  if (batchMode) {
    urls = document.getElementById('batchUrls').value.trim()
              .split('\n').map(u => u.trim()).filter(Boolean);
  } else {
    const u = document.getElementById('urlInput').value.trim();
    if (u) urls = [u];
  }
  if (!urls.length) { shakeInput(); return; }

  goBtn.disabled = true;
  goBtn.innerHTML = '<span class="spinner"></span><span>Processing…</span>';

  try {
    const res  = await fetch('/api/process', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ urls, brand_kit: settings.brand_kit, mode: currentMode }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    (data.job_ids || [data.job_id]).forEach(createJobCard);
    document.getElementById('jobsSection').scrollIntoView({ behavior: 'smooth' });
    document.getElementById('urlInput').value  = '';
    document.getElementById('batchUrls').value = '';
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    goBtn.disabled = false;
    goBtn.innerHTML = '<span>Generate Clips</span><span class="btn-arrow">→</span>';
  }
}

// ── Job cards ─────────────────────────────────────────────────────────────
function createJobCard(jobId) {
  const tpl  = document.getElementById('jobTemplate');
  const card = tpl.content.cloneNode(true).querySelector('.job-card');
  card.id    = `job-${jobId}`;
  document.getElementById('jobsList').prepend(card);
  updateJobCard(jobId, { status:'queued', progress:0, message:'Queued…', url:'' });
  activePolls[jobId] = setInterval(() => pollJob(jobId), 1500);
}

async function pollJob(jobId) {
  try {
    const r = await fetch(`/api/status/${jobId}`);
    updateJobCard(jobId, await r.json());
    const job = await (await fetch(`/api/status/${jobId}`)).json();
    if (job.status === 'done' || job.status === 'error') {
      clearInterval(activePolls[jobId]);
      delete activePolls[jobId];
      refreshBrainStats();   // update brain widget after job
    }
  } catch {}
}

function updateJobCard(jobId, data) {
  const card = document.getElementById(`job-${jobId}`);
  if (!card) return;

  const vi = data.video_info;
  card.querySelector('.job-title').textContent =
    vi ? vi.title : (data.url ? truncate(data.url, 60) : 'Processing video…');
  card.querySelector('.job-url').textContent = data.url || '';

  const badge = card.querySelector('.job-status-badge');
  badge.textContent = data.status || 'queued';
  badge.className   = `job-status-badge status-${data.status || 'queued'}`;

  card.querySelector('.job-progress-fill').style.width = (data.progress || 0) + '%';
  card.querySelector('.job-progress-msg').textContent  = data.message || '';

  if (data.status === 'error') {
    card.querySelector('.job-progress-msg').style.color = '#ef4444';
  }

  // Brain message
  if (data.brain_msg) {
    const bm = card.querySelector('.job-brain-msg');
    bm.textContent = data.brain_msg;
    bm.classList.remove('hidden');
  }

  // Detection source label
  if (data.detection_source) {
    const src = data.detection_source;
    const lbl = src.includes('claude') ? '✨ Claude AI'
              : src.includes('openai') ? '✨ OpenAI'
              : src === 'model'        ? '🧠 Self-learned model'
              : '⚡ Heuristic engine';
    card.querySelector('.job-progress-msg').textContent =
      (data.message || '') + (data.status === 'done' ? `  ·  ${lbl}` : '');
  }

  if (data.status === 'done' && data.clips?.length) {
    renderClips(card, data.clips, jobId);
    card.querySelector('.job-actions').classList.remove('hidden');
    card.querySelector('.btn-dl-all').onclick = () => downloadAll(jobId);
  }
}

// ── Clip rendering ────────────────────────────────────────────────────────
function renderClips(card, clips, jobId) {
  const grid = card.querySelector('.job-clips-grid');
  if (grid.dataset.rendered) return;
  grid.dataset.rendered = '1';
  grid.innerHTML = '';

  clips.forEach((clip, idx) => {
    const tpl    = document.getElementById('clipCardTemplate');
    const clipEl = tpl.content.cloneNode(true).querySelector('.clip-card');

    clipEl.querySelector('.clip-num').textContent = `Clip ${clip.index || idx+1}`;

    const score   = clip.viral_score || 0;
    const scoreEl = clipEl.querySelector('.clip-score');
    scoreEl.textContent = score;
    scoreEl.style.cssText = `font-size:22px;font-weight:900;background:${
      score>=80 ? 'linear-gradient(135deg,#22c55e,#16a34a)'
    : score>=60 ? 'linear-gradient(135deg,#8b5cf6,#ec4899)'
    :             'linear-gradient(135deg,#9090a8,#606075)'
    };-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text`;

    clipEl.querySelector('.clip-title').textContent   = clip.title   || `Clip ${idx+1}`;
    clipEl.querySelector('.clip-duration').textContent= formatDur(clip.duration || (clip.end-clip.start));
    clipEl.querySelector('.clip-type-badge').textContent = clip.type || 'insight';

    // Source badge
    const sb = clipEl.querySelector('.clip-source-badge');
    const src = clip._source || '';
    if (src === 'model') { sb.textContent='🧠 model'; sb.className='clip-source-badge src-model'; }
    else if (src.includes('api')) { sb.textContent='✨ API'; sb.className='clip-source-badge src-api'; }
    else { sb.textContent='⚡ auto'; sb.className='clip-source-badge src-heuristic'; }

    clipEl.querySelector('.clip-hook').textContent           = clip.hook    || '';
    clipEl.querySelector('.clip-caption-preview').textContent= clip.caption || '';
    clipEl.querySelector('.clip-reason').textContent         = clip.reason  || '';

    clipEl.querySelector('.btn-download').onclick = () => downloadClip(jobId, idx);
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

// ── Downloads ─────────────────────────────────────────────────────────────
function downloadClip(jobId, idx) { window.location.href = `/api/download/${jobId}/${idx}`; }
function downloadAll(jobId)       { window.location.href = `/api/download-all/${jobId}`; }

// ── Batch mode ────────────────────────────────────────────────────────────
function toggleBatch() {
  batchMode = !batchMode;
  document.getElementById('batchArea').classList.toggle('hidden', !batchMode);
  document.getElementById('batchToggleIcon').textContent = batchMode ? '−' : '+';
  const inp = document.getElementById('urlInput');
  inp.disabled    = batchMode;
  inp.placeholder = batchMode ? 'Using batch mode ↓' : 'Paste video URL here…';
}

// ── Brain stats (live refresh) ────────────────────────────────────────────
async function refreshBrainStats() {
  try {
    const r = await fetch('/api/brain/stats');
    const s = await r.json();
    // Navbar label
    const lbl = document.getElementById('brainLabel');
    if (lbl) {
      if (s.model_accuracy) {
        lbl.textContent = `Model active · ${s.total_clips} clips · ~${Math.round(s.model_accuracy)}% acc`;
      } else if (s.total_clips > 0) {
        lbl.textContent = `Learning · ${s.total_clips} clips stored`;
      } else {
        lbl.textContent = 'Waiting to learn';
      }
    }
    // Stats cards
    setTxt('bsTotal', s.total_clips);
    setTxt('bsModel', s.model_accuracy ? `~${Math.round(s.model_accuracy)}%` : '—');
    setTxt('bsAPI',   s.api_clips);
    setTxt('bsReady', s.model_ready ? '✅' : '⏳');

    // Ring
    const ring = document.getElementById('brainRing');
    if (ring) {
      const pct = Math.min(s.total_clips / 50, 1);
      ring.setAttribute('stroke-dashoffset', Math.round(138 - pct * 138));
    }
    setTxt('brainRingLabel', `${s.total_clips}<br/><small>clips</small>`);

    // Settings brain info
    const box = document.getElementById('settingsBrainInfo');
    if (box) {
      const lines = [
        `📊 Total clips stored: <b>${s.total_clips}</b>`,
        `✨ API-trained clips: <b>${s.api_clips}</b> (weighted 3× in training)`,
        `🤖 Model status: <b>${s.model_ready ? 'Active' : `${s.total_clips}/10 clips needed`}</b>`,
        s.model_accuracy ? `🎯 Estimated accuracy: <b>~${Math.round(s.model_accuracy)}%</b>` : '',
        `<br/>Every video you process contributes to the model. API clips teach it faster.`,
      ].filter(Boolean).join('<br/>');
      box.innerHTML = lines;
    }
  } catch {}
}

async function triggerRetrain() {
  const btn = event.target;
  btn.textContent = '⏳ Training…'; btn.disabled = true;
  try {
    const r = await fetch('/api/brain/train', { method:'POST' });
    const d = await r.json();
    if (d.trained) {
      showToast(`🧠 Model retrained! ${d.clips} clips, ~${Math.round(d.accuracy)}% accuracy`);
    } else {
      showToast(d.reason || 'Not enough data yet (need 10 clips)');
    }
    refreshBrainStats();
  } catch {}
  btn.textContent = '🔄 Retrain Model Now'; btn.disabled = false;
}

// ── Cookies helpers ───────────────────────────────────────────────────────
async function saveCookies() {
  const content = document.getElementById('s_cookies').value.trim();
  const r = await fetch('/api/cookies', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cookies: content}) });
  const d = await r.json();
  showToast(d.msg || 'Saved');
  refreshCookieStatus();
}
async function clearCookies() {
  await fetch('/api/cookies', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cookies: ''}) });
  document.getElementById('s_cookies').value = '';
  showToast('Cookies cleared');
  refreshCookieStatus();
}
async function refreshCookieStatus() {
  try {
    const r = await fetch('/api/cookies');
    const d = await r.json();
    const el = document.getElementById('cookieStatus');
    if (el) el.textContent = d.has_cookies ? `✅ ${d.cookie_count} cookies saved` : '⚠️ No cookies — may get bot errors';
  } catch {}
}

// ── Settings modal ────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById('settingsModal').classList.add('open');
  loadSettingsIntoForm();
  refreshBrainStats();
  refreshCookieStatus();
}
function closeSettings(e) {
  if (!e || e.target === document.getElementById('settingsModal'))
    document.getElementById('settingsModal').classList.remove('open');
}
function loadSettingsIntoForm() {
  const bk = settings.brand_kit || {};
  setVal('s_channel',         bk.channel_name    || '');
  setVal('s_font',            bk.font            || 'Arial');
  setVal('s_caption_color',   bk._caption_hex    || '#ffffff');
  setVal('s_highlight_color', bk._highlight_hex  || '#ffff00');
  fetch('/api/settings').then(r=>r.json()).then(s=>{
    setVal('s_whisper_mode',  s.whisper_mode  || 'local');
    setVal('s_whisper_model', s.whisper_model || 'base');
    setVal('s_max_clips',     s.max_clips     || 7);
    setVal('s_min_dur',       s.min_duration  || 15);
    setVal('s_max_dur',       s.max_duration  || 90);
  }).catch(()=>{});
}
function saveSettings() {
  settings.brand_kit = {
    channel_name:   getVal('s_channel'),
    font:           getVal('s_font'),
    caption_color:  hexToAss(getVal('s_caption_color')),
    highlight_color:hexToAss(getVal('s_highlight_color')),
    _caption_hex:   getVal('s_caption_color'),
    _highlight_hex: getVal('s_highlight_color'),
  };
  localStorage.setItem('viralclipper_settings', JSON.stringify(settings));
  closeSettings();
  showToast('Settings saved ✓');
}

// ── Utils ─────────────────────────────────────────────────────────────────
function hexToAss(hex) {
  if (!hex || hex[0]!=='#') return '&H00FFFFFF';
  const r=hex.slice(1,3), g=hex.slice(3,5), b=hex.slice(5,7);
  return `&H00${b}${g}${r}`.toUpperCase();
}
function formatDur(s) {
  if (!s) return '';
  return `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
}
function truncate(s, n) { return s.length>n ? s.slice(0,n)+'…' : s; }
function setVal(id, v)  { const e=document.getElementById(id); if(e) e.value=v; }
function getVal(id)     { const e=document.getElementById(id); return e?e.value:''; }
function setTxt(id, v)  { const e=document.getElementById(id); if(e) e.innerHTML=String(v); }
function shakeInput()   {
  const w=document.querySelector('.input-wrap');
  w.style.animation='none'; w.offsetHeight; w.style.animation='shake 0.4s ease';
}
function showToast(msg) {
  const t=document.createElement('div');
  t.style.cssText='position:fixed;bottom:24px;right:24px;z-index:999;background:#8b5cf6;color:white;padding:12px 20px;border-radius:10px;font-weight:600;font-size:13px;';
  t.textContent=msg; document.body.appendChild(t);
  setTimeout(()=>t.remove(), 3000);
}

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Restore settings
  try {
    const sv = localStorage.getItem('viralclipper_settings');
    if (sv) settings = JSON.parse(sv);
    const sm = localStorage.getItem('viralclipper_mode');
    if (sm) setMode(sm);
    const st = localStorage.getItem('viralclipper_theme');
    applyTheme(st || 'dark');
  } catch {}

  document.getElementById('urlInput').addEventListener('keydown', e => {
    if (e.key==='Enter') processVideo();
  });
  document.addEventListener('keydown', e => { if(e.key==='Escape') closeSettings(); });

  // Inject shake keyframe
  const s=document.createElement('style');
  s.textContent='@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-8px)}40%{transform:translateX(8px)}60%{transform:translateX(-5px)}80%{transform:translateX(5px)}}';
  document.head.appendChild(s);

  // Initial brain stats load + refresh every 30s
  refreshBrainStats();
  setInterval(refreshBrainStats, 30000);
});
