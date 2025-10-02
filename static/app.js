// Sticky header shrink on scroll
const header = document.querySelector('.app-header');
const toggleHeader = () => header.classList.toggle('scrolled', window.scrollY > 2);
document.addEventListener('scroll', toggleHeader); toggleHeader();

// Elements
const form = document.getElementById('form');
const startBtn = document.getElementById('startBtn');
const urlInput = document.getElementById('url');

const previewSkel = document.getElementById('preview-skel');
const preview = document.getElementById('preview');
const thumb = document.getElementById('thumb');
const titleEl = document.getElementById('title');
const channelAvatar = document.getElementById('channelAvatar');
const channelName = document.getElementById('channelName');
const channelSubs = document.getElementById('channelSubs');

const progressSkel = document.getElementById('progress-skel');
const progressBlock = document.getElementById('progressBlock');
const pwrap = document.getElementById('pwrap');
const bar = document.getElementById('bar');
const statusBadge = document.getElementById('statusBadge');
const downloadedEl = document.getElementById('downloaded');
const totalEl = document.getElementById('total');
const speedEl = document.getElementById('speed');
const etaEl = document.getElementById('eta');
const startedAt = document.getElementById('startedAt');
const expiresAt = document.getElementById('expiresAt'); // text inside progress chip
const progADSkel = document.getElementById('progADSkel');
const progADWrap = document.getElementById('progADWrap');

const cancelBtn = document.getElementById('cancelBtn');
const newBtnTop = document.getElementById('newBtnTop');

const errBox = document.getElementById('errBox');
const errmsgEl = document.getElementById('errmsg');

const cancelBlock = document.getElementById('cancelBlock');
const retryBtn = document.getElementById('retryBtn');
const newBtnCancel = document.getElementById('newBtnCancel');

const resultBlock = document.getElementById('resultBlock');
const filenameEl = document.getElementById('filename');
const downloadLink = document.getElementById('downloadLink');
const resultExpiresAt = document.getElementById('resultExpiresAt');
const newBtnResult = document.getElementById('newBtnResult');

const toastHolder = document.getElementById('toastHolder');

let currentJobId = null;
let currentCleanUrl = null;
let pollTimer = null;

// Toasts
function showToast(type, message){
  const toast = document.createElement('div');
  toast.className = `toast align-items-center text-bg-dark border-0 ${type==='success'?'toast-success':type==='error'?'toast-error':'toast-info'}`;
  toast.role = 'alert'; toast.ariaLive = 'assertive'; toast.ariaAtomic = 'true';
  toast.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>`;
  toastHolder.appendChild(toast);
  const bsToast = new bootstrap.Toast(toast, { delay: 3500 });
  bsToast.show();
  toast.addEventListener('hidden.bs.toast', ()=> toast.remove());
}

// URL sanitation (client)
function cleanYouTubeUrl(raw){
  try{
    const u = new URL(raw);
    const host = u.hostname.toLowerCase();
    const allowed = ['youtube.com','www.youtube.com','m.youtube.com','youtu.be','music.youtube.com'];
    if (!allowed.includes(host)) return { ok:false, error:'Only YouTube URLs are supported.' };
    if (host === 'youtu.be'){
      const id = u.pathname.replace(/^\/+/,'').split('/')[0];
      if (!id) return { ok:false, error:'Invalid youtu.be URL.' };
      return { ok:true, url:`https://www.youtube.com/watch?v=${id}`, kind:'video' };
    }
    const path = u.pathname || '/';
    if (path.startsWith('/watch')){
      const v = u.searchParams.get('v');
      if (!v) return { ok:false, error:'Missing video id.' };
      return { ok:true, url:`https://www.youtube.com/watch?v=${v}`, kind:'video' };
    }
    if (path.includes('/shorts/')){
      const parts = path.split('/').filter(Boolean);
      const idx = parts.indexOf('shorts');
      if (idx === -1 || !parts[idx+1]) return { ok:false, error:'Invalid shorts URL.' };
      return { ok:true, url:`https://www.youtube.com/shorts/${parts[idx+1]}`, kind:'short' };
    }
    if (host === 'music.youtube.com' && path.startsWith('/watch')){
      const v = u.searchParams.get('v');
      if (!v) return { ok:false, error:'Missing video id.' };
      return { ok:true, url:`https://www.youtube.com/watch?v=${v}`, kind:'video' };
    }
    return { ok:false, error:'Only direct YouTube video or shorts URLs are supported.' };
  }catch{
    return { ok:false, error:'Invalid URL.' };
  }
}

// Helpers
function show(el){ el.classList.remove('d-none'); }
function hide(el){ el.classList.add('d-none'); }
function focusInput(){ urlInput.focus({ preventScroll:true }); }

