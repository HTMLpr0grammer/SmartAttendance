/* SmartAttendance – shared front-end helpers (vanilla JS, no build step). */
(function () {
  'use strict';

  // ------------------------------------------------------------------ CSRF / fetch
  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? decodeURIComponent(m.pop()) : null;
  }

  async function api(url, { method = 'GET', body = null } = {}) {
    const opts = {
      method,
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    };
    if (method !== 'GET') {
      opts.headers['X-CSRFToken'] = getCookie('csrftoken');
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body || {});
    }
    let res, data = {};
    try {
      res = await fetch(url, opts);
    } catch (e) {
      return { ok: false, status: 0, data: { detail: 'Network error. Check your connection.' } };
    }
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    return { ok: res.ok && data.ok !== false, status: res.status, data };
  }

  function errorText(data, fallback = 'Something went wrong.') {
    if (!data) return fallback;
    if (data.message) return data.message;
    if (data.detail) return data.detail;
    if (data.non_field_errors) return data.non_field_errors.join(' ');
    const first = Object.values(data).find(v => Array.isArray(v));
    return first ? first.join(' ') : fallback;
  }

  // ------------------------------------------------------------------ Toasts
  function toast(message, type = 'info', ms = 4000) {
    const stack = document.getElementById('toast-stack');
    if (!stack) return alert(message);
    const colors = {
      success: 'bg-emerald-600', error: 'bg-rose-600', warning: 'bg-amber-500', info: 'bg-slate-800',
    };
    const el = document.createElement('div');
    el.className = `pointer-events-auto max-w-md rounded-xl px-4 py-3 text-sm font-medium text-white shadow-lg transition-all duration-300 opacity-0 -translate-y-2 ${colors[type] || colors.info}`;
    el.textContent = message;
    stack.appendChild(el);
    requestAnimationFrame(() => el.classList.remove('opacity-0', '-translate-y-2'));
    setTimeout(() => { el.classList.add('opacity-0'); setTimeout(() => el.remove(), 300); }, ms);
  }

  // ------------------------------------------------------------------ Confirm modal
  function confirmDialog({ title = 'Are you sure?', body = '', okText = 'Confirm', danger = true } = {}) {
    const modal = document.getElementById('confirm-modal');
    if (!modal) return Promise.resolve(window.confirm(`${title}\n\n${body}`));
    modal.querySelector('#confirm-title').textContent = title;
    modal.querySelector('#confirm-body').textContent = body;
    const ok = modal.querySelector('[data-confirm-ok]');
    const cancel = modal.querySelector('[data-confirm-cancel]');
    ok.textContent = okText;
    ok.className = `rounded-lg px-4 py-2 text-sm font-semibold text-white ${danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-brand-700 hover:bg-brand-800'}`;
    modal.classList.remove('hidden'); modal.classList.add('flex');
    return new Promise(resolve => {
      const close = (val) => {
        modal.classList.add('hidden'); modal.classList.remove('flex');
        ok.removeEventListener('click', onOk); cancel.removeEventListener('click', onCancel);
        modal.removeEventListener('click', onBackdrop);
        resolve(val);
      };
      const onOk = () => close(true);
      const onCancel = () => close(false);
      const onBackdrop = (e) => { if (e.target === modal) close(false); };
      ok.addEventListener('click', onOk); cancel.addEventListener('click', onCancel);
      modal.addEventListener('click', onBackdrop);
    });
  }

  // ------------------------------------------------------------------ Generic modals
  function openModal(id) { const m = document.getElementById(id); if (m) { m.classList.remove('hidden'); m.classList.add('flex'); } }
  function closeModal(id) { const m = document.getElementById(id); if (m) { m.classList.add('hidden'); m.classList.remove('flex'); } }

  // ------------------------------------------------------------------ Utilities
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtTime(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
  }
  const BADGE = {
    present: 'bg-emerald-100 text-emerald-800', late: 'bg-amber-100 text-amber-800', absent: 'bg-rose-100 text-rose-800',
    'Good Standing': 'bg-emerald-100 text-emerald-800', 'Frequent Tardy': 'bg-amber-100 text-amber-800',
    'Critical Absences': 'bg-rose-100 text-rose-800', pending: 'bg-amber-100 text-amber-800',
    active: 'bg-emerald-100 text-emerald-800', suspended: 'bg-rose-100 text-rose-800',
  };
  function badge(text, key) {
    return `<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${BADGE[key || text] || 'bg-slate-100 text-slate-700'}">${esc(text)}</span>`;
  }

  // ------------------------------------------------------------------ Instructor roster actions
  async function unbindDevice(studentId, name, onDone) {
    const ok = await confirmDialog({
      title: 'Unbind device?',
      body: `This resets the phone binding for ${name}. Their next successful scan will bind whichever phone they use. Only do this after verifying their identity (e.g. lost or replaced phone).`,
      okText: 'Unbind device',
    });
    if (!ok) return;
    const r = await api(`/api/students/${studentId}/unbind-device/`, { method: 'POST' });
    toast(r.ok ? r.data.detail : errorText(r.data), r.ok ? 'success' : 'error');
    if (r.ok && onDone) onDone();
  }

  async function dropStudent(subjectId, studentId, name, onDone) {
    const ok = await confirmDialog({
      title: 'Drop student from subject?',
      body: `${name} will be unenrolled from this subject only. Their account and past attendance records are NOT deleted.`,
      okText: 'Drop from subject',
    });
    if (!ok) return;
    const r = await api(`/api/subjects/${subjectId}/students/${studentId}/drop/`, { method: 'POST' });
    toast(r.ok ? r.data.detail : errorText(r.data), r.ok ? 'success' : 'error');
    if (r.ok && onDone) onDone();
  }

  async function openProfile(subjectId, studentId) {
    const modal = document.getElementById('profile-modal');
    if (!modal) return;
    const body = modal.querySelector('[data-profile-body]');
    body.innerHTML = '<div class="py-12 text-center text-sm text-slate-500">Loading…</div>';
    openModal('profile-modal');
    const r = await api(`/api/subjects/${subjectId}/students/${studentId}/profile/`);
    if (!r.ok) { body.innerHTML = `<p class="py-8 text-center text-rose-600">${esc(errorText(r.data))}</p>`; return; }
    const { student: s, subject, summary: m, recent } = r.data;
    const ringColor = m.standing === 'Good Standing' ? '#16a34a' : m.standing === 'Frequent Tardy' ? '#d97706' : '#e11d48';
    const C = 2 * Math.PI * 42, off = C * (1 - Math.min(100, m.attendance_rate) / 100);
    body.innerHTML = `
      <div class="flex items-start justify-between gap-4">
        <div>
          <p class="text-xs font-semibold uppercase tracking-wide text-brand-700">${esc(subject.code)} · ${esc(subject.title)}</p>
          <h3 class="mt-1 text-xl font-bold text-slate-900">${esc(s.name)}</h3>
          <p class="text-sm text-slate-500">${esc(s.id_number || '')}${s.course ? ' · ' + esc(s.course) : ''}${s.year_level ? ' · Year ' + s.year_level : ''}</p>
          <div class="mt-2 flex flex-wrap gap-2">${badge(m.standing)} ${badge(s.account_status, s.account_status)}
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${s.device_bound ? 'bg-sky-100 text-sky-800' : 'bg-slate-100 text-slate-600'}">${s.device_bound ? '📱 Device bound' : 'No device bound'}</span></div>
        </div>
        <div class="relative h-24 w-24 shrink-0">
          <svg viewBox="0 0 100 100" class="h-24 w-24 -rotate-90">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#e2e8f0" stroke-width="10"/>
            <circle cx="50" cy="50" r="42" fill="none" stroke="${ringColor}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}"/>
          </svg>
          <span class="absolute inset-0 flex items-center justify-center text-lg font-bold text-slate-900">${Math.round(m.attendance_rate)}%</span>
        </div>
      </div>
      <div class="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div class="rounded-xl bg-slate-50 p-3 text-center"><p class="text-xs text-slate-500">Attendance rate</p><p class="text-xl font-bold text-slate-900">${m.attendance_rate}%</p></div>
        <div class="rounded-xl bg-emerald-50 p-3 text-center"><p class="text-xs text-emerald-700">On time</p><p class="text-xl font-bold text-emerald-800">${m.present}</p></div>
        <div class="rounded-xl bg-amber-50 p-3 text-center"><p class="text-xs text-amber-700">Late count</p><p class="text-xl font-bold text-amber-800">${m.late}</p></div>
        <div class="rounded-xl bg-rose-50 p-3 text-center"><p class="text-xs text-rose-700">Absences</p><p class="text-xl font-bold text-rose-800">${m.absent}</p></div>
      </div>
      <h4 class="mt-6 text-sm font-semibold text-slate-900">Recent sessions</h4>
      <div class="mt-2 max-h-56 divide-y divide-slate-100 overflow-y-auto rounded-xl ring-1 ring-slate-200">
        ${recent.length ? recent.map(x => `
          <div class="flex items-center justify-between gap-3 px-3 py-2 text-sm">
            <div class="min-w-0"><p class="truncate font-medium text-slate-800">${esc(x.title || fmtDate(x.date))}</p>
            <p class="truncate text-xs text-slate-500">${fmtDate(x.date)}${x.notes ? ' · ' + esc(x.notes) : ''}</p></div>
            ${badge(x.status.charAt(0).toUpperCase() + x.status.slice(1), x.status)}
          </div>`).join('') : '<p class="px-3 py-6 text-center text-sm text-slate-500">No attendance records yet.</p>'}
      </div>
      <p class="mt-4 text-xs text-slate-400">${s.email ? esc(s.email) + ' · ' : ''}Enrolled ${fmtDate(s.enrolled_at)}</p>`;
  }

  // ------------------------------------------------------------------ Declarative behaviours
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-toggle]');
    if (t) { document.querySelector(t.dataset.toggle)?.classList.toggle('hidden'); }
    const d = e.target.closest('[data-dismiss]');
    if (d) { d.closest('[data-dismissible]')?.remove(); }
    const pw = e.target.closest('[data-toggle-password]');
    if (pw) {
      const input = document.querySelector(pw.dataset.togglePassword);
      if (input) { const show = input.type === 'password'; input.type = show ? 'text' : 'password'; pw.textContent = show ? 'Hide' : 'Show'; }
    }
    const open = e.target.closest('[data-open-modal]');
    if (open) openModal(open.dataset.openModal);
    const close = e.target.closest('[data-close-modal]');
    if (close) closeModal(close.dataset.closeModal);
  });

  // Forms with data-confirm="message" ask before submitting.
  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form.dataset.confirm || form.dataset.confirmed) return;
    e.preventDefault();
    const ok = await confirmDialog({
      title: form.dataset.confirmTitle || 'Please confirm',
      body: form.dataset.confirm,
      okText: form.dataset.confirmOk || 'Confirm',
      danger: form.dataset.confirmDanger !== 'false',
    });
    if (ok) { form.dataset.confirmed = '1'; (form.requestSubmit ? form.requestSubmit(e.submitter) : form.submit()); }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.querySelectorAll('[data-modal].flex').forEach(m => { m.classList.add('hidden'); m.classList.remove('flex'); });
  });

  window.SA = { api, errorText, toast, confirmDialog, openModal, closeModal, esc, fmtTime, fmtDate, badge,
                unbindDevice, dropStudent, openProfile, getCookie };
})();
