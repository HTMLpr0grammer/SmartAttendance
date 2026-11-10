/* Instructor live session: rotating QR, countdown ring, live roster, rogue-scan feed. */
(function () {
  'use strict';
  const root = document.getElementById('live-root');
  if (!root) return;

  const SID = +root.dataset.sessionId;
  const SUBJECT = +root.dataset.subjectId;
  let REFRESH = +root.dataset.refresh || 8;
  // Roster / rogue-scan feed refresh. Kept slow on purpose (CPU-friendly on small hosts);
  // use "Refresh now" or any edit for an instant update. Stops entirely once the session closes.
  const LIVE_POLL_MS = (+root.dataset.livePoll || 30) * 1000;
  const $ = id => document.getElementById(id);
  const RING_C = 2 * Math.PI * 44;

  let isOpen = true;
  let nextTokenAt = 0;
  let tokenTimer = null;
  let liveTimer = null;
  let tickTimer = null;
  let wakeLock = null;
  let lastSyncAt = 0;
  let expiresAt = null;
  let expiryHandled = false;
  const knownStatus = new Map();   // student_id -> status (for new check-in flashes)
  const rows = new Map();          // student_id -> <tr>

  // ======================================================== QR token rotation
  async function fetchToken() {
    clearTimeout(tokenTimer);
    if (!isOpen) return;
    const r = await SA.api(`/api/sessions/${SID}/qr-token/`);
    if (r.status === 410 || (r.data && r.data.session && !r.data.session.is_open)) {
      setClosed();
      fetchLive();   // one last roster fetch so the auto-marked absences show up
      return;
    }
    if (!r.ok) {
      $('qr-stale').classList.replace('hidden', 'flex');
      tokenTimer = setTimeout(fetchToken, 2000);
      return;
    }
    $('qr-stale').classList.replace('flex', 'hidden');
    REFRESH = r.data.refresh_seconds || REFRESH;
    const box = $('qr-box');
    box.style.opacity = '0.25';
    box.innerHTML = r.data.svg;
    requestAnimationFrame(() => (box.style.opacity = '1'));
    applySession(r.data.session);
    nextTokenAt = Date.now() + REFRESH * 1000;
    tokenTimer = setTimeout(fetchToken, REFRESH * 1000);
  }

  function tick() {
    if (isOpen && nextTokenAt) {
      const left = Math.max(0, nextTokenAt - Date.now());
      const secs = Math.ceil(left / 1000);
      $('ring').style.strokeDashoffset = String(RING_C * (1 - left / (REFRESH * 1000)));
      $('ring-num').textContent = secs;
      $('ring-text').textContent = secs;
    }
    if (isOpen && expiresAt) {
      const s = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      $('session-left').textContent = (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
      // fetchToken() answers 410 once the server has closed the session, which also triggers the final roster fetch.
      if (s === 0 && !expiryHandled) { expiryHandled = true; setTimeout(fetchToken, 1200); }
    }
  }

  function applySession(s) {
    if (!s) return;
    expiresAt = new Date(s.expires_at).getTime();
    if (s.is_open) setOpenUI(); else setClosed(s);
  }

  function setOpenUI() {
    $('qr-live').classList.replace('hidden', 'flex');
    $('qr-closed').classList.add('hidden');
    $('live-badge').classList.replace('hidden', 'inline-flex');
    $('closed-badge').classList.add('hidden');
    $('btn-close').classList.remove('hidden');
  }

  function setClosed() {
    isOpen = false;
    // Stop ALL background work: QR rotation, roster polling and the countdown ticker.
    clearTimeout(tokenTimer);
    clearInterval(liveTimer); liveTimer = null;
    clearInterval(tickTimer); tickTimer = null;
    releaseWakeLock();
    $('sync-label').textContent = 'Session closed – updates after each edit';
    $('qr-box').innerHTML = '';
    $('qr-live').classList.replace('flex', 'hidden');
    $('qr-closed').classList.remove('hidden');
    $('live-badge').classList.replace('inline-flex', 'hidden');
    $('closed-badge').classList.remove('hidden');
    $('btn-close').classList.add('hidden');
    $('btn-fullscreen').classList.add('hidden');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }

  // ======================================================== Live roster / feeds
  async function fetchLive() {
    const r = await SA.api(`/api/sessions/${SID}/live/`);
    if (!r.ok) return;
    const d = r.data;
    if (d.session && !d.session.is_open && isOpen) setClosed(d.session);
    else if (d.session && d.session.is_open && !expiresAt) applySession(d.session);
    renderCounts(d.counts);
    renderRoster(d.roster);
    renderRogue(d.rogue);
    renderAlerts(d.alerts);
    lastSyncAt = Date.now();
    $('last-sync').textContent = new Date().toLocaleTimeString();
  }

  function renderCounts(c) {
    $('c-present').textContent = c.present;
    $('c-late').textContent = c.late;
    $('c-absent').textContent = c.absent;
    $('c-waiting').textContent = c.not_scanned;
    $('fs-present').textContent = c.present;
    $('fs-late').textContent = c.late;
  }

  const ROW_TINT = { present: 'bg-emerald-50/40', late: 'bg-amber-50/50', absent: 'bg-rose-50/40' };

  function buildRow(s) {
    const tr = document.createElement('tr');
    tr.dataset.student = s.student_id;
    tr.className = 'transition-colors duration-700';
    tr.innerHTML = `
      <td class="px-4 py-3">
        <button type="button" data-act="profile" class="text-left">
          <p class="font-semibold text-slate-800 hover:text-brand-700">${SA.esc(s.name)}</p>
          <p class="text-xs text-slate-500"><span>${SA.esc(s.id_number || '')}</span> · <span data-f="device"></span></p>
        </button>
      </td>
      <td class="whitespace-nowrap px-3 py-3 text-slate-600" data-f="time"></td>
      <td class="px-3 py-3">
        <select data-f="status" class="rounded-lg border-slate-300 py-1.5 pl-2 pr-8 text-sm font-medium focus:border-brand-600 focus:ring-brand-600">
          <option value="" disabled>— Not scanned —</option>
          <option value="present">Present</option>
          <option value="late">Late</option>
          <option value="absent">Absent</option>
        </select>
      </td>
      <td class="px-3 py-3">
        <input data-f="notes" type="text" maxlength="2000" placeholder="Add note…" class="w-full rounded-lg border-slate-200 py-1.5 text-sm placeholder:text-slate-400 focus:border-brand-600 focus:ring-brand-600">
      </td>
      <td class="whitespace-nowrap px-4 py-3 text-right">
        <div class="inline-flex gap-1">
          <button type="button" data-act="profile" class="rounded-md px-2 py-1 text-xs font-semibold text-brand-700 hover:bg-brand-50">Profile</button>
          <button type="button" data-act="unbind" class="rounded-md px-2 py-1 text-xs font-semibold text-sky-700 hover:bg-sky-50">Unbind Device</button>
          <button type="button" data-act="drop" class="rounded-md px-2 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50">Drop</button>
        </div>
      </td>`;

    const sel = tr.querySelector('[data-f="status"]');
    const notes = tr.querySelector('[data-f="notes"]');
    sel.addEventListener('change', async () => {
      const r = await SA.api(`/api/sessions/${SID}/attendance/`, { method: 'POST', body: { student_id: s.student_id, status: sel.value } });
      SA.toast(r.ok ? `${s.name}: marked ${sel.value}` : SA.errorText(r.data), r.ok ? 'success' : 'error', 2500);
      fetchLive();
    });
    notes.addEventListener('input', () => (notes.dataset.dirty = '1'));
    const saveNotes = async () => {
      if (!notes.dataset.dirty) return;
      const r = await SA.api(`/api/sessions/${SID}/attendance/`, { method: 'POST', body: { student_id: s.student_id, notes: notes.value } });
      if (r.ok) { delete notes.dataset.dirty; notes.classList.add('ring-2', 'ring-emerald-400'); setTimeout(() => notes.classList.remove('ring-2', 'ring-emerald-400'), 900); }
      else SA.toast(SA.errorText(r.data), 'error');
      fetchLive();
    };
    notes.addEventListener('change', saveNotes);
    notes.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); notes.blur(); } });

    tr.addEventListener('click', e => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const name = tr.dataset.name;
      if (btn.dataset.act === 'profile') SA.openProfile(SUBJECT, s.student_id);
      if (btn.dataset.act === 'unbind') SA.unbindDevice(s.student_id, name, fetchLive);
      if (btn.dataset.act === 'drop') SA.dropStudent(SUBJECT, s.student_id, name, fetchLive);
    });
    return tr;
  }

  function updateRow(tr, s) {
    tr.dataset.name = s.name;
    const sel = tr.querySelector('[data-f="status"]');
    const notes = tr.querySelector('[data-f="notes"]');
    if (document.activeElement !== sel) sel.value = s.status || '';
    if (document.activeElement !== notes && !notes.dataset.dirty) notes.value = s.notes || '';
    tr.querySelector('[data-f="time"]').textContent = s.scanned_at ? SA.fmtTime(s.scanned_at) : (s.status ? 'manual' : '—');
    tr.querySelector('[data-f="device"]').innerHTML = s.device_bound ? '📱 bound' : '<span class="text-amber-600">no device</span>';
    tr.querySelector('[data-act="unbind"]').classList.toggle('hidden', !s.device_bound);
    Object.values(ROW_TINT).forEach(c => tr.classList.remove(c));
    if (s.status) tr.classList.add(ROW_TINT[s.status]);

    const prev = knownStatus.get(s.student_id);
    if (prev === null && (s.status === 'present' || s.status === 'late') && s.scanned_at) {
      tr.classList.add('bg-emerald-200');
      setTimeout(() => tr.classList.remove('bg-emerald-200'), 1500);
    }
    knownStatus.set(s.student_id, s.status || null);
  }

  function renderRoster(list) {
    const body = $('roster-body');
    if (!list.length) {
      rows.clear();
      body.innerHTML = '<tr><td colspan="5" class="px-4 py-10 text-center text-slate-500">No students enrolled in this subject yet. Approve rogue scans to add them.</td></tr>';
      return;
    }
    if (!rows.size) body.innerHTML = '';
    const seen = new Set();
    list.forEach(s => {
      seen.add(s.student_id);
      let tr = rows.get(s.student_id);
      if (!tr) { tr = buildRow(s); rows.set(s.student_id, tr); body.appendChild(tr); }
      updateRow(tr, s);
    });
    for (const [id, tr] of rows) if (!seen.has(id)) { tr.remove(); rows.delete(id); knownStatus.delete(id); }
    // Keep alphabetical order (only re-append if it changed, to preserve input focus).
    const desired = list.map(s => rows.get(s.student_id));
    const current = Array.from(body.children);
    if (desired.some((tr, i) => current[i] !== tr)) desired.forEach(tr => body.appendChild(tr));
  }

  function renderRogue(list) {
    $('rogue-count').textContent = list.length;
    const ul = $('rogue-list');
    if (!list.length) { ul.innerHTML = '<li class="px-4 py-6 text-center text-xs text-slate-500">No unenrolled scans yet.</li>'; return; }
    ul.innerHTML = list.map(r => `
      <li class="flex items-center justify-between gap-3 px-4 py-3">
        <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${r.position === 1 ? 'bg-sky-600 text-white' : 'bg-slate-100 text-slate-600'} text-[11px] font-bold" title="Position in the queue (first come, first served)">${r.position}</span>
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-semibold text-slate-800">${SA.esc(r.name)}${r.position === 1 ? ' <span class="text-[10px] font-bold uppercase text-sky-700">· next</span>' : ''}</p>
          <p class="truncate text-xs text-slate-500">${SA.esc(r.id_number || 'No ID')}${r.course ? ' · ' + SA.esc(r.course) : ''} · ${SA.fmtTime(r.scanned_at)}</p>
          ${r.account_status === 'pending' ? '<span class="mt-1 inline-block rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">PENDING ACCOUNT</span>' : ''}
        </div>
        <button type="button" data-approve="${r.audit_id}" class="shrink-0 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-700">Add to Class &amp; Approve</button>
      </li>`).join('');
  }

  $('rogue-list').addEventListener('click', async e => {
    const btn = e.target.closest('[data-approve]');
    if (!btn) return;
    btn.disabled = true; btn.textContent = 'Adding…';
    const r = await SA.api(`/api/sessions/${SID}/rogue/${btn.dataset.approve}/approve/`, { method: 'POST' });
    SA.toast(r.ok ? r.data.detail : SA.errorText(r.data), r.ok ? 'success' : 'error');
    fetchLive();
  });

  const REASON_TINT = { device_mismatch: 'bg-rose-100 text-rose-700', out_of_bounds: 'bg-orange-100 text-orange-700',
                        expired_token: 'bg-slate-100 text-slate-600', session_closed: 'bg-violet-100 text-violet-700' };
  function renderAlerts(list) {
    $('alert-count').textContent = list.length;
    const ul = $('alert-list');
    if (!list.length) { ul.innerHTML = '<li class="px-4 py-6 text-center text-xs text-slate-500">No rejected scans.</li>'; return; }
    ul.innerHTML = list.map(a => `
      <li class="flex items-center justify-between gap-3 px-4 py-2.5">
        <div class="min-w-0"><p class="truncate text-sm font-medium text-slate-800">${SA.esc(a.name)}</p>
        <p class="text-xs text-slate-500">${SA.fmtTime(a.created_at)}</p></div>
        <span class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${REASON_TINT[a.reason] || 'bg-slate-100 text-slate-600'}">${SA.esc(a.reason_display)}</span>
      </li>`).join('');
  }

  // ======================================================== Controls
  $('btn-close').addEventListener('click', async () => {
    const ok = await SA.confirmDialog({
      title: 'End this session?',
      body: 'The QR code will stop working immediately and every enrolled student who has not checked in will be marked absent.',
      okText: 'End session',
    });
    if (!ok) return;
    const r = await SA.api(`/api/sessions/${SID}/close/`, { method: 'POST' });
    if (r.ok) { setClosed(r.data.session); SA.toast('Session closed.', 'success'); fetchLive(); }
    else SA.toast(SA.errorText(r.data), 'error');
  });

  $('btn-fullscreen').addEventListener('click', () => {
    const card = $('qr-card');
    if (!document.fullscreenElement) card.requestFullscreen?.().catch(() => SA.toast('Fullscreen not available.', 'warning'));
    else document.exitFullscreen();
  });
  document.addEventListener('fullscreenchange', () => {
    const fs = !!document.fullscreenElement;
    document.querySelectorAll('[data-fs-only]').forEach(el => el.classList.toggle('hidden', !fs));
    $('qr-box').parentElement.classList.toggle('max-w-[26rem]', !fs);
    $('qr-box').parentElement.classList.toggle('max-w-[min(80vh,42rem)]', fs);
  });

  $('btn-refresh').addEventListener('click', fetchLive);

  // Keep the projector screen awake while the session is open (released as soon as it closes).
  function acquireWakeLock() {
    if (!('wakeLock' in navigator) || !isOpen || wakeLock) return;
    navigator.wakeLock.request('screen').then(l => {
      if (!isOpen) { l.release().catch(() => {}); return; }
      wakeLock = l;
      l.addEventListener('release', () => { if (wakeLock === l) wakeLock = null; });
    }).catch(() => {});
  }
  function releaseWakeLock() {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || !isOpen) return;
    acquireWakeLock();
    if (Date.now() - lastSyncAt > 5000) fetchLive();   // catch up after the tab was in the background
  });

  // ======================================================== Boot
  $('sync-label').textContent = `Auto-refreshing every ${Math.round(LIVE_POLL_MS / 1000)} s`;
  tickTimer = setInterval(tick, 200);
  fetchToken();
  fetchLive();
  liveTimer = setInterval(fetchLive, LIVE_POLL_MS);
  acquireWakeLock();
})();