// Preview skeleton controller
const loader = {
  need:0, done:0, armed:false, timeoutId:null,
  reset(){ this.need=0; this.done=0; this.armed=false; clearTimeout(this.timeoutId); },
  add(){ this.need++; },
  mark(){ this.done++; if (this.armed && this.done>=this.need){ setTimeout(()=>{ hide(previewSkel); show(preview); }, 60); } },
  arm(){ this.armed=true; if (this.need===0){ hide(previewSkel); show(preview); }
         this.timeoutId=setTimeout(()=>{ hide(previewSkel); show(preview); }, 1000); }
};

// Progress bar skeleton toggles
function startBarSkeleton(){ pwrap?.classList.add('skel'); }
function stopBarSkeleton(){ pwrap?.classList.remove('skel'); }

function resetUI(){
  [preview, previewSkel, progressSkel, progressBlock, resultBlock, cancelBlock, errBox].forEach(hide);
  titleEl.textContent = ''; channelName.textContent = ''; channelSubs.textContent = '';
  thumb.removeAttribute('src'); channelAvatar.removeAttribute('src');
  bar.style.width = '0%'; bar.textContent = '0%';
  downloadedEl.textContent = '0 B'; totalEl.textContent = '?'; speedEl.textContent = '-'; etaEl.textContent = '-';
  startedAt.textContent = '—'; expiresAt.textContent = '—';
  hide(newBtnTop); show(cancelBtn);

  // Progress: show shimmer on Auto-deletes (text hidden)
  show(progADSkel); hide(progADWrap); expiresAt.textContent = '—';
  // Progress bar shimmer off by default until the block is shown
  stopBarSkeleton();

  currentJobId = null;
}

function clearForNew(){
  resetUI();
  urlInput.value = '';
  focusInput();
}

// Cancel handling
cancelBtn.addEventListener('click', async ()=>{
  if (!currentJobId) return;
  try{
    const r = await fetch(`/api/cancel/${currentJobId}`, { method:'POST' }).then(r=>r.json());
    if (r.ok){
      showToast('info','Cancelling…');
    }else{
      showToast('error', r.message || 'Failed to cancel.');
    }
  }catch(e){
    showToast('error','Failed to cancel.');
  }
});

// New Video buttons
newBtnTop.addEventListener('click', clearForNew);
document.getElementById('newBtnCancel').addEventListener('click', clearForNew);
document.getElementById('newBtnResult').addEventListener('click', clearForNew);

// Retry from cancelled
document.getElementById('retryBtn').addEventListener('click', async ()=>{
  if (!currentCleanUrl){
    showToast('error','No URL to retry.');
    return;
  }
  hide(cancelBlock);
  show(progressSkel);
  try{
    const start = await fetch('/api/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ url: currentCleanUrl })
    }).then(r=>r.json());
    if (!start.ok) throw new Error(start.error || 'Failed to restart download');
    currentJobId = start.job_id;
    hide(progressSkel); show(progressBlock);
    // Show progress bar shimmer until we confirm it's started
    startBarSkeleton();
    show(cancelBtn); hide(newBtnTop);
    pollTimer = setInterval(poll, 900);
    showToast('info','Retry started.');
  }catch(err){
    hide(progressSkel);
    showToast('error', err.message);
  }
});

// Submit flow
form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  if (startBtn.disabled) return;

  resetUI();
  startBtn.disabled = true;

  const raw = urlInput.value.trim();
  const cleaned = cleanYouTubeUrl(raw);
  if (!cleaned.ok){
    startBtn.disabled = false;
    showToast('error', cleaned.error);
    return;
  }
  urlInput.value = cleaned.url;
  currentCleanUrl = cleaned.url;

  show(previewSkel); show(progressSkel);

  // Probe (preview)
  try{
    const probe = await fetch('/api/probe', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ url: currentCleanUrl })
    }).then(r=>r.json());
    if (!probe.ok) throw new Error(probe.error || 'Probe failed');
    const meta = probe.meta || {};

    loader.reset();
    if (meta.thumbnail){
      loader.add();
      thumb.onload = ()=>loader.mark();
      thumb.onerror = ()=>loader.mark();
      thumb.src = meta.thumbnail;
    } else {
      thumb.removeAttribute('src');
    }

    const name = meta.uploader || 'Unknown';
    if (meta.channel_avatar){
      channelAvatar.onload = ()=>{};
      channelAvatar.onerror = ()=>{ channelAvatar.src = svgAvatar(name); };
      channelAvatar.src = meta.channel_avatar;
    } else {
      channelAvatar.src = svgAvatar(name);
    }

    titleEl.textContent = meta.title || '(Untitled)';
    channelName.textContent = name;
    channelSubs.textContent = (meta.subscribers != null) ? formatSubs(meta.subscribers) : '— subscribers';

    loader.arm();
  }catch(err){
    hide(previewSkel);
    showToast('error', err.message);
    startBtn.disabled = false;
    return;
  }

  // Start download
  try{
    const start = await fetch('/api/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ url: currentCleanUrl })
    }).then(r=>r.json());
    if (!start.ok) throw new Error(start.error || 'Failed to start download');

    currentJobId = start.job_id;
    hide(progressSkel); show(progressBlock);
    // Progress bar shimmer ON until we detect status != queued
    startBarSkeleton();
    // Ensure Auto-deletes skeleton visible
    show(progADSkel); hide(progADWrap); expiresAt.textContent='—';

    pollTimer = setInterval(poll, 900);
    showToast('info','Downloading started.');
  }catch(err){
    hide(progressSkel);
    showToast('error', err.message);
    startBtn.disabled = false;
  }finally{
    startBtn.disabled = false;
  }
});

