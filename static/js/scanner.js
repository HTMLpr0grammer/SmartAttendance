/* Student QR scanner: html5-qrcode + device UUID binding + GPS + POST /api/attendance/scan/ */
(function () {
  'use strict';
  const root = document.getElementById('scanner');
  if (!root) return;
  const $ = id => document.getElementById(id);

  // ------------------------------------------------------------ Device UUID (RFC 4122 v4)
  const DEVICE_KEY = 'smartattendance_device_uuid';
  function uuidv4() {
    if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;  // version 4
    b[8] = (b[8] & 0x3f) | 0x80;  // variant 10xx
    const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  function getDeviceId() {
    let id = null;
    try { id = localStorage.getItem(DEVICE_KEY); } catch (_) { /* private mode */ }
    if (!id || !/^[0-9a-f-]{36}$/i.test(id)) {
      id = uuidv4();
      try { localStorage.setItem(DEVICE_KEY, id); } catch (_) {}
    }
    return id;
  }
  const DEVICE_ID = getDeviceId();
  $('device-short').textContent = DEVICE_ID.slice(0, 8) + '…';

  // ------------------------------------------------------------ Environment checks
  function warn(msg) { const w = $('scan-warning'); w.textContent = msg; w.classList.remove('hidden'); }
  if (!window.isSecureContext) warn('Camera and GPS need HTTPS. Open this page via https:// (or localhost).');
  if (typeof Html5Qrcode === 'undefined') warn('Scanner library failed to load. Check your internet connection.');

  // ------------------------------------------------------------ GPS (continuous so a fix is ready at scan time)
  let lastFix = null, watchId = null;
  function startGps() {
    if (!('geolocation' in navigator)) { $('gps-status').textContent = 'Location unsupported on this browser'; return; }
    if (watchId !== null) return;
    $('gps-status').textContent = 'Location: locating…';
    watchId = navigator.geolocation.watchPosition(
      p => {
        lastFix = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy, t: Date.now() };
        $('gps-status').textContent = `Location locked (±${Math.round(p.coords.accuracy)} m)`;
      },
      e => { $('gps-status').textContent = 'Location blocked – enable GPS permission'; lastFix = null; console.warn(e); },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 }
    );
  }
  function freshFix() {
    if (lastFix && Date.now() - lastFix.t < 20000) return Promise.resolve(lastFix);
    return new Promise(resolve => {
      if (!('geolocation' in navigator)) return resolve(null);
      navigator.geolocation.getCurrentPosition(
        p => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy, t: Date.now() }),
        () => resolve(null),
        { enableHighAccuracy: true, timeout: 6000, maximumAge: 10000 }
      );
    });
  }

  // ------------------------------------------------------------ Camera
  let scanner = null, running = false, busy = false, torchOn = false;
  let cameras = [], camIndex = -1;

  function show(id, on, display = 'flex') {
    const el = $(id);
    el.classList.toggle('hidden', !on);
    if (display === 'flex') el.classList.toggle('flex', on);
  }

  async function startCamera() {
    if (typeof Html5Qrcode === 'undefined') return;
    startGps();
    show('scan-idle', false);
    show('scan-result', false);
    try {
      scanner = scanner || new Html5Qrcode('reader', { verbose: false });
      const source = camIndex >= 0 && cameras[camIndex] ? cameras[camIndex].id : { facingMode: 'environment' };
      await scanner.start(source, { fps: 12, aspectRatio: 1.0, disableFlip: false }, onDecoded, () => {});
      running = true;
      show('scan-overlay', true, 'block');
      ['scan-stop'].forEach(id => $(id).classList.remove('hidden'));
      setupTorch();
      Html5Qrcode.getCameras().then(c => { cameras = c || []; $('scan-switch').classList.toggle('hidden', cameras.length < 2); }).catch(() => {});
    } catch (err) {
      running = false;
      show('scan-idle', true);
      const m = String(err && (err.message || err));
      SA.toast(/permission|notallowed/i.test(m) ? 'Camera permission denied. Allow camera access in your browser settings.' : 'Unable to start camera: ' + m, 'error', 6000);
    }
  }

  async function stopCamera() {
    if (scanner && running) { try { await scanner.stop(); } catch (_) {} }
    running = false; torchOn = false;
    show('scan-overlay', false, 'block');
    show('scan-idle', true);
    ['scan-stop', 'scan-torch', 'scan-switch'].forEach(id => $(id).classList.add('hidden'));
  }

  // ------------------------------------------------------------ Torch
  function torchFeature() {
    try {
      const caps = scanner.getRunningTrackCameraCapabilities?.();
      const t = caps && caps.torchFeature && caps.torchFeature();
      if (t && t.isSupported()) return { set: on => t.apply(on) };
    } catch (_) {}
    try {
      const c = scanner.getRunningTrackCapabilities?.();
      if (c && 'torch' in c) return { set: on => scanner.applyVideoConstraints({ advanced: [{ torch: on }] }) };
    } catch (_) {}
    return null;
  }
  function setupTorch() {
    const t = torchFeature();
    $('scan-torch').classList.toggle('hidden', !t);
    $('scan-torch').setAttribute('aria-pressed', 'false');
  }
  $('scan-torch').addEventListener('click', async () => {
    const t = torchFeature();
    if (!t) return SA.toast('Torch is not supported on this camera.', 'warning');
    try {
      torchOn = !torchOn;
      await t.set(torchOn);
      $('scan-torch').setAttribute('aria-pressed', String(torchOn));
      $('scan-torch').classList.toggle('bg-gold-500', torchOn);
      $('scan-torch').classList.toggle('text-slate-900', torchOn);
    } catch (e) { torchOn = false; SA.toast('Could not toggle torch.', 'error'); }
  });

  $('scan-switch').addEventListener('click', async () => {
    if (cameras.length < 2) return;
    camIndex = (camIndex + 1) % cameras.length;
    await stopCamera();
    startCamera();
  });

  // ------------------------------------------------------------ Decode → submit
  const looksLikeJwt = s => /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(s);

  async function onDecoded(text) {
    if (busy) return;
    busy = true;
    try { scanner.pause(true); } catch (_) {}
    if (navigator.vibrate) navigator.vibrate(60);

    if (!looksLikeJwt(text.trim())) {
      showResult(false, 'Not a SmartAttendance code', 'Scan the QR code shown on your instructor\'s screen.');
      return;
    }
    showResult(null, 'Verifying…', 'Checking code, device and location.');
    const fix = await freshFix();
    const r = await SA.api('/api/attendance/scan/', {
      method: 'POST',
      body: {
        token: text.trim(),
        latitude: fix ? +fix.lat.toFixed(6) : null,
        longitude: fix ? +fix.lng.toFixed(6) : null,
        device_id: DEVICE_ID,
      },
    });
    const d = r.data || {};
    if (r.ok) {
      const late = d.status === 'late';
      showResult(late ? 'late' : true, late ? 'Checked in – Late' : 'Checked in!', `${d.message}${d.device_bound_now ? ' This phone is now bound to your account.' : ''}`);
      if (navigator.vibrate) navigator.vibrate([80, 60, 80]);
      setTimeout(() => window.dispatchEvent(new CustomEvent('sa:scanned', { detail: d })), 300);
    } else {
      const titles = {
        device_mismatch: 'Device mismatch', device_in_use: 'Phone already in use', out_of_bounds: 'Outside classroom', unenrolled_student: 'Not enrolled',
        expired_token: 'Code expired', session_closed: 'Session closed', suspended: 'Account suspended',
      };
      let msg = SA.errorText(d, 'Scan rejected.');
      if (d.code === 'unenrolled_student') msg += ' Your instructor has been notified and can add you to the class.';
      if (d.code === 'expired_token') msg += ' Codes refresh every few seconds – scan the current one.';
      if (d.code === 'out_of_bounds' && d.distance_m) msg += ` (≈${Math.round(d.distance_m)} m away)`;
      showResult(false, titles[d.code] || 'Scan rejected', msg);
    }
  }

  function showResult(ok, title, msg) {
    const box = $('scan-result');
    box.classList.remove('bg-emerald-600/90', 'bg-rose-600/90', 'bg-amber-500/90', 'bg-slate-900/80');
    box.classList.add(ok === null ? 'bg-slate-900/80' : ok === 'late' ? 'bg-amber-500/90' : ok ? 'bg-emerald-600/90' : 'bg-rose-600/90');
    $('scan-result-icon').className = 'flex h-20 w-20 items-center justify-center rounded-full text-4xl bg-white/20';
    $('scan-result-icon').textContent = ok === null ? '⏳' : ok === 'late' ? '⏰' : ok ? '✅' : '⛔';
    $('scan-result-title').textContent = title;
    $('scan-result-msg').textContent = msg;
    $('scan-again').classList.toggle('hidden', ok === null);
    show('scan-result', true);
  }

  $('scan-again').addEventListener('click', () => {
    show('scan-result', false);
    busy = false;
    try { scanner.resume(); } catch (_) { startCamera(); }
  });
  $('scan-start').addEventListener('click', startCamera);
  $('scan-stop').addEventListener('click', stopCamera);
  document.addEventListener('visibilitychange', () => { if (document.hidden && running) stopCamera(); });

  if (root.dataset.autostart === '1') startCamera();
})();