// Polling
async function poll(){
  if (!currentJobId) return;
  try{
    const data = await fetch(`/api/progress/${currentJobId}`).then(r=>r.json());
    if (!data.ok) throw new Error(data.error || 'Progress error');

    if (data.started_at_ist) startedAt.textContent = `Started: ${data.started_at_ist}`;

    // Hide progress bar shimmer once we are past queued (download really started)
    if (data.status && data.status.toLowerCase() !== 'queued'){
      stopBarSkeleton();
    }

    if (data.expires_at_ist && (data.status === 'downloading' || data.status === 'processing' || data.status === 'finished')){
      expiresAt.textContent = data.expires_at_ist;
      hide(progADSkel); show(progADWrap);
    }

    updateProgress(data);

    if (data.status === 'finished'){
      clearInterval(pollTimer);
      hide(cancelBtn); show(newBtnTop);

      filenameEl.textContent = data.filename || '(unknown)';
      if (data.download_url) downloadLink.href = data.download_url;
      resultExpiresAt.textContent = data.expires_at_ist || '—';
      show(resultBlock);
    } else if (data.status === 'expired'){
      clearInterval(pollTimer);
      showToast('error', 'This file has expired and was auto-deleted.');
      hide(cancelBtn); show(newBtnTop);
    } else if (data.status === 'cancelled'){
      clearInterval(pollTimer);
      showToast('info', 'Download cancelled.');
      hide(progressBlock); hide(resultBlock);
      show(cancelBlock);
    } else if (data.status === 'error'){
      clearInterval(pollTimer);
      showToast('error', data.error || 'Unknown error');
      show(errBox); errmsgEl.textContent = data.error || 'Unknown error';
      hide(cancelBtn); show(newBtnTop);
    }
  }catch(err){
    clearInterval(pollTimer);
    showToast('error', err.message);
    hide(cancelBtn); show(newBtnTop);
  }
}

function updateProgress(d){
  let pct = Number(d.progress || 0);
  if ((d.status || '') === 'processing' && pct < 99) pct = 99;
  pct = Math.max(0, Math.min(100, pct));
  bar.style.width = pct + '%';
  bar.textContent = pct.toFixed(0) + '%';

  const displayStatus = (s)=>{
    if (!s) return '—';
    s = s.toLowerCase();
    if (s === 'processing') return 'downloading';
    return s;
  };
  statusBadge.textContent = displayStatus(d.status).toUpperCase();

  downloadedEl.textContent = d.downloaded || '0 B';
  totalEl.textContent = d.total || '?';
  speedEl.textContent = d.speed || '-';
  etaEl.textContent = d.eta != null ? (d.eta + 's') : '-';
}

function svgAvatar(name){
  const initial = (name || '?').trim().charAt(0).toUpperCase();
  const bg = '#5566aa', fg = '#e9ecff';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">
    <rect width="40" height="40" rx="20" fill="${bg}"/>
    <text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle"
      font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial"
      font-size="18" font-weight="700" fill="${fg}">${initial}</text>
  </svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

function formatSubs(n){
  n = Number(n);
  const units = [['B',1],['K',1e3],['M',1e6],['B',1e9]];
  for (let i = units.length-1; i>=0; i--){
    if (n >= units[i][1]){
      const val = (n/units[i][1]).toFixed(n>=100000?0:1).replace(/\.0$/,'');
      return `${val}${units[i][0]} subscribers`;
    }
  }
  return `${n} subscribers`;
}

// Init
(function init(){
  resetUI();
  focusInput();
})();
