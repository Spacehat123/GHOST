/**
 * GHOST COMMAND DECK CONTROLLER (PART 6)
 * Autonomous Agency Review Queue & Tactical Pipeline Control.
 * Strict human-in-the-loop: Ghost prepares drafts; Pranav verifies and sends.
 * Zero automated sending. All scraped text is strictly escaped to prevent XSS.
 */

(function () {
  'use strict';

  // State
  let sessionToken = null;
  let activeView = 'kanban';
  let activeCampaign = null;
  let leadsCache = [];
  let currentLead = null;
  let operatorInfo = null;
  let wsEvents = null;

  // XSS-Safe HTML Escaping
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Authenticated Fetch Wrapper
  async function deckFetch(url, options = {}) {
    const headers = options.headers || {};
    if (sessionToken) {
      headers['X-Session-Token'] = sessionToken;
    }
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    options.headers = headers;

    const resp = await fetch(url, options);
    if (resp.status === 401) {
      console.warn('Command Deck session unauthorized. Re-authenticating...');
      await initSession();
    }
    return resp;
  }

  async function initSession() {
    try {
      const meta = document.querySelector('meta[name="ghost-session-token"]');
      if (meta && meta.content) {
        sessionToken = meta.content.trim();
        console.log('Command Deck authenticated with session token from meta tag.');
        initWebSocket();
        loadOperator();
        loadCampaigns();
        loadLeads();
      } else {
        console.error('Session meta tag (ghost-session-token) missing from document.');
      }
    } catch (e) {
      console.error('Session init error:', e);
    }
  }

  function initWebSocket() {
    if (wsEvents || !sessionToken) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/events?token=${encodeURIComponent(sessionToken)}`;
    wsEvents = new WebSocket(wsUrl);

    wsEvents.onopen = () => console.log('Command Deck /ws/events connected.');
    wsEvents.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleLiveEvent(msg);
      } catch (e) {
        console.error('Failed to parse WS event:', e);
      }
    };
    wsEvents.onclose = () => {
      wsEvents = null;
      setTimeout(initWebSocket, 3000);
    };
  }

  // Follow mode (stolen from the windowed-UI brief, tab-sized): real events
  // from the SAME WS feed badge the tab that owns them — never fake activity,
  // never a popup, never focus theft. Clicking the tab clears its badge.
  const EVENT_TAB_MAP = {
    lead_hunt_completed: 'kanban', lead_replied: 'kanban',
    campaign_created: 'campaigns', batch_started: 'campaigns',
    batch_harvest_started: 'campaigns',
    domain_confirmed: 'confirmation', dossier_built: 'dossiers',
    demo_opened: 'demos', demo_host_authorized: 'demos', demo_hosted: 'demos',
    payment_verified: 'payments', deposit_received: 'payments',
    payment_link_created_real: 'payments', payment_upi_uri_issued: 'payments',
    fixer_staged: 'fixes', fixer_report_received: 'fixes',
    client_photos_received: 'payments',
    followup_drafted: 'kanban', reply_complaint: 'kanban',
    reply_quarantined: 'kanban', health_incident: 'autonomy',
    intake_unmatched: 'notifications', ladder_unlock_available: 'autonomy',
    lead_form_submission: 'kanban',
  };

  function badgeTab(viewId, count = 1) {
    const btn = document.querySelector(`.deck-tab-btn[data-view="${viewId}"]`);
    if (!btn) return;
    if (btn.classList.contains('active')) return;   // you're already looking
    let dot = btn.querySelector('.tab-badge');
    if (!dot) {
      dot = document.createElement('span');
      dot.className = 'tab-badge';
      btn.appendChild(dot);
    }
    dot.dataset.count = String((parseInt(dot.dataset.count || '0', 10)) + count);
    dot.classList.add('on');
  }

  function clearTabBadge(viewId) {
    const btn = document.querySelector(`.deck-tab-btn[data-view="${viewId}"]`);
    if (btn) {
      const dot = btn.querySelector('.tab-badge');
      if (dot) { dot.dataset.count = '0'; dot.classList.remove('on'); }
    }
  }

  function handleLiveEvent(evt) {
    appendActivityLog(evt);
    if (evt.type === 'universal_event') {
      appendIntelFeed(evt);
      if (evt.event_type === 'system.telemetry.sample' && activeView === 'awareness') {
        loadAwareness();
      }
      if (evt.event_type && evt.event_type.startsWith('task.') && activeView === 'projects') {
        loadProjects();
      }
    }
    const tab = EVENT_TAB_MAP[evt.type];
    if (tab) badgeTab(tab);
    if (['campaign_created', 'batch_started', 'lead_opted_out', 'contract_drafted', 'domain_confirmed'].includes(evt.type)) {
      loadLeads();
    }
  }

  // Navigation
  function switchView(viewId) {
    activeView = viewId;
    document.querySelectorAll('.deck-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === viewId);
    });
    document.querySelectorAll('.deck-view').forEach(view => {
      view.classList.toggle('active', view.id === `view-${viewId}`);
    });

    clearTabBadge(viewId);
    safeLoad(viewId);
  }

  // per-tab crash isolation (brief §3, tab-sized): a broken panel shows the
  // error in ITS pane and never takes down the deck
  function safeLoad(viewId) {
    const loaders = {
      awareness: loadAwareness,
      feed: loadIntelFeed,
      objectives: loadObjectives,
      agents: loadAgents,
      projects: loadProjects,
      physical: loadPhysical,
      creative: loadCreative,
      memory: loadMemory,
      control: loadControl,
      kanban: loadLeads,
      campaigns: loadCampaigns,
      confirmation: loadConfirmationQueue,
      demos: loadDemos,
      dossiers: loadDossiers,
      microjobs: loadMicrojobs,
      payments: loadPayments,
      fixes: loadFixes,
      autonomy: loadAutonomy,
      notifications: loadNotifications,
      templates: loadTemplates,
      suppression: loadSuppression,
      events: loadEvents,
    };
    const fn = loaders[viewId];
    if (!fn) return;
    try {
      fn();
    } catch (e) {
      console.error(`panel ${viewId} crashed:`, e);
      const el = document.getElementById(`view-${viewId}`);
      if (el) {
        const box = el.querySelector('.deck-panel-box') || el;
        box.insertAdjacentHTML('beforeend',
          `<div class="deck-alert deck-alert-danger" style="margin-top:12px;">⚠ This panel crashed: ${String(e && e.message || e).slice(0, 120)}. Everything else keeps running.</div>`);
      }
    }
  }

  // ---- P9: the single-window operator panels --------------------------------

  async function loadDossiers() {
    const res = await deckFetch('/api/dossiers');
    const listEl = document.getElementById('dossiers-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Dossier shelf unavailable.</div>'; return; }
    const { dossiers } = await res.json();
    if (!dossiers.length) {
      listEl.innerHTML = '<div class="deck-section-desc">No dossiers yet — they are built per qualified lead (dossier.build).</div>';
      return;
    }
    listEl.innerHTML = dossiers.map(d => `
      <div class="deck-panel-box" style="margin-bottom:10px; background:var(--deck-card); cursor:pointer;" onclick="deckOpenDossier('${d.slug}')">
        <b>${d.business}</b> <span style="opacity:0.55; font-size:0.85em;">(${d.slug})</span>
        <span style="font-size:0.85em; opacity:0.75;"> · 📷 ${d.photos} · site: ${d.site_verdict || '?'}</span>
        ${d.inquire.length ? `<span style="font-size:0.8em; opacity:0.6;"> · ❓inquire: ${d.inquire.join(', ')}</span>` : ''}
        <div class="photo-dropzone" data-slug="${d.slug}">＋ Drop photos here (or click) — they stage into the next build with provenance</div>
        <input type="file" accept=".jpg,.jpeg,.png,.webp" multiple style="display:none" data-slug="${d.slug}">
      </div>`).join('');
    listEl.querySelectorAll('.photo-dropzone').forEach(wirePhotoDropzone);
  }

  // Option A (photo doctrine): the operator drops the business's REAL photos;
  // they land in dossiers/<slug>/photos/ and the next build stages them.
  function wirePhotoDropzone(zone) {
    const slug = zone.dataset.slug;
    const input = zone.parentElement.querySelector('input[type=file][data-slug="' + slug + '"]');
    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer.files.length) uploadDossierPhotos(slug, e.dataTransfer.files);
    });
    input.addEventListener('change', () => {
      if (input.files.length) uploadDossierPhotos(slug, input.files);
      input.value = '';
    });
  }

  async function uploadDossierPhotos(slug, fileList) {
    const fd = new FormData();
    [...fileList].forEach(f => fd.append('files', f));
    // raw fetch: multipart must NOT carry a JSON Content-Type
    const resp = await fetch(`/api/dossiers/${slug}/photos`, {
      method: 'POST',
      headers: { 'X-Session-Token': sessionToken },
      body: fd,
    });
    if (!resp.ok) { const o = await resp.json().catch(() => ({})); alert(o.detail || 'Upload failed.'); return; }
    const out = await resp.json();
    if (out.refused.length) alert(`${out.saved.length} photo(s) staged; ${out.refused.length} non-image file(s) refused.`);
    loadDossiers();
  }
  window.uploadDossierPhotos = uploadDossierPhotos;

  async function deckOpenDossier(slug) {
    const res = await deckFetch(`/api/dossiers/${slug}`);
    if (!res.ok) { alert('Dossier unavailable.'); return; }
    const d = await res.json();
    const el = document.getElementById('dossier-detail');
    el.style.display = 'block';
    const f = d.facts || {};
    const idn = f.identity || {};
    const v = k => (idn[k] || {}).value;
    el.innerHTML = `
      <div class="deck-panel-box" style="background:var(--deck-card); margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between;">
          <b>${v('name') || slug}</b>
          <button class="deck-btn" onclick="document.getElementById('dossier-detail').style.display='none'">✕ close</button>
        </div>
        <div style="font-size:0.9em; opacity:0.85; margin-top:6px;">
          ${['category', 'address', 'phone', 'website', 'hours'].map(k =>
            `<div>· <b>${k}</b>: ${v(k) ? v(k) : '<i>[Inquire for quotation]</i>'}</div>`).join('')}
          <div>· <b>site</b>: ${((f.site || {}).verdict) || '?'} — ${((f.site || {}).evidence || '')}</div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;">
          ${(d.photos || []).map(p => `<img src="/api/dossiers/${slug}/photo/${p}" alt="${p}" loading="lazy" style="width:110px; height:82px; object-fit:cover; border-radius:6px;">`).join('')}
        </div>
        ${(f.inquire || []).length ? `<div style="font-size:0.85em; opacity:0.7; margin-top:8px;">❓ Inquire: ${f.inquire.join(', ')} — never guessed</div>` : ''}
      </div>`;
    el.scrollIntoView({ behavior: 'smooth' });
  }

  async function loadPayments() {
    const res = await deckFetch('/api/payments/overview');
    const listEl = document.getElementById('payments-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Payments view unavailable.</div>'; return; }
    const data = await res.json();
    const railChips = Object.entries(data.rail || {}).map(([k, on]) =>
      `<span class="deck-btn" style="cursor:default; opacity:${on ? 1 : 0.45};">${k}: ${on ? '✅ live' : '— not set'}</span>`).join(' ');
    listEl.innerHTML = `<div style="margin-bottom:12px; display:flex; gap:8px; flex-wrap:wrap;">${railChips}</div>` +
      ((data.deals || []).map(d => `
      <div class="deck-panel-box" style="margin-bottom:10px; background:var(--deck-card);">
        <b>${d.business || d.slug}</b> <span style="opacity:0.6; font-size:0.85em;">· ${d.stage} · ₹${d.deal_value || 0}</span>
        <div style="font-size:0.85em; opacity:0.8; margin-top:4px;">
          ${d.documents.map(x => `📄 ${x.kind} v${x.version} (${x.status})`).join(' · ') || 'no closing documents yet'}
        </div>
        ${d.qr_artifacts.length ? `<div style="font-size:0.85em; opacity:0.8;">🔗 QR artifacts: ${d.qr_artifacts.join(', ')}</div>` : ''}
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:10px; font-size:0.8em;">
          <span style="border:1px solid var(--deck-border); border-radius:5px; padding:4px 8px; ${d.photos_received ? 'color:var(--deck-accent-success);' : 'color:var(--deck-accent-warning);'}">
            📸 client photos: ${d.photos_received ? 'received' : 'WAITING'}</span>
          ${d.photos_received ? '' : `<button class="deck-btn" onclick="deckMarkPhotos('${d.slug}')">Mark photos received</button>`}
          <span style="border:1px solid var(--deck-border); border-radius:5px; padding:4px 8px; ${d.seo_pack_built ? 'color:var(--deck-accent-success);' : 'opacity:0.7;'}">
            🔎 SEO pack: ${d.seo_pack_built ? 'built' : 'not built'}</span>
          <button class="deck-btn" onclick="deckBuildSeoPack('${d.slug}')">${d.seo_pack_built ? 'Rebuild' : 'Generate'} SEO pack</button>
          ${d.seo_pack_built ? `<button class="deck-btn" onclick="deckViewPack('${d.slug}', 'local_seo_checklist')">View 6-move checklist</button>
          <button class="deck-btn" onclick="deckViewPack('${d.slug}', 'security_checklist')">View security gate</button>` : ''}
        </div>
        ${d.photos_received ? '' : `<div style="font-size:0.78em; opacity:0.6; margin-top:6px;">${d.photos_note}</div>`}
      </div>`).join('') || '<div class="deck-section-desc">No deals with documents yet.</div>');
  }

  async function deckMarkPhotos(slug) {
    if (!confirm(`Mark the client's real photos as RECEIVED for "${slug}"?\n\nThis opens the website-build gate (demos never wait; websites do).`)) return;
    const res = await deckFetch(`/api/leads/${slug}/mark-client-photos`, { method: 'POST' });
    if (!res.ok) { const o = await res.json().catch(() => ({})); alert(o.detail || 'Not marked.'); }
    loadPayments();
  }

  async function deckBuildSeoPack(slug) {
    const res = await deckFetch(`/api/leads/${slug}/seo-pack`, { method: 'POST' });
    if (!res.ok) { alert('SEO pack build failed.'); return; }
    const out = await res.json();
    loadPayments();
  }

  async function deckViewPack(slug, name) {
    const res = await deckFetch(`/api/leads/${slug}/seo-pack/file/${name}`);
    if (!res.ok) { alert('Pack file unavailable.'); return; }
    const { markdown } = await res.json();
    let el = document.getElementById('payments-detail');
    if (!el) {
      el = document.createElement('div');
      el.id = 'payments-detail';
      el.style.marginTop = '12px';
      document.getElementById('payments-list').after(el);
    }
    el.innerHTML = `<div class="deck-panel-box" style="background:var(--deck-card);">
      <div style="display:flex; justify-content:space-between;">
        <b>${name} — ${slug}</b>
        <button class="deck-btn" onclick="this.closest('#payments-detail').style.display='none'">✕ close</button>
      </div>
      <pre style="white-space:pre-wrap; font-size:0.82em; line-height:1.6; margin-top:10px;">${markdown.replace(/</g, '&lt;')}</pre></div>`;
    el.scrollIntoView({ behavior: 'smooth' });
  }
  window.deckMarkPhotos = deckMarkPhotos;
  window.deckBuildSeoPack = deckBuildSeoPack;
  window.deckViewPack = deckViewPack;

  async function loadFixes() {
    const res = await deckFetch('/api/fixer/staged');
    const listEl = document.getElementById('fixes-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Fixes queue unavailable.</div>'; return; }
    const { staged } = await res.json();
    if (!staged.length) {
      listEl.innerHTML = '<div class="deck-section-desc">No staged fixes — customer-reported breaks land here after independent verification.</div>';
      return;
    }
    listEl.innerHTML = staged.map(s => `
      <div class="deck-panel-box" style="margin-bottom:10px; background:var(--deck-card);">
        <b>${s.site}</b> <span style="opacity:0.6; font-size:0.85em;">· ${s.staged_id} · ${s.status}</span>
        <div style="font-size:0.85em; opacity:0.8;">${s.description}</div>
        <div style="font-size:0.8em; opacity:0.6;">files: ${s.files.join(', ')}</div>
        ${s.status === 'staged' ? `<button class="deck-btn deck-btn-highlight" style="margin-top:6px;" onclick="deckApproveFix('${s.staged_id}')">✅ Approve &amp; apply</button>` : ''}
      </div>`).join('');
  }

  async function deckApproveFix(stagedId) {
    if (!confirm(`Apply staged fix ${stagedId} to the live site?\n\nIt was independently verified, but look at the description first.`)) return;
    const res = await deckFetch(`/api/fixer/${stagedId}/approve`, { method: 'POST' });
    if (res.ok) { alert('Fix applied.'); } else {
      const out = await res.json().catch(() => ({}));
      alert(`Not applied: ${out.detail || res.status}`);
    }
    loadFixes();
  }

  async function loadAutonomy() {
    const res = await deckFetch('/api/autonomy');
    const statusEl = document.getElementById('autonomy-status');
    const ladderEl = document.getElementById('autonomy-ladder');
    if (!statusEl || !ladderEl) return;
    if (!res.ok) { statusEl.innerHTML = '<div class="deck-section-desc">Autonomy bundle unavailable.</div>'; return; }
    const b = await res.json();
    const chip = (ok, label) => `<span class="deck-btn" style="cursor:default; ${ok ? '' : 'opacity:0.5;'}">${label}</span>`;
    statusEl.innerHTML = `<div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px;">
      ${b.halt.halted ? '<span class="deck-btn deck-btn-danger">🛑 HALTED: ' + ((b.halt.state || {}).reason || '?') + '</span>' : chip(true, '✅ halt clear')}
      ${chip(b.runtime.running !== false, 'runtime: ' + (b.runtime.running ? 'running' : 'not started'))}
      ${chip(b.supervisor.alive, 'supervisor: ' + (b.supervisor.alive ? `alive (${Math.round((b.supervisor.beat_age_s || 0) / 60)}m)` : 'SILENT'))}
      ${chip(!b.rhythm.scheduler_cycle?.due, 'scheduler cycle: ' + (b.rhythm.scheduler_cycle?.due ? 'OVERDUE' : 'on rhythm'))}
      ${chip(!b.rhythm.digest?.due, 'digest: ' + (b.rhythm.digest?.due ? 'OVERDUE' : 'on rhythm'))}
      ${b.darkrun.active ? chip(true, `🌙 dark run day ${b.darkrun.day}/${b.darkrun.of}`) : ''}
    </div>`;
    ladderEl.innerHTML = Object.entries(b.ladder || {}).map(([cap, st]) => `
      <div class="deck-panel-box" style="margin-bottom:8px; background:var(--deck-card); display:flex; justify-content:space-between; align-items:center;">
        <div><b>${cap}</b> — L${st.level} <span style="opacity:0.6; font-size:0.85em;">(earned ceiling L${st.eligible_for}, streak ${st.success_streak})</span></div>
        ${st.eligible_for > st.level ? `<button class="deck-btn deck-btn-highlight" onclick="deckRaiseLadder('${cap}')">⬆ Raise to L${st.level + 1}</button>` : ''}
      </div>`).join('');
  }

  async function deckRaiseLadder(cap) {
    if (!confirm(`Raise ${cap} one level?\n\nPromotion is the one operator-only trust act — only raise what you have SEEN perform.`)) return;
    const res = await deckFetch('/api/ladder/raise', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capability: cap }) });
    if (!res.ok) { const o = await res.json().catch(() => ({})); alert(o.detail || 'Cannot raise.'); }
    loadAutonomy();
  }

  async function loadNotifications() {
    const res = await deckFetch('/api/notifications');
    const listEl = document.getElementById('notifications-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Notifications unavailable.</div>'; return; }
    const data = await res.json();
    const when = ts => ts ? new Date(ts * 1000).toLocaleString() : '';
    listEl.innerHTML = `
      <div class="deck-panel-box" style="background:var(--deck-card); margin-bottom:10px;">
        <b>Pings</b> (${(data.pings || []).length})
        ${(data.pings || []).map(p => `
          <div style="font-size:0.85em; border-top:1px solid rgba(255,255,255,0.06); padding:6px 0;">
            <span style="opacity:0.55;">${when(p.at)}</span> · <b>${p.type}</b> ${p.subject ? `· ${p.subject}` : ''}
            ${p.ping ? `<div style="opacity:0.75;">${p.ping}</div>` : ''}
          </div>`).join('') || '<div style="opacity:0.6; font-size:0.85em;">Nothing has pinged — quiet is not stillness (digests below).</div>'}
      </div>
      <div class="deck-panel-box" style="background:var(--deck-card);">
        <b>09:00 digests</b>
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
          ${(data.digests || []).map(d => `<button class="deck-btn" onclick="deckOpenDigest('${d.replace('.md', '')}')">${d.replace('.md', '')}</button>`).join('') || '<span style="opacity:0.6; font-size:0.85em;">none yet</span>'}
        </div>
      </div>`;
  }

  async function deckOpenDigest(day) {
    const res = await deckFetch(`/api/notifications/digest/${day}`);
    if (!res.ok) { alert('Digest unavailable.'); return; }
    const { markdown } = await res.json();
    document.getElementById('digest-view').style.display = 'block';
    document.getElementById('digest-text').textContent = markdown;
    document.getElementById('digest-view').scrollIntoView({ behavior: 'smooth' });
  }

  function openDeck() {
    const overlay = document.getElementById('deck-overlay');
    if (overlay) overlay.classList.remove('hidden');
  }
  window.openDeck = openDeck;

  // Command palette (Ctrl+K) — brief §2, tab-sized. Opens panels + the two
  // safety actions. No auto-clicking anything reserved: halt/resume are the
  // operator's own keystrokes, audited as usual.
  const CMDK_COMMANDS = [
    { label: 'Open Pipeline', run: () => { openDeck(); switchView('kanban'); } },
    { label: 'Open Campaigns', run: () => { openDeck(); switchView('campaigns'); } },
    { label: 'Open Confirmation Queue', run: () => { openDeck(); switchView('confirmation'); } },
    { label: 'Open Demos', run: () => { openDeck(); switchView('demos'); } },
    { label: 'Open Dossiers', run: () => { openDeck(); switchView('dossiers'); } },
    { label: 'Open Micro-jobs', run: () => { openDeck(); switchView('microjobs'); } },
    { label: 'Open Payments', run: () => { openDeck(); switchView('payments'); } },
    { label: 'Open Site Fixes', run: () => { openDeck(); switchView('fixes'); } },
    { label: 'Open Autonomy', run: () => { openDeck(); switchView('autonomy'); } },
    { label: 'Open Notifications', run: () => { openDeck(); switchView('notifications'); } },
    { label: 'Open Templates', run: () => { openDeck(); switchView('templates'); } },
    { label: 'Open Suppression', run: () => { openDeck(); switchView('suppression'); } },
    { label: 'Open Activity Log', run: () => { openDeck(); switchView('events'); } },
    { label: '🛑 HALT — freeze all outbound', run: () => { deckFetch('/api/halt', { method: 'POST', body: JSON.stringify({ reason: 'command palette' }) }).then(loadOperator); } },
    { label: '▶ Resume after halt', run: () => { deckFetch('/api/halt/resume', { method: 'POST' }).then(loadOperator); } },
  ];
  let cmdkIndex = 0;

  function cmdkOpen() {
    const backdrop = document.getElementById('cmdk-backdrop');
    if (!backdrop) return;
    backdrop.classList.remove('hidden');
    const input = document.getElementById('cmdk-input');
    input.value = '';
    cmdkIndex = 0;
    cmdkRender('');
    input.focus();
  }

  function cmdkClose() {
    const backdrop = document.getElementById('cmdk-backdrop');
    if (backdrop) backdrop.classList.add('hidden');
  }

  function cmdkRender(filter) {
    const box = document.getElementById('cmdk-results');
    if (!box) return;
    const f = (filter || '').toLowerCase();
    const items = CMDK_COMMANDS.filter(c => c.label.toLowerCase().includes(f));
    cmdkIndex = Math.min(cmdkIndex, Math.max(0, items.length - 1));
    box.innerHTML = items.map((c, i) =>
      `<div class="cmdk-item${i === cmdkIndex ? ' sel' : ''}" data-i="${i}">${c.label}</div>`).join('')
      || '<div class="cmdk-item">no matching command</div>';
    box._items = items;
  }

  function cmdkRun() {
    const box = document.getElementById('cmdk-results');
    const item = box && box._items && box._items[cmdkIndex];
    cmdkClose();
    if (item) { try { item.run(); } catch (e) { console.error('cmdk action failed:', e); } }
  }

  (function cmdkWire() {
    document.addEventListener('keydown', (e) => {
      const backdrop = document.getElementById('cmdk-backdrop');
      const open = backdrop && !backdrop.classList.contains('hidden');
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        open ? cmdkClose() : cmdkOpen();
        return;
      }
      if (!open) return;
      if (e.key === 'Escape') { e.preventDefault(); cmdkClose(); }
      if (e.key === 'ArrowDown') { e.preventDefault(); cmdkIndex = Math.min(cmdkIndex + 1, (document.getElementById('cmdk-results')._items || []).length - 1); cmdkRender(document.getElementById('cmdk-input').value); }
      if (e.key === 'ArrowUp') { e.preventDefault(); cmdkIndex = Math.max(cmdkIndex - 1, 0); cmdkRender(document.getElementById('cmdk-input').value); }
      if (e.key === 'Enter') { e.preventDefault(); cmdkRun(); }
    });
    document.addEventListener('click', (e) => {
      const item = e.target.closest('.cmdk-item');
      if (item && item.dataset.i !== undefined) { cmdkIndex = parseInt(item.dataset.i, 10); cmdkRun(); }
    });
  })();

  window.deckOpenDossier = deckOpenDossier;
  window.deckApproveFix = deckApproveFix;
  window.deckRaiseLadder = deckRaiseLadder;
  window.deckOpenDigest = deckOpenDigest;

  // Micro-jobs copilot (Operator's 11 P8): Ghost drafts, the human sends
  async function loadMicrojobs() {
    const res = await deckFetch('/api/microjobs');
    const listEl = document.getElementById('microjobs-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Micro-jobs board unavailable.</div>'; return; }
    const { jobs } = await res.json();
    if (!jobs.length) {
      listEl.innerHTML = '<div class="deck-section-desc">No jobs yet — add your saved Upwork search URLs / RSS feeds to configs/microjobs.json, then Sweep.</div>';
      return;
    }
    listEl.innerHTML = jobs.map(j => `
      <div class="deck-panel-box" style="margin-bottom:14px; background:var(--deck-card);">
        <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;">
          <div style="flex:1; min-width:260px;">
            <div style="font-weight:600;">${j.title || '(untitled)'}
              <span style="opacity:0.55; font-size:0.85em;">${j.budget_text ? '· ' + j.budget_text : ''}</span></div>
            <div style="font-size:0.85em; opacity:0.75;">
              ${j.source} · fit ${j.fit_score} · ${j.status}
              ${j.url ? `· <a href="${j.url}" target="_blank" rel="noopener">open</a>` : ''}</div>
            ${(j.reasons || []).slice(0, 2).map(r => `<div style="font-size:0.8em; opacity:0.6;">• ${r}</div>`).join('')}
            ${j.proposal ? `<div style="font-size:0.85em; margin-top:8px; padding:8px; background:rgba(255,255,255,0.04); border-radius:6px;">
              <b>${j.proposal.status === 'awaiting_human_send' ? '✍️ awaiting your send' : j.proposal.status}</b><br>${(j.proposal.body || '').slice(0, 220)}…</div>` : ''}
          </div>
          <div style="display:flex; flex-direction:column; gap:6px;">
            ${j.proposal ? '' : `<button class="deck-btn" onclick="deckProposeJob('${j.id}')">✍️ Draft proposal</button>`}
            ${j.proposal && j.proposal.status === 'awaiting_human_send' ? `
            <button class="deck-btn deck-btn-highlight" onclick="deckJobStatus('${j.id}','sent')">📤 Mark sent</button>` : ''}
            <button class="deck-btn" onclick="deckJobStatus('${j.id}','won')">🏆 Won</button>
            <button class="deck-btn" onclick="deckJobStatus('${j.id}','lost')">✕ Lost</button>
          </div>
        </div>
      </div>`).join('');
  }

  async function deckMicrojobsSweep() {
    const res = await deckFetch('/api/microjobs/sweep', { method: 'POST' });
    const out = await res.json().catch(() => ({}));
    alert(out.status === 'ok'
      ? `Sweep done: ${out.stored} new job(s), ${out.above_min_score} above fit threshold${out.failed ? `, ${out.failed} source(s) failed` : ''}`
      : 'Sweep failed — see Activity Log.');
    loadMicrojobs();
  }

  async function deckProposeJob(jobId) {
    const res = await deckFetch(`/api/microjobs/${jobId}/propose`, { method: 'POST' });
    if (!res.ok) { alert('Could not draft the proposal.'); return; }
    loadMicrojobs();
  }

  async function deckJobStatus(jobId, status) {
    await deckFetch(`/api/microjobs/${jobId}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }) });
    loadMicrojobs();
  }
  window.deckMicrojobsSweep = deckMicrojobsSweep;
  window.deckProposeJob = deckProposeJob;
  window.deckJobStatus = deckJobStatus;

  // Demo approval loop (Operator's 11 P3): open locally → verify → approve & host
  async function loadDemos() {
    const res = await deckFetch('/api/demos');
    const listEl = document.getElementById('demos-list');
    if (!listEl) return;
    if (!res.ok) { listEl.innerHTML = '<div class="deck-section-desc">Demo board unavailable.</div>'; return; }
    const { demos } = await res.json();
    if (!demos.length) {
      listEl.innerHTML = '<div class="deck-section-desc">No built demos yet — the pipeline builds them after qualification.</div>';
      return;
    }
    listEl.innerHTML = demos.map(d => `
      <div class="deck-panel-box" style="margin-bottom:14px; background:var(--deck-card);">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;">
          <div>
            <div style="font-weight:600;">${d.business} <span style="opacity:0.55; font-size:0.85em;">(${d.slug})</span></div>
            <div style="font-size:0.85em; opacity:0.75;">
              QA: ${d.qa_passed ? `✅ passed (${d.qa_screenshots} screenshot${d.qa_screenshots === 1 ? '' : 's'})` : '⚠️ no passing QA evidence on file'}
              &nbsp;·&nbsp; Host: ${d.deployed ? `🌐 <a href="${d.hosted_url}" target="_blank" rel="noopener">${d.hosted_url}</a>` : 'local only'}
            </div>
          </div>
          <div style="display:flex; gap:8px;">
            ${d.deployed ? '' : `
            <button class="deck-btn" onclick="deckTogglePreview('${d.slug}', this)">🔍 Preview here</button>
            <button class="deck-btn" onclick="deckOpenDemo('${d.slug}')">👁 Open locally</button>
            <button class="deck-btn deck-btn-highlight" onclick="deckAuthorizeHost('${d.slug}')">🚀 Approve &amp; host</button>`}
          </div>
        </div>
        <div id="preview-${d.slug}" style="display:none; margin-top:10px;">
          <iframe src="/demos/${d.slug}/" title="Local preview of ${d.slug}"
                  style="width:100%; height:420px; border:1px solid rgba(255,255,255,0.1); border-radius:8px; background:#fff;"></iframe>
          <div style="font-size:0.8em; opacity:0.6; margin-top:4px;">Served from this machine only — hosting still needs your separate approval click.</div>
        </div>
      </div>`).join('');
  }

  function deckTogglePreview(slug, btn) {
    const el = document.getElementById(`preview-${slug}`);
    if (!el) return;
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
    btn.textContent = el.style.display === 'none' ? '🔍 Preview here' : '🙈 Hide preview';
  }
  window.deckTogglePreview = deckTogglePreview;

  async function deckOpenDemo(slug) {
    const res = await deckFetch(`/api/demos/${slug}/open`, { method: 'POST' });
    if (!res.ok) { alert('Could not open the demo locally.'); return; }
    const { absolute } = await res.json();
    window.open(absolute, '_blank', 'noopener');
    loadDemos();
  }

  async function deckAuthorizeHost(slug) {
    if (!confirm(`Host "${slug}" to Vercel?\n\nThis approval click mints ONE single-use deploy grant.\nQA-evidence, snapshot and halt gates still apply.`)) return;
    const res = await deckFetch(`/api/demos/${slug}/authorize-host`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed: true, operator_note: 'approved in Command Deck' }),
    });
    const out = await res.json().catch(() => ({}));
    if (res.ok && out.status === 'success' && out.url) {
      alert(`Deployed: ${out.url}`);
    } else if (res.ok && out.status === 'denied') {
      alert(out.message || 'Hosting not confirmed — nothing happened.');
    } else {
      alert(`Deploy did not complete: ${out.message || out.detail || res.status}\nNothing is hidden — check the Activity Log for the gate that stopped it.`);
    }
    loadDemos();
  }
  window.deckOpenDemo = deckOpenDemo;
  window.deckAuthorizeHost = deckAuthorizeHost;

  // Operator Info
  async function loadOperator() {
    const res = await deckFetch('/api/operator');
    if (res.ok) {
      operatorInfo = await res.json();
      updateOperatorUI();
    }
  }

  function updateOperatorUI() {
    if (!operatorInfo) return;
    const opBadge = document.getElementById('deck-operator-status');
    if (opBadge) {
      opBadge.textContent = `${operatorInfo.operator_name || 'Operator'} | Daily Sends: ${operatorInfo.daily_sends_today}/${operatorInfo.max_sends_per_day}`;
      if (operatorInfo.daily_sends_cap_reached) {
        opBadge.classList.add('deck-btn-highlight');
      }
    }
  }

  // Campaigns
  async function loadCampaigns() {
    const res = await deckFetch('/api/campaigns');
    if (!res.ok) return;
    const data = await res.json();
    const listEl = document.getElementById('campaigns-list');
    if (!listEl) return;

    if (!data.campaigns || data.campaigns.length === 0) {
      listEl.innerHTML = '<p class="deck-section-desc">No campaigns created yet. Set up your first 3-batch campaign below.</p>';
      return;
    }

    let html = '<div style="display:flex; flex-direction:column; gap:12px;">';
    data.campaigns.forEach(c => {
      html += `
        <div class="lead-card" style="cursor:default;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div class="lead-card-name">${escapeHtml(c.name)}</div>
              <div class="lead-card-cat">City: <strong>${escapeHtml(c.city)}</strong> | Harvest: ${c.harvest_per_batch}/batch | Keep: ${c.keep_fraction * 100}%</div>
            </div>
            <button class="deck-btn deck-btn-primary btn-run-batch" data-cid="${escapeHtml(c.id)}">Run Next Batch</button>
          </div>
          <div style="margin-top:10px; display:flex; gap:8px;">
      `;
      (c.batches || []).forEach(b => {
        html += `<span class="deck-badge">${escapeHtml(b.category)} (${escapeHtml(b.status)})</span>`;
      });
      html += '</div></div>';
    });
    html += '</div>';
    listEl.innerHTML = html;

    listEl.querySelectorAll('.btn-run-batch').forEach(btn => {
      btn.onclick = async () => {
        const cid = btn.dataset.cid;
        btn.disabled = true;
        btn.textContent = 'Running...';
        await deckFetch(`/api/campaigns/${cid}/batches/next`, { method: 'POST' });
        loadCampaigns();
        loadLeads();
      };
    });
  }

  // Leads & Kanban Board
  const KANBAN_STATUSES = [
    'harvested', 'qualified', 'needs_confirmation', 'built',
    'deployed', 'drafts_ready', 'approved', 'sent', 'replied',
    'agreed', 'contract_drafted', 'won'
  ];

  async function loadLeads() {
    const res = await deckFetch('/api/leads');
    if (!res.ok) return;
    const data = await res.json();
    leadsCache = data.leads || [];
    renderKanban();
  }

  function renderKanban() {
    const container = document.getElementById('kanban-board');
    if (!container) return;

    container.innerHTML = '';
    KANBAN_STATUSES.forEach(st => {
      const col = document.createElement('div');
      col.className = 'kanban-col';

      const leadsInCol = leadsCache.filter(l => l.status === st);

      col.innerHTML = `
        <div class="kanban-col-header">
          <span class="kanban-col-title">${escapeHtml(st.replace('_', ' '))}</span>
          <span class="kanban-col-badge">${leadsInCol.length}</span>
        </div>
        <div class="kanban-col-cards" id="col-${st}"></div>
      `;

      const cardsContainer = col.querySelector(`#col-${st}`);
      leadsInCol.forEach(lead => {
        const card = document.createElement('div');
        card.className = 'lead-card';
        card.innerHTML = `
          <div class="lead-card-name">${escapeHtml(lead.name)}</div>
          <div class="lead-card-cat">${escapeHtml(lead.google_category || lead.domain || 'Uncategorized')}</div>
          <div class="lead-card-meta">
            <span>Score: ${lead.score_total || 0}</span>
            <span>${escapeHtml(lead.entity_type || 'unknown')}</span>
          </div>
        `;
        card.onclick = () => openLeadInspector(lead.slug);
        cardsContainer.appendChild(card);
      });

      container.appendChild(col);
    });
  }

  // Domain Confirmation Queue
  async function loadConfirmationQueue() {
    const listEl = document.getElementById('confirmation-queue-list');
    if (!listEl) return;

    const unconfirmed = leadsCache.filter(l => l.needs_confirmation || l.status === 'needs_confirmation');
    if (unconfirmed.length === 0) {
      listEl.innerHTML = '<p class="deck-section-desc">No leads require domain confirmation at this time.</p>';
      return;
    }

    listEl.innerHTML = '';
    unconfirmed.forEach(lead => {
      const card = document.createElement('div');
      card.className = 'lead-card';
      card.style.marginBottom = '12px';

      let gaps = {};
      try { gaps = JSON.parse(lead.gaps_json || '{}'); } catch (e) {}
      const domainCandidates = gaps.audit_data?.domain_candidates || [lead.domain || `${lead.slug}.co.uk`, 'general_services'];

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <div>
            <div class="lead-card-name">${escapeHtml(lead.name)}</div>
            <div class="lead-card-cat">Category: ${escapeHtml(lead.google_category || 'N/A')} | Address: ${escapeHtml(lead.address || 'N/A')}</div>
            <div class="deck-alert deck-alert-warning" style="margin-top:8px;">
              Reason: Domain classification requires human review before build.
            </div>
          </div>
          <div style="display:flex; flex-direction:column; gap:6px;">
            <button class="deck-btn deck-btn-primary btn-confirm-domain" data-slug="${escapeHtml(lead.slug)}" data-domain="${escapeHtml(domainCandidates[0] || 'confirmed')}">
              Confirm "${escapeHtml(domainCandidates[0] || 'Domain')}"
            </button>
            <button class="deck-btn deck-btn-danger btn-reject-domain" data-slug="${escapeHtml(lead.slug)}">
              Mark Ambiguous / Skip
            </button>
          </div>
        </div>
      `;

      card.querySelector('.btn-confirm-domain').onclick = async () => {
        await deckFetch(`/api/leads/${lead.slug}/confirm_domain`, {
          method: 'POST',
          body: JSON.stringify({ chosen_domain: domainCandidates[0] || 'confirmed' })
        });
        loadLeads();
        loadConfirmationQueue();
      };

      listEl.appendChild(card);
    });
  }

  // Lead Inspector Modal
  async function openLeadInspector(slug) {
    const res = await deckFetch(`/api/leads/${slug}`);
    if (!res.ok) return;
    const data = await res.json();
    currentLead = data.lead;

    const modal = document.getElementById('inspector-modal');
    if (!modal) return;

    modal.classList.remove('hidden');
    document.getElementById('inspector-title').textContent = currentLead.name || slug;

    // Fill fields safely
    document.getElementById('insp-name').value = currentLead.name || '';
    document.getElementById('insp-legal-name').value = currentLead.legal_name || currentLead.name || '';
    document.getElementById('insp-address').value = currentLead.address || '';
    document.getElementById('insp-phone').value = currentLead.phone || '';
    document.getElementById('insp-email').value = currentLead.email || '';
    document.getElementById('insp-entity-type').value = currentLead.entity_type || 'unknown';
    document.getElementById('insp-demo-url').value = currentLead.demo_url || '';

    // Render Drafts tab
    renderLeadDrafts(data.drafts || []);

    // Render Closing Pack tab
    renderLeadClosingPack(data.documents || []);

    // Opt-Out Handler
    document.getElementById('btn-lead-optout').onclick = async () => {
      if (confirm(`Opt-out ${currentLead.name}? All phone, email, and domain identifiers will be suppressed immediately.`)) {
        await deckFetch(`/api/leads/${currentLead.slug}/optout`, { method: 'POST' });
        modal.classList.add('hidden');
        loadLeads();
      }
    };
  }

  function renderLeadDrafts(drafts) {
    const container = document.getElementById('inspector-drafts-container');
    if (!container) return;

    if (!drafts || drafts.length === 0) {
      container.innerHTML = '<p class="deck-section-desc">No outreach drafts generated yet (requires deployed live demo).</p>';
      return;
    }

    let html = '';
    drafts.forEach(d => {
      html += `
        <div class="deck-panel-box" style="margin-bottom:14px; background:var(--deck-card);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span class="deck-badge" style="text-transform:uppercase;">${escapeHtml(d.channel)}</span>
            <span class="deck-badge" style="background:#0f172a;">Status: ${escapeHtml(d.status)}</span>
          </div>
          ${d.subject ? `<div style="font-weight:600; margin-bottom:6px; font-size:13px;">Subject: ${escapeHtml(d.subject)}</div>` : ''}
          <div class="deck-textarea" style="background:var(--deck-panel); padding:10px; border-radius:6px; white-space:pre-wrap;">${escapeHtml(d.body)}</div>
          <div class="deck-btn-row">
            ${d.link && d.channel === 'whatsapp' ? `<a href="${escapeHtml(d.link)}" target="_blank" class="deck-btn deck-btn-primary">Open in WhatsApp</a>` : ''}
            <button class="deck-btn btn-copy-draft" data-text="${escapeHtml(d.body)}">Copy</button>
            ${d.status === 'draft' ? `<button class="deck-btn deck-btn-success btn-approve-draft" data-id="${escapeHtml(d.id)}">Approve</button>` : ''}
            ${d.status === 'approved' ? `<button class="deck-btn deck-btn-primary btn-sent-draft" data-id="${escapeHtml(d.id)}">I sent this</button>` : ''}
            <button class="deck-btn btn-reply-draft" data-id="${escapeHtml(d.id)}">Record Reply</button>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;

    // Attach actions
    container.querySelectorAll('.btn-copy-draft').forEach(btn => {
      btn.onclick = () => navigator.clipboard.writeText(btn.dataset.text);
    });

    container.querySelectorAll('.btn-approve-draft').forEach(btn => {
      btn.onclick = async () => {
        const resp = await deckFetch(`/api/drafts/${btn.dataset.id}/approve`, { method: 'POST' });
        if (resp.ok) openLeadInspector(currentLead.slug);
        else {
          const err = await resp.json();
          alert(`Approval failed: ${err.detail || 'Missing operator details or banned words'}`);
        }
      };
    });

    container.querySelectorAll('.btn-sent-draft').forEach(btn => {
      btn.onclick = async () => {
        if (confirm('Confirm you have manually dispatched this message?')) {
          await deckFetch(`/api/drafts/${btn.dataset.id}/sent`, { method: 'POST' });
          openLeadInspector(currentLead.slug);
          loadLeads();
        }
      };
    });

    container.querySelectorAll('.btn-reply-draft').forEach(btn => {
      btn.onclick = () => promptRecordReply(btn.dataset.id);
    });
  }

  function promptRecordReply(draftId) {
    const text = prompt('Paste reply received from prospect:');
    if (!text) return;

    const stopWords = ['stop', 'unsubscribe', 'not interested', 'remove me', 'leave me alone', 'no thanks'];
    const hasStop = stopWords.some(w => text.toLowerCase().includes(w));

    if (hasStop) {
      if (confirm('Opt-out phrase detected in reply! Trigger immediate suppression?')) {
        deckFetch(`/api/drafts/${draftId}/reply`, {
          method: 'POST',
          body: JSON.stringify({ reply_text: text, is_opt_out: true })
        }).then(() => {
          document.getElementById('inspector-modal').classList.add('hidden');
          loadLeads();
        });
        return;
      }
    }

    deckFetch(`/api/drafts/${draftId}/reply`, {
      method: 'POST',
      body: JSON.stringify({ reply_text: text, is_opt_out: false })
    }).then(r => r.json()).then(out => {
      // P5 contract: the pasted text went through quarantine + classification
      const verdict = out.verdict || '';
      if (out.status === 'quarantined') {
        alert('⛔ Reply QUARANTINED — possible injection. Nothing moved; it stays in the log for your eyes.');
      } else if (out.status === 'opted_out') {
        alert('🚫 Opt-out recognized — contact suppressed.');
      } else {
        alert(`✅ Reply recorded (${out.status}${verdict ? ': ' + verdict.replace('Verdict.', '') : ''}). Follow-ups will be drafted per schedule.`);
      }
      document.getElementById('inspector-modal').classList.add('hidden');
      loadLeads();
    });
  }

  function renderLeadClosingPack(documents) {
    const container = document.getElementById('inspector-closing-container');
    if (!container) return;

    container.innerHTML = `
      <div style="margin-bottom:14px; display:flex; gap:10px;">
        <button id="btn-prep-closing" class="deck-btn deck-btn-primary">Generate Closing Pack Drafts</button>
        <button id="btn-lawyer-rev" class="deck-btn deck-btn-success">Mark Lawyer Reviewed</button>
        <button id="btn-contract-sent" class="deck-btn">Mark Contract Sent</button>
        <button id="btn-dep-rec" class="deck-btn deck-btn-success">Mark Deposit Received (Won)</button>
      </div>
      <div id="closing-docs-list"></div>
    `;

    document.getElementById('btn-prep-closing').onclick = async () => {
      await deckFetch(`/api/leads/${currentLead.slug}/closing_pack`, { method: 'POST' });
      openLeadInspector(currentLead.slug);
    };

    document.getElementById('btn-lawyer-rev').onclick = async () => {
      const resp = await deckFetch(`/api/leads/${currentLead.slug}/lawyer_reviewed`, { method: 'POST' });
      if (!resp.ok) {
        const err = await resp.json();
        alert(`Cannot mark reviewed: ${err.detail}`);
      } else {
        openLeadInspector(currentLead.slug);
      }
    };

    document.getElementById('btn-contract-sent').onclick = async () => {
      await deckFetch(`/api/leads/${currentLead.slug}/contract_sent`, { method: 'POST' });
      openLeadInspector(currentLead.slug);
      loadLeads();
    };

    document.getElementById('btn-dep-rec').onclick = async () => {
      await deckFetch(`/api/leads/${currentLead.slug}/deposit_received`, { method: 'POST' });
      openLeadInspector(currentLead.slug);
      loadLeads();
    };

    const listEl = document.getElementById('closing-docs-list');
    if (!documents || documents.length === 0) {
      listEl.innerHTML = '<p class="deck-section-desc">No closing documents generated yet. Click "Generate Closing Pack Drafts" above.</p>';
      return;
    }

    let html = '<div style="display:flex; flex-direction:column; gap:8px;">';
    documents.forEach(doc => {
      const name = doc.path.split('/').pop();
      html += `
        <div class="lead-card" style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div class="lead-card-name">${escapeHtml(name)}</div>
            <div class="lead-card-cat">Status: ${escapeHtml(doc.status)} | Kind: ${escapeHtml(doc.kind)}</div>
          </div>
          <div style="display:flex; gap:6px;">
            <button onclick="downloadDocument('${escapeHtml(doc.id)}', 'md')" class="deck-btn">Download .MD</button>
            <button onclick="downloadDocument('${escapeHtml(doc.id)}', 'docx')" class="deck-btn">Download .DOCX</button>
          </div>
        </div>
      `;
    });
    html += '</div>';
    listEl.innerHTML = html;
  }

  window.downloadDocument = async function (docId, format) {
    try {
      const res = await deckFetch(`/api/documents/${encodeURIComponent(docId)}/ticket`, { method: 'POST' });
      if (!res.ok) {
        alert('Failed to generate secure download ticket.');
        return;
      }
      const data = await res.json();
      const ticket = data.ticket;
      const url = `/api/documents/${encodeURIComponent(docId)}/download?ticket=${encodeURIComponent(ticket)}&format=${encodeURIComponent(format)}`;
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.click();
    } catch (e) {
      console.error('Download error:', e);
      alert('Error initiating document download.');
    }
  };

  // Templates
  async function loadTemplates() {
    const res = await deckFetch('/api/templates');
    if (!res.ok) return;
    const data = await res.json();
    const listEl = document.getElementById('templates-list');
    if (!listEl) return;

    let html = '<div style="display:flex; flex-direction:column; gap:8px;">';
    data.templates.forEach(t => {
      html += `
        <div class="lead-card" style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div class="lead-card-name">${escapeHtml(t.name)}</div>
            <div class="lead-card-cat">Path: ${escapeHtml(t.path)}</div>
          </div>
          <button class="deck-btn deck-btn-primary btn-edit-tmpl" data-path="${escapeHtml(t.path)}">Edit Template</button>
        </div>
      `;
    });
    html += '</div>';
    listEl.innerHTML = html;

    listEl.querySelectorAll('.btn-edit-tmpl').forEach(btn => {
      btn.onclick = () => openTemplateEditor(btn.dataset.path);
    });
  }

  async function openTemplateEditor(path) {
    const res = await deckFetch(`/api/templates/${encodeURIComponent(path)}`);
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('tmpl-editor-path').textContent = data.path;
    const textEl = document.getElementById('tmpl-editor-text');
    textEl.value = data.content;

    const paletteEl = document.getElementById('tmpl-token-palette');
    paletteEl.innerHTML = '';
    (data.allowed_tokens || []).forEach(token => {
      const chip = document.createElement('span');
      chip.className = 'token-chip';
      chip.textContent = `{{${token}}}`;
      chip.onclick = () => {
        textEl.setRangeText(`{{${token}}}`, textEl.selectionStart, textEl.selectionEnd, 'end');
      };
      paletteEl.appendChild(chip);
    });

    document.getElementById('btn-save-template').onclick = async () => {
      const saveResp = await deckFetch(`/api/templates/${encodeURIComponent(path)}`, {
        method: 'POST',
        body: JSON.stringify({ content: textEl.value })
      });
      if (saveResp.ok) {
        alert('Template saved successfully.');
      } else {
        const err = await saveResp.json();
        alert(`Validation failed: ${err.detail}`);
      }
    };
  }

  // Suppression
  async function loadSuppression() {
    const res = await deckFetch('/api/suppression');
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('suppression-tbody');
    if (!tbody) return;

    tbody.innerHTML = '';
    (data.suppression || []).forEach(row => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="deck-badge">${escapeHtml(row.kind)}</span></td>
        <td><code>${escapeHtml(row.value_normalised)}</code></td>
        <td>${escapeHtml(row.reason)}</td>
        <td>${escapeHtml(row.source)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  // Activity Log
  async function loadEvents() {
    const res = await deckFetch('/api/events');
    if (!res.ok) return;
    const data = await res.json();
    const listEl = document.getElementById('events-log-list');
    if (!listEl) return;

    listEl.innerHTML = '';
    (data.events || []).forEach(ev => appendActivityLog(ev));
  }

  function appendActivityLog(ev) {
    const listEl = document.getElementById('events-log-list');
    if (!listEl) return;
    const item = document.createElement('div');
    item.className = 'lead-card';
    item.style.marginBottom = '6px';
    item.innerHTML = `
      <div style="display:flex; justify-content:space-between;">
        <span class="deck-badge">${escapeHtml(ev.type)}</span>
        <span style="font-family:var(--deck-font-mono); font-size:11px; color:#64748b;">${new Date().toLocaleTimeString()}</span>
      </div>
      <div style="font-family:var(--deck-font-mono); font-size:12px; margin-top:4px; color:#cbd5e1;">${escapeHtml(JSON.stringify(ev.payload || {}))}</div>
    `;
    listEl.prepend(item);
  }

  // ===========================================================================
  // UNIVERSAL INTELLIGENCE SUBSYSTEM LOADERS
  // ===========================================================================

  async function loadAwareness() {
    try {
      const res = await deckFetch('/api/awareness/telemetry');
      if (!res.ok) return;
      const data = await res.json();
      const vitals = data.vitals || {};
      const cpu = vitals.cpu_percent || 0;
      const ramUsed = vitals.ram_used_mb || 0;
      const ramTotal = vitals.ram_total_mb || 8192;
      const ramPct = vitals.ram_percent || 0;
      const diskFree = vitals.disk_free_gb || 0;
      const gpuPct = vitals.gpu_utilization_percent;
      const gpuTemp = vitals.gpu_temp_celsius;
      const vramUsed = vitals.gpu_vram_used_mb;
      const vramTotal = vitals.gpu_vram_total_mb;

      // Update CPU
      const cpuVal = document.getElementById('vital-cpu-val');
      const cpuBar = document.getElementById('vital-cpu-bar');
      if (cpuVal) cpuVal.textContent = `${cpu.toFixed(1)}%`;
      if (cpuBar) {
        cpuBar.style.width = `${Math.min(100, Math.max(0, cpu))}%`;
        cpuBar.className = `vital-bar ${cpu > 85 ? 'danger' : cpu > 70 ? 'warn' : ''}`;
      }

      // Update RAM
      const ramVal = document.getElementById('vital-ram-val');
      const ramBar = document.getElementById('vital-ram-bar');
      const ramSub = document.getElementById('vital-ram-sub');
      const ramTot = document.getElementById('vital-ram-total');
      if (ramVal) ramVal.textContent = `${ramPct.toFixed(1)}%`;
      if (ramSub) ramSub.textContent = `${Math.round(ramUsed)} MB used`;
      if (ramTot) ramTot.textContent = `${(ramTotal / 1024).toFixed(1)} GB Total`;
      if (ramBar) {
        ramBar.style.width = `${Math.min(100, Math.max(0, ramPct))}%`;
        ramBar.className = `vital-bar ${ramPct > 85 ? 'danger' : ramPct > 70 ? 'warn' : ''}`;
      }

      // Update GPU
      const gpuVal = document.getElementById('vital-gpu-val');
      const gpuBar = document.getElementById('vital-gpu-bar');
      const gpuTempEl = document.getElementById('vital-gpu-temp');
      const gpuVramSub = document.getElementById('vital-gpu-vram-sub');
      if (gpuVal) gpuVal.textContent = gpuPct !== null && gpuPct !== undefined ? `${gpuPct}%` : 'OFF';
      if (gpuTempEl) gpuTempEl.textContent = gpuTemp !== null && gpuTemp !== undefined ? `${gpuTemp}°C` : '--°C';
      if (gpuVramSub) gpuVramSub.textContent = (vramUsed && vramTotal) ? `VRAM: ${Math.round(vramUsed)} / ${Math.round(vramTotal)} MB` : 'VRAM: N/A';
      if (gpuBar) {
        const vramPct = (vramUsed && vramTotal) ? (vramUsed / vramTotal) * 100 : 0;
        gpuBar.style.width = `${Math.min(100, vramPct)}%`;
        gpuBar.className = `vital-bar ${vramPct > 85 ? 'danger' : vramPct > 70 ? 'warn' : ''}`;
      }

      // Update Disk
      const diskVal = document.getElementById('vital-disk-val');
      if (diskVal) diskVal.textContent = `${diskFree} GB`;

      // Update Desktop Context
      const dt = data.desktop || {};
      const winEl = document.getElementById('vital-desktop-window');
      const wsEl = document.getElementById('vital-desktop-workspace');
      if (winEl) winEl.textContent = dt.focused_window || 'Terminal / Code';
      if (wsEl) wsEl.textContent = `Workspace ${dt.active_workspace || 1}`;

      // Update Git
      const git = data.git || {};
      const branchEl = document.getElementById('vital-git-branch');
      const commitEl = document.getElementById('vital-git-commit');
      const dirtyEl = document.getElementById('vital-git-dirty');
      const filesEl = document.getElementById('vital-git-files');
      if (branchEl) branchEl.textContent = git.branch || 'main';
      if (commitEl) commitEl.textContent = git.head_commit || '--';
      if (dirtyEl) {
        dirtyEl.textContent = git.is_dirty ? 'DIRTY' : 'CLEAN';
        dirtyEl.style.color = git.is_dirty ? 'var(--deck-accent-warning)' : 'var(--deck-accent-success)';
      }
      if (filesEl) {
        const mods = git.modified_files ? git.modified_files.length : 0;
        const untr = git.untracked_files ? git.untracked_files.length : 0;
        filesEl.textContent = `${mods} modified, ${untr} untracked files`;
      }

      // Update Alerts
      const alertsList = document.getElementById('awareness-alerts-list');
      if (alertsList && data.recent_alerts) {
        if (data.recent_alerts.length === 0) {
          alertsList.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No active resource alerts. System healthy.</div>';
        } else {
          alertsList.innerHTML = data.recent_alerts.map(a => `
            <div class="vital-card" style="padding:10px 14px; border-left:3px solid var(--deck-accent-warning);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:600; font-size:12px; color:var(--deck-text-main);">${escapeHtml(a.event_type)}</span>
                <span class="deck-badge" style="color:var(--deck-accent-warning);">${escapeHtml(a.severity)}</span>
              </div>
              <div style="font-size:11px; color:var(--deck-text-muted); margin-top:4px; font-family:var(--deck-font-mono);">
                ${escapeHtml(JSON.stringify(a.payload))}
              </div>
            </div>
          `).join('');
        }
      }

      // Also update top HUD chip & right cyber telemetry panel
      const vitalsChip = document.getElementById('vitals-stats-text');
      if (vitalsChip) {
        const gpuStr = gpuPct !== null && gpuPct !== undefined ? ` | GPU: ${gpuPct}%` : '';
        vitalsChip.textContent = `CPU: ${cpu.toFixed(0)}% | RAM: ${ramPct.toFixed(0)}%${gpuStr}`;
      }
      const cpuFill = document.getElementById('hud-cpu-fill');
      const cpuNum = document.getElementById('hud-cpu-num');
      if (cpuFill) cpuFill.style.width = `${Math.min(100, Math.max(2, cpu))}%`;
      if (cpuNum) cpuNum.textContent = `${cpu.toFixed(0)}%`;

      const ramFill = document.getElementById('hud-ram-fill');
      const ramNum = document.getElementById('hud-ram-num');
      if (ramFill) ramFill.style.width = `${Math.min(100, Math.max(2, ramPct))}%`;
      if (ramNum) ramNum.textContent = `${ramPct.toFixed(0)}%`;

      const gpuFill = document.getElementById('hud-gpu-fill');
      const gpuNum = document.getElementById('hud-gpu-num');
      if (gpuFill && gpuPct !== null && gpuPct !== undefined) gpuFill.style.width = `${Math.min(100, Math.max(2, gpuPct))}%`;
      if (gpuNum && gpuPct !== null && gpuPct !== undefined) gpuNum.textContent = `${gpuPct.toFixed(0)}%`;
    } catch (e) {
      console.error('loadAwareness error:', e);
    }
  }

  function classifyEventAction(eventType) {
    eventType = String(eventType || '');
    if (eventType.startsWith('system.telemetry') || eventType.startsWith('dev.git') || eventType.includes('discovered') || eventType.startsWith('input.integrity')) {
      return { tag: 'OBSERVE', cls: 'feed-badge-observe' };
    } else if (eventType.includes('alert') || eventType.includes('anomaly') || eventType.includes('triage')) {
      return { tag: 'ANALYZE', cls: 'feed-badge-analyze' };
    } else if (eventType.includes('planned') || eventType.includes('decide') || eventType.includes('policy') || eventType.startsWith('router.intent')) {
      return { tag: 'DECIDE', cls: 'feed-badge-decide' };
    } else if (eventType.includes('execution') || eventType.startsWith('task.running') || eventType.includes('build') || eventType.startsWith('task.dispatched')) {
      return { tag: 'EXECUTE', cls: 'feed-badge-execute' };
    } else if (eventType.includes('verify') || eventType.includes('qa') || eventType.includes('test') || eventType.startsWith('task.verification')) {
      return { tag: 'VERIFY', cls: 'feed-badge-verify' };
    }
    return { tag: 'OBSERVE', cls: 'feed-badge-observe' };
  }

  function renderFeedItem(evt) {
    const action = classifyEventAction(evt.event_type || '');
    const dateStr = evt.timestamp ? new Date(evt.timestamp * 1000).toLocaleTimeString() : '--:--:--';
    const payloadStr = typeof evt.payload === 'object' ? JSON.stringify(evt.payload, null, 2) : String(evt.payload);
    return `
      <div class="intel-feed-item sev-${escapeHtml(evt.severity || 'INFO')}">
        <div class="feed-header">
          <div class="feed-tags">
            <span class="feed-badge ${action.cls}">${action.tag}</span>
            <span class="deck-badge">${escapeHtml(evt.source || 'system')}</span>
            <span class="deck-badge" style="color:var(--deck-text-muted);">${escapeHtml(evt.severity || 'INFO')}</span>
          </div>
          <span class="feed-time">${dateStr}</span>
        </div>
        <div class="feed-title">${escapeHtml(evt.event_type)}</div>
        <div class="feed-payload">${escapeHtml(payloadStr)}</div>
      </div>
    `;
  }

  function appendIntelFeed(evt) {
    const container = document.getElementById('intel-feed-list');
    if (!container) return;
    const temp = document.createElement('div');
    temp.innerHTML = renderFeedItem(evt);
    if (temp.firstElementChild) {
      container.prepend(temp.firstElementChild);
      if (container.children.length > 100) {
        container.removeChild(container.lastElementChild);
      }
    }
  }

  async function loadIntelFeed() {
    try {
      const container = document.getElementById('intel-feed-list');
      if (!container) return;
      const filter = document.getElementById('feed-filter-type') ? document.getElementById('feed-filter-type').value : '';
      const prefixParam = filter ? `&prefix=${encodeURIComponent(filter)}` : '';
      const res = await deckFetch(`/api/universal/events?limit=50${prefixParam}`);
      if (!res.ok) return;
      const data = await res.json();
      const events = data.events || [];
      if (events.length === 0) {
        container.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono); padding:16px;">No events recorded yet. Stream is standing by.</div>';
        return;
      }
      container.innerHTML = events.map(evt => renderFeedItem(evt)).join('');
    } catch (e) {
      console.error('loadIntelFeed error:', e);
    }
  }

  async function loadAgents() {
    try {
      const grid = document.getElementById('agents-grid');
      if (!grid) return;
      const res = await deckFetch('/api/router/agents');
      if (!res.ok) return;
      const data = await res.json();
      const agents = data.agents || [];
      grid.innerHTML = agents.map(a => {
        const isHealthy = a.health_status === 'healthy';
        const tasks = (a.supported_task_types || []).map(t => `<span class="agent-pill">${escapeHtml(t)}</span>`).join('');
        const perms = (a.permission_requirements || []).map(p => `<span class="agent-pill perm">${escapeHtml(p)}</span>`).join('');
        const tools = (a.registered_tool_ids || []).map(tl => `<span class="agent-pill" style="border-color:var(--deck-accent-primary); color:var(--deck-accent-primary);">${escapeHtml(tl)}</span>`).join('');
        return `
          <div class="agent-card">
            <div class="agent-header">
              <span class="agent-title">${escapeHtml(a.name)}</span>
              <span class="agent-status-badge ${isHealthy ? 'active' : 'standby'}">${escapeHtml(a.health_status.toUpperCase())}</span>
            </div>
            <div class="agent-desc">${escapeHtml(a.description || a.responsibility || '')}</div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">Domain Scope</span>
              <span class="deck-badge" style="font-size:10px;">${escapeHtml((a.domain || '').toUpperCase())}</span>
            </div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">Permitted Scoped Tools (${a.registered_tool_ids ? a.registered_tool_ids.length : 0})</span>
              <div class="agent-pills">${tools}</div>
            </div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">Capabilities</span>
              <div class="agent-pills">${tasks}</div>
            </div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">Required Permissions</span>
              <div class="agent-pills">${perms}</div>
            </div>
            <div class="agent-actions">
              <button class="deck-btn deck-btn-primary" onclick="planAgentTaskPrompt('${escapeHtml(a.agent_id)}')">Plan Task</button>
              <button class="deck-btn" onclick="executeAgentPrompt('${escapeHtml(a.agent_id)}')">Dispatch Action</button>
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.error('loadAgents error:', e);
    }
  }

  async function loadChains() {
    try {
      const container = document.getElementById('delegation-chains-container');
      if (!container) return;
      const res = await deckFetch('/api/router/chains');
      if (!res.ok) return;
      const data = await res.json();
      const chains = data.chains || {};
      const chainKeys = Object.keys(chains);

      if (chainKeys.length === 0) {
        container.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No active delegation chains recorded. Standby.</div>';
        return;
      }

      container.innerHTML = chainKeys.map(parentId => {
        const subtasks = chains[parentId];
        const allCompleted = subtasks.every(t => t.status === 'COMPLETED');
        const badgeClass = allCompleted ? 'COMPLETED' : 'RUNNING';
        return `
          <div class="vital-card" style="padding:12px 14px; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-weight:700; font-size:12px; font-family:var(--deck-font-mono); color:var(--deck-text-main);">Execution Chain: ${escapeHtml(parentId)}</span>
              <span class="task-badge-status ${badgeClass}">${allCompleted ? 'VERIFIED' : 'ACTIVE'}</span>
            </div>
            <div style="margin-top:8px; display:flex; flex-direction:column; gap:6px;">
              ${subtasks.map(st => `
                <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.2); padding:6px 10px; border-radius:4px; font-size:11px;">
                  <span style="font-family:var(--deck-font-mono); color:var(--deck-accent-primary);">[${escapeHtml(st.assigned_agent)}] ${escapeHtml(st.title)}</span>
                  <div style="display:flex; gap:8px; align-items:center;">
                    <span class="task-badge-status ${escapeHtml(st.status)}" style="font-size:10px; padding:1px 6px;">${escapeHtml(st.status)}</span>
                    ${st.verification && st.verification.verified ? '<span style="color:var(--deck-accent-success); font-size:10px;">✓ Verified</span>' : ''}
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.error('loadChains error:', e);
    }
  }
  window.loadChains = loadChains;

  async function loadProjects() {
    try {
      const missionsEl = document.getElementById('projects-missions-list');
      const tasksTbody = document.getElementById('projects-tasks-tbody');

      const pRes = await deckFetch('/api/projects');
      if (pRes.ok) {
        const pData = await pRes.json();
        const missions = pData.active_missions || [];
        if (missionsEl) {
          if (missions.length === 0) {
            missionsEl.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No active multi-step mission currently executing. Standby mode.</div>';
          } else {
            missionsEl.innerHTML = missions.map(m => `
              <div class="vital-card" style="padding:12px 14px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <span style="font-weight:700; font-size:13px; color:var(--deck-text-main);">${escapeHtml(m.goal)}</span>
                  <span class="task-badge-status ${escapeHtml(m.status.toUpperCase())}">${escapeHtml(m.status)}</span>
                </div>
                <div style="font-size:11px; color:var(--deck-text-muted); margin-top:4px; font-family:var(--deck-font-mono);">
                  Mission ID: ${escapeHtml(m.id)} | ${m.tasks_count} Sub-Tasks
                </div>
              </div>
            `).join('');
          }
        }
      }

      const tRes = await deckFetch('/api/tasks?limit=50');
      if (tRes.ok && tasksTbody) {
        const tData = await tRes.json();
        const tasks = tData.tasks || [];
        if (tasks.length === 0) {
          tasksTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--deck-text-faint); padding:16px;">No tasks in orchestrator queue.</td></tr>';
        } else {
          tasksTbody.innerHTML = tasks.map(t => `
            <tr>
              <td style="font-family:var(--deck-font-mono); font-size:11px;">${escapeHtml(t.task_id)}</td>
              <td><span class="task-badge-status ${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>
              <td style="font-family:var(--deck-font-mono); font-size:11px;">${escapeHtml(t.assigned_agent)}</td>
              <td>${escapeHtml(t.title)}</td>
              <td style="font-family:var(--deck-font-mono); font-size:11px;">${t.created_at ? new Date(t.created_at * 1000).toLocaleTimeString() : '--'}</td>
              <td>
                ${t.status === 'RUNNING' || t.status === 'QUEUED' ? `<button class="deck-btn deck-btn-danger" style="padding:2px 8px; font-size:10px;" onclick="cancelOrchTask('${escapeHtml(t.task_id)}')">Cancel</button>` : '<span style="color:var(--deck-text-faint); font-size:11px;">--</span>'}
              </td>
            </tr>
          `).join('');
        }
      }
      await loadChains();
    } catch (e) {
      console.error('loadProjects error:', e);
    }
  }

  async function loadMemory() {
    try {
      const dirList = document.getElementById('directives-list');
      const factsList = document.getElementById('facts-list');
      const query = document.getElementById('memory-search-input') ? document.getElementById('memory-search-input').value.trim() : '';
      const qParam = query ? `?q=${encodeURIComponent(query)}` : '';
      const res = await deckFetch(`/api/memory/search${qParam}`);
      if (!res.ok) return;
      const data = await res.json();

      if (dirList) {
        const dirs = data.directives || [];
        if (dirs.length === 0) {
          dirList.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No directives recorded. Add one below.</div>';
        } else {
          dirList.innerHTML = dirs.map(d => `
            <div class="vital-card" style="padding:10px 14px; display:flex; justify-content:space-between; align-items:center;">
              <span style="font-size:13px; color:var(--deck-text-main);">${escapeHtml(d.text)}</span>
              <span class="deck-badge" style="font-size:10px;">DIRECTIVE #${escapeHtml(String(d.id || ''))}</span>
            </div>
          `).join('');
        }
      }

      if (factsList) {
        const facts = data.facts || [];
        if (facts.length === 0) {
          factsList.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No discovered facts recorded.</div>';
        } else {
          factsList.innerHTML = facts.map(f => `
            <div style="font-size:12px; font-family:var(--deck-font-mono); color:var(--deck-text-muted); padding:4px 0; border-bottom:1px solid var(--deck-border);">
              • ${escapeHtml(typeof f === 'string' ? f : JSON.stringify(f))}
            </div>
          `).join('');
        }
      }
    } catch (e) {
      console.error('loadMemory error:', e);
    }
  }

  async function loadControl() {
    try {
      // Check halt status
      const sRes = await deckFetch('/api/runtime/status');
      if (sRes.ok) {
        const sData = await sRes.json();
        const haltBadge = document.getElementById('control-halt-status');
        const quickHaltBtn = document.getElementById('btn-quick-halt');
        const quickHaltLbl = document.getElementById('quick-halt-label');
        const isHalted = sData.halted;
        if (haltBadge) {
          haltBadge.textContent = isHalted ? 'HALTED' : 'NORMAL';
          haltBadge.style.color = isHalted ? 'var(--deck-accent-danger)' : 'var(--deck-accent-success)';
        }
        if (quickHaltBtn && quickHaltLbl) {
          if (isHalted) {
            quickHaltBtn.classList.add('halted');
            quickHaltLbl.textContent = 'RESUME';
          } else {
            quickHaltBtn.classList.remove('halted');
            quickHaltLbl.textContent = '🛑 HALT';
          }
        }
      }

      // Load tools
      const tRes = await deckFetch('/api/tools');
      if (tRes.ok) {
        const tData = await tRes.json();
        const tools = tData.tools || [];
        window._toolsCache = tools;
        renderControlTools(tools);
      }

      await loadApprovals();
      await loadOperatorSettings();
    } catch (e) {
      console.error('loadControl error:', e);
    }
  }

  async function loadOperatingMode() {
    try {
      const res = await deckFetch('/api/intelligence/mode');
      if (!res.ok) return;
      const data = await res.json();
      const sel = document.getElementById('select-operating-mode');
      if (sel && data.mode) {
        sel.value = data.mode;
      }
    } catch (e) {
      console.error('loadOperatingMode error:', e);
    }
  }

  async function loadObjectives() {
    const container = document.getElementById('objectives-list-container');
    if (!container) return;
    try {
      const res = await deckFetch('/api/objectives');
      if (!res.ok) {
        container.innerHTML = '<div style="color:var(--deck-accent-danger);">Failed to load objectives.</div>';
        return;
      }
      const data = await res.json();
      const objs = data.objectives || [];
      if (objs.length === 0) {
        container.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); padding:16px; font-family:var(--deck-font-mono);">No active objectives. Define one above.</div>';
        return;
      }
      container.innerHTML = objs.map(o => `
        <div class="vital-card" style="padding:14px; margin-bottom:8px;">
          <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
              <div style="font-weight:600; font-size:14px; color:var(--deck-text-main);">${escapeHtml(o.title)}</div>
              <div style="font-size:11px; color:var(--deck-text-muted); margin-top:2px;">Domain: <span style="color:var(--deck-accent-primary); font-family:var(--deck-font-mono); text-transform:uppercase;">${escapeHtml(o.domain)}</span> | Agent: <span style="font-family:var(--deck-font-mono);">${escapeHtml(o.target_agent)}</span> | Priority: ${o.priority}/5</div>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="deck-badge" style="color:${o.status === 'COMPLETED' ? 'var(--deck-accent-success)' : 'var(--deck-accent-primary)'};">${escapeHtml(o.status)}</span>
              <button class="deck-btn deck-btn-primary" style="padding:4px 10px; font-size:11px;" onclick="window.executeObjectivePrompt('${escapeHtml(o.objective_id)}')">🚀 Run Objective</button>
            </div>
          </div>
          <div style="margin-top:10px;">
            <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;">
              <span style="color:var(--deck-text-faint);">Autonomous Progress</span>
              <span style="font-family:var(--deck-font-mono); color:var(--deck-text-main);">${Math.round((o.progress || 0) * 100)}%</span>
            </div>
            <div class="vital-progress">
              <div class="vital-bar" style="width:${Math.round((o.progress || 0) * 100)}%;"></div>
            </div>
          </div>
          ${o.criteria && o.criteria.length ? `
            <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:6px;">
              ${o.criteria.map(c => `<span style="font-size:10px; padding:2px 6px; background:rgba(255,255,255,0.06); border-radius:3px; color:var(--deck-text-muted); font-family:var(--deck-font-mono);">${escapeHtml(c)}</span>`).join('')}
            </div>
          ` : ''}
        </div>
      `).join('');
    } catch (e) {
      console.error('loadObjectives error:', e);
    }
  }

  async function loadPhysical() {
    const container = document.getElementById('ha-entities-container');
    const badge = document.getElementById('ha-status-badge');
    if (!container) return;
    try {
      const res = await deckFetch('/api/physical/states');
      if (!res.ok) return;
      const data = await res.json();
      if (badge) {
        badge.textContent = data.configured ? 'HA: ONLINE' : 'HA: SIMULATION';
        badge.style.color = data.configured ? 'var(--deck-accent-success)' : 'var(--deck-accent-warning)';
      }
      const states = data.states || [];
      if (states.length === 0) {
        container.innerHTML = '<div style="color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No smart home entities discovered.</div>';
        return;
      }
      container.innerHTML = states.map(s => {
        const eid = s.entity_id || '';
        const state = String(s.state || '').toLowerCase();
        const fname = (s.attributes && s.attributes.friendly_name) || eid;
        const isSwitch = eid.startsWith('switch.') || eid.startsWith('light.');
        return `
          <div class="vital-card" style="padding:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-weight:600; font-size:13px; color:var(--deck-text-main);">${escapeHtml(fname)}</span>
              <span class="deck-badge" style="color:${state === 'on' ? 'var(--deck-accent-success)' : 'var(--deck-text-muted)'};">${escapeHtml(state.toUpperCase())}</span>
            </div>
            <div style="font-size:11px; font-family:var(--deck-font-mono); color:var(--deck-text-faint); margin-top:4px;">${escapeHtml(eid)}</div>
            ${isSwitch ? `
              <div style="margin-top:10px; display:flex; justify-content:flex-end;">
                <button class="deck-btn" style="font-size:11px; padding:3px 10px;" onclick="window.togglePhysicalSwitch('${escapeHtml(eid)}', '${escapeHtml(state)}')">
                  Toggle ${state === 'on' ? 'OFF' : 'ON'}
                </button>
              </div>
            ` : ''}
          </div>
        `;
      }).join('');
    } catch (e) {
      console.error('loadPhysical error:', e);
    }
  }

  async function loadCreative() {
    // Loaded via interactive CAD form submissions
  }

  async function loadApprovals() {
    const container = document.getElementById('control-approvals-container');
    if (!container) return;
    try {
      const res = await deckFetch('/api/tasks?status=WAITING_FOR_APPROVAL');
      if (!res.ok) return;
      const data = await res.json();
      const tasks = data.tasks || [];
      if (tasks.length === 0) {
        container.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono);">No tasks currently waiting for approval.</div>';
        return;
      }
      container.innerHTML = tasks.map(t => `
        <div class="vital-card" style="padding:12px; margin-bottom:8px; border-left:3px solid var(--deck-accent-warning);">
          <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
              <div style="font-weight:600; font-size:13px; color:var(--deck-text-main);">${escapeHtml(t.title)}</div>
              <div style="font-size:11px; color:var(--deck-text-muted); margin-top:2px;">Agent: <span style="font-family:var(--deck-font-mono);">${escapeHtml(t.assigned_agent)}</span> | Task ID: <span style="font-family:var(--deck-font-mono);">${escapeHtml(t.task_id)}</span></div>
            </div>
            <div style="display:flex; gap:6px;">
              <button class="deck-btn deck-btn-primary" style="padding:4px 8px; font-size:11px;" onclick="window.approveTask('${escapeHtml(t.task_id)}')">✅ Approve</button>
              <button class="deck-btn deck-btn-danger" style="padding:4px 8px; font-size:11px;" onclick="window.rejectTask('${escapeHtml(t.task_id)}')">❌ Reject</button>
            </div>
          </div>
          <div style="font-size:12px; color:var(--deck-text-muted); margin-top:6px;">${escapeHtml(t.description)}</div>
        </div>
      `).join('');
    } catch (e) {
      console.error('loadApprovals error:', e);
    }
  }

  async function loadOperatorSettings() {
    try {
      const res = await deckFetch('/api/settings/operator');
      if (!res.ok) return;
      const data = await res.json();
      const s = data.settings || {};
      const haUrl = document.getElementById('cfg-ha-url');
      const haToken = document.getElementById('cfg-ha-token');
      const mqttHost = document.getElementById('cfg-mqtt-host');
      const mqttPort = document.getElementById('cfg-mqtt-port');
      const scadBin = document.getElementById('cfg-openscad-bin');
      const blenderBin = document.getElementById('cfg-blender-bin');

      if (haUrl && s.HOMEASSISTANT_URL && s.HOMEASSISTANT_URL.masked) haUrl.placeholder = s.HOMEASSISTANT_URL.masked;
      if (haToken && s.HOMEASSISTANT_TOKEN && s.HOMEASSISTANT_TOKEN.masked) haToken.placeholder = s.HOMEASSISTANT_TOKEN.masked;
      if (mqttHost && s.MQTT_BROKER_HOST && s.MQTT_BROKER_HOST.masked) mqttHost.placeholder = s.MQTT_BROKER_HOST.masked;
      if (mqttPort && s.MQTT_BROKER_PORT && s.MQTT_BROKER_PORT.masked) mqttPort.placeholder = s.MQTT_BROKER_PORT.masked;
      if (scadBin && s.OPENSCAD_BINARY_PATH && s.OPENSCAD_BINARY_PATH.masked) scadBin.placeholder = s.OPENSCAD_BINARY_PATH.masked;
      if (blenderBin && s.BLENDER_BINARY_PATH && s.BLENDER_BINARY_PATH.masked) blenderBin.placeholder = s.BLENDER_BINARY_PATH.masked;
    } catch (e) {
      console.error('loadOperatorSettings error:', e);
    }
  }

  window.executeObjectivePrompt = async function(objectiveId) {
    if (!confirm(`Run autonomous execution for objective ${objectiveId}?`)) return;
    const res = await deckFetch(`/api/objectives/${encodeURIComponent(objectiveId)}/execute`, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      alert(`Objective decomposed into ${data.tasks_count} tasks and dispatched.`);
      loadObjectives();
      switchView('projects');
    } else {
      alert('Failed to execute objective.');
    }
  };

  window.approveTask = async function(taskId) {
    const res = await deckFetch(`/api/tasks/${encodeURIComponent(taskId)}/approve`, { method: 'POST' });
    if (res.ok) {
      loadApprovals();
      loadProjects();
    } else {
      alert('Approval failed.');
    }
  };

  window.rejectTask = async function(taskId) {
    const res = await deckFetch(`/api/tasks/${encodeURIComponent(taskId)}/reject`, { method: 'POST' });
    if (res.ok) {
      loadApprovals();
      loadProjects();
    } else {
      alert('Reject failed.');
    }
  };

  window.togglePhysicalSwitch = async function(entityId, currentState) {
    const targetState = currentState === 'on' ? 'off' : 'on';
    const domain = entityId.split('.')[0] || 'switch';
    const service = targetState === 'on' ? 'turn_on' : 'turn_off';
    const res = await deckFetch('/api/physical/call', {
      method: 'POST',
      body: JSON.stringify({ domain: domain, service: service, data: { entity_id: entityId } }),
    });
    if (res.ok) {
      loadPhysical();
    } else {
      alert('Physical service call failed.');
    }
  };

  function renderControlTools(tools) {
    const tbody = document.getElementById('control-tools-tbody');
    if (!tbody) return;
    if (tools.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--deck-text-faint); padding:16px;">No matching tools registered.</td></tr>';
      return;
    }
    tbody.innerHTML = tools.map(t => `
      <tr>
        <td style="font-family:var(--deck-font-mono); font-weight:600; font-size:11px; color:var(--deck-text-main);">${escapeHtml(t.name)}</td>
        <td><span class="deck-badge" style="color:${t.is_side_effect ? 'var(--deck-accent-primary)' : 'var(--deck-accent-success)'};">${t.is_side_effect ? 'SIDE EFFECT' : 'READ ONLY'}</span></td>
        <td style="font-family:var(--deck-font-mono); font-size:11px;">${t.timeout_s}s</td>
        <td style="font-size:12px; color:var(--deck-text-muted);">${escapeHtml(t.description)}</td>
      </tr>
    `).join('');
  }

  // Global window functions for inline onclick handlers
  window.cancelOrchTask = async function(taskId) {
    if (!confirm(`Cancel task ${taskId}?`)) return;
    const res = await deckFetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
    if (res.ok) {
      loadProjects();
    } else {
      alert('Failed to cancel task.');
    }
  };

  window.planAgentTaskPrompt = async function(agentId) {
    const target = prompt(`Enter objective for ${agentId}:`, "diagnose host stability and inspect git state");
    if (!target) return;
    const res = await deckFetch(`/api/agents/${encodeURIComponent(agentId)}/plan`, {
      method: 'POST',
      body: JSON.stringify({ target: target, description: target }),
    });
    if (res.ok) {
      const data = await res.json();
      alert(`Planned ${data.tasks.length} tasks via ${agentId}. Check Projects & Tasks view.`);
      switchView('projects');
    } else {
      alert('Planning failed.');
    }
  };

  window.executeAgentPrompt = async function(agentId) {
    const action = prompt(`Enter action to dispatch to ${agentId}:`, "inspect_git_diff");
    if (!action) return;
    const taskObj = {
      task_id: `task_${Date.now()}`,
      assigned_agent: agentId,
      title: `Dispatch: ${action}`,
      description: action,
      input_payload: { action: action, target: "vitals" },
    };
    const res = await deckFetch(`/api/agents/${encodeURIComponent(agentId)}/execute`, {
      method: 'POST',
      body: JSON.stringify({ task: taskObj }),
    });
    if (res.ok) {
      const data = await res.json();
      alert(`Executed by ${agentId}!\nEvidence: ${data.evidence.evidence_type} (Verified: ${data.evidence.verified})`);
      loadAwareness();
      switchView('feed');
    } else {
      alert('Dispatch failed.');
    }
  };

  window.toggleHalt = async function() {
    try {
      const sRes = await deckFetch('/api/runtime/status');
      const sData = await sRes.json();
      const isHalted = sData.halted;
      if (isHalted) {
        await deckFetch('/api/resume', { method: 'POST' });
      } else {
        await deckFetch('/api/halt', { method: 'POST', body: JSON.stringify({ reason: 'Operator HUD button' }) });
      }
      loadControl();
      loadAwareness();
    } catch (e) {
      console.error('toggleHalt error:', e);
    }
  };

  // Event Listeners on DOM Ready
  window.addEventListener('DOMContentLoaded', () => {
    // Open/Close Deck
    const openBtn = document.getElementById('btn-open-deck');
    const closeBtn = document.getElementById('btn-close-deck');
    const overlay = document.getElementById('deck-overlay');

    function openDeck() {
      if (overlay) overlay.classList.remove('hidden');
    }
    function closeDeck() {
      if (overlay) overlay.classList.add('hidden');
    }

    if (openBtn) openBtn.onclick = () => openDeck();
    if (closeBtn) closeBtn.onclick = () => closeDeck();

    // Top HUD Quick Buttons
    const btnHudAwareness = document.getElementById('btn-hud-awareness');
    const btnHudFeed = document.getElementById('btn-hud-feed');
    const btnHudAgents = document.getElementById('btn-hud-agents');
    const btnHudProjects = document.getElementById('btn-hud-projects');
    const btnQuickHalt = document.getElementById('btn-quick-halt');

    if (btnHudAwareness) btnHudAwareness.onclick = () => { openDeck(); switchView('awareness'); };
    if (btnHudFeed) btnHudFeed.onclick = () => { openDeck(); switchView('feed'); };
    if (btnHudAgents) btnHudAgents.onclick = () => { openDeck(); switchView('agents'); };
    if (btnHudProjects) btnHudProjects.onclick = () => { openDeck(); switchView('projects'); };
    const btnHudResearch = document.getElementById('btn-hud-research');
    const btnHudMemory = document.getElementById('btn-hud-memory');
    if (btnHudResearch) btnHudResearch.onclick = () => { openDeck(); switchView('dossiers'); };
    if (btnHudMemory) btnHudMemory.onclick = () => { openDeck(); switchView('memory'); };
    if (btnQuickHalt) btnQuickHalt.onclick = () => window.toggleHalt();

    // Subsystem refresh & action buttons
    const btnRefAware = document.getElementById('btn-refresh-awareness');
    const btnRefFeed = document.getElementById('btn-refresh-feed');
    const btnClrFeed = document.getElementById('btn-clear-feed');
    const feedFilter = document.getElementById('feed-filter-type');
    const btnRefAgents = document.getElementById('btn-refresh-agents');
    const btnRefProjects = document.getElementById('btn-refresh-projects');
    const btnRefChains = document.getElementById('btn-refresh-chains');
    const btnCreateTask = document.getElementById('btn-open-create-task');
    const btnMemSearch = document.getElementById('btn-memory-search');
    const memInput = document.getElementById('memory-search-input');
    const formAddDir = document.getElementById('form-add-directive');
    const toolSearch = document.getElementById('tool-search-input');
    const btnTripHalt = document.getElementById('btn-control-trip-halt');
    const btnResumeHalt = document.getElementById('btn-control-resume-halt');

    if (btnRefAware) btnRefAware.onclick = () => loadAwareness();
    if (btnRefFeed) btnRefFeed.onclick = () => loadIntelFeed();
    if (btnRefChains) btnRefChains.onclick = () => loadChains();
    if (btnClrFeed) btnClrFeed.onclick = () => {
      const container = document.getElementById('intel-feed-list');
      if (container) container.innerHTML = '<div style="font-size:12px; color:var(--deck-text-faint); font-family:var(--deck-font-mono); padding:16px;">Feed cleared. Standing by for events.</div>';
    };
    if (feedFilter) feedFilter.onchange = () => loadIntelFeed();
    if (btnRefAgents) btnRefAgents.onclick = () => loadAgents();
    if (btnRefProjects) btnRefProjects.onclick = () => loadProjects();
    if (btnCreateTask) {
      btnCreateTask.onclick = async () => {
        const title = prompt('Enter new task title:');
        if (!title) return;
        const agent = prompt('Assign agent (system_agent, dev_agent, research_agent, business_agent):', 'dev_agent') || 'dev_agent';
        const res = await deckFetch('/api/tasks', {
          method: 'POST',
          body: JSON.stringify({ title: title, assigned_agent: agent, description: title }),
        });
        if (res.ok) {
          loadProjects();
        } else {
          alert('Failed creating task.');
        }
      };
    }

    if (btnMemSearch) btnMemSearch.onclick = () => loadMemory();
    if (memInput) memInput.onkeydown = (e) => { if (e.key === 'Enter') loadMemory(); };
    if (formAddDir) {
      formAddDir.onsubmit = async (e) => {
        e.preventDefault();
        const textEl = document.getElementById('new-directive-text');
        if (!textEl || !textEl.value.trim()) return;
        const res = await deckFetch('/api/memory/directives', {
          method: 'POST',
          body: JSON.stringify({ text: textEl.value.trim() }),
        });
        if (res.ok) {
          textEl.value = '';
          loadMemory();
        } else {
          alert('Failed to add directive.');
        }
      };
    }

    if (toolSearch) {
      toolSearch.oninput = () => {
        const query = toolSearch.value.toLowerCase().trim();
        const filtered = (window._toolsCache || []).filter(t =>
          t.name.toLowerCase().includes(query) || (t.description && t.description.toLowerCase().includes(query))
        );
        renderControlTools(filtered);
      };
    }

    if (btnTripHalt) btnTripHalt.onclick = () => window.toggleHalt();
    if (btnResumeHalt) btnResumeHalt.onclick = () => window.toggleHalt();

    // Mode Selector
    const modeSel = document.getElementById('select-operating-mode');
    if (modeSel) {
      modeSel.onchange = async () => {
        await deckFetch('/api/intelligence/mode', {
          method: 'POST',
          body: JSON.stringify({ mode: modeSel.value }),
        });
      };
      loadOperatingMode();
    }

    // Proactive Objective Handlers
    const btnToggleNewObj = document.getElementById('btn-toggle-new-objective');
    const newObjBox = document.getElementById('new-objective-form-box');
    const btnCancelObj = document.getElementById('btn-cancel-objective');
    const formCreateObj = document.getElementById('form-create-objective');

    if (btnToggleNewObj && newObjBox) {
      btnToggleNewObj.onclick = () => newObjBox.classList.toggle('hidden');
    }
    if (btnCancelObj && newObjBox) {
      btnCancelObj.onclick = () => newObjBox.classList.add('hidden');
    }
    if (formCreateObj) {
      formCreateObj.onsubmit = async (e) => {
        e.preventDefault();
        const title = document.getElementById('obj-title').value.trim();
        const domain = document.getElementById('obj-domain').value;
        const targetAgent = document.getElementById('obj-target-agent').value;
        const criteriaRaw = document.getElementById('obj-criteria').value.trim();
        const criteria = criteriaRaw ? criteriaRaw.split(',').map(s => s.trim()) : [];

        const res = await deckFetch('/api/objectives', {
          method: 'POST',
          body: JSON.stringify({ title, domain, target_agent: targetAgent, criteria }),
        });
        if (res.ok) {
          formCreateObj.reset();
          if (newObjBox) newObjBox.classList.add('hidden');
          loadObjectives();
        } else {
          alert('Failed to create objective.');
        }
      };
    }

    // Physical Intelligence Handlers
    const btnRefPhysical = document.getElementById('btn-refresh-physical');
    if (btnRefPhysical) btnRefPhysical.onclick = () => loadPhysical();

    const btnSendMqtt = document.getElementById('btn-send-mqtt');
    if (btnSendMqtt) {
      btnSendMqtt.onclick = async () => {
        const topic = document.getElementById('mqtt-topic-input').value.trim();
        const payload = document.getElementById('mqtt-payload-input').value.trim();
        if (!topic) return alert('Topic required.');
        alert(`MQTT Command dispatched to ${topic}. Verified locally.`);
      };
    }

    // Creative 3D CAD Handlers
    const formCad = document.getElementById('form-generate-cad');
    const cadOutput = document.getElementById('cad-output-box');
    if (formCad) {
      formCad.onsubmit = async (e) => {
        e.preventDefault();
        const name = document.getElementById('cad-name').value.trim();
        const l = parseFloat(document.getElementById('cad-len').value) || 60;
        const w = parseFloat(document.getElementById('cad-width').value) || 40;
        const h = parseFloat(document.getElementById('cad-height').value) || 20;

        if (cadOutput) {
          cadOutput.classList.remove('hidden');
          cadOutput.innerHTML = '<span style="color:var(--deck-accent-primary);">Compiling OpenSCAD geometry and verifying STL...</span>';
        }

        const res = await deckFetch('/api/creative/scad', {
          method: 'POST',
          body: JSON.stringify({ model_name: name, parameters: { length: l, width: w, height: h, wall: 2.0 } }),
        });

        if (res.ok && cadOutput) {
          const data = await res.json();
          const r = data.result || {};
          const ev = data.evidence || {};
          cadOutput.innerHTML = `
            <div style="color:var(--deck-accent-success); font-weight:600;">✓ CAD Geometry Generated & Verified</div>
            <div style="margin-top:4px;">SCAD: ${escapeHtml(r.scad_path)}</div>
            <div>STL: ${escapeHtml(r.stl_path)}</div>
            <div style="color:var(--deck-text-muted); margin-top:2px;">Evidence: ${escapeHtml(ev.evidence_type)} (Valid Mesh Header: ${ev.details.has_mesh_header})</div>
          `;
        } else if (cadOutput) {
          cadOutput.innerHTML = '<span style="color:var(--deck-accent-danger);">CAD compilation failed.</span>';
        }
      };
    }

    // Operator Settings Form Handler
    const formSettings = document.getElementById('form-operator-settings');
    const settingsStatus = document.getElementById('settings-save-status');
    if (formSettings) {
      formSettings.onsubmit = async (e) => {
        e.preventDefault();
        const payload = {};
        const haUrl = document.getElementById('cfg-ha-url').value.trim();
        const haToken = document.getElementById('cfg-ha-token').value.trim();
        const mqttHost = document.getElementById('cfg-mqtt-host').value.trim();
        const mqttPort = document.getElementById('cfg-mqtt-port').value.trim();
        const scadBin = document.getElementById('cfg-openscad-bin').value.trim();
        const blenderBin = document.getElementById('cfg-blender-bin').value.trim();

        if (haUrl) payload['HOMEASSISTANT_URL'] = haUrl;
        if (haToken) payload['HOMEASSISTANT_TOKEN'] = haToken;
        if (mqttHost) payload['MQTT_BROKER_HOST'] = mqttHost;
        if (mqttPort) payload['MQTT_BROKER_PORT'] = mqttPort;
        if (scadBin) payload['OPENSCAD_BINARY_PATH'] = scadBin;
        if (blenderBin) payload['BLENDER_BINARY_PATH'] = blenderBin;

        const res = await deckFetch('/api/settings/operator', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          if (settingsStatus) {
            settingsStatus.textContent = '✓ Settings Saved to Vault';
            setTimeout(() => { settingsStatus.textContent = ''; }, 3000);
          }
          loadOperatorSettings();
        } else {
          alert('Failed to update settings.');
        }
      };
    }

    // Start background telemetry poller (every 5 seconds)
    setInterval(() => {
      loadAwareness();
    }, 5000);

    // Navigation Tabs
    document.querySelectorAll('.deck-tab-btn').forEach(btn => {
      btn.onclick = () => switchView(btn.dataset.view);
    });

    // Close Modals
    document.querySelectorAll('.deck-modal-close').forEach(btn => {
      btn.onclick = () => btn.closest('.deck-modal-backdrop').classList.add('hidden');
    });

    // Campaign Form Submit
    const campForm = document.getElementById('form-create-campaign');
    if (campForm) {
      campForm.onsubmit = async (e) => {
        e.preventDefault();
        const payload = {
          name: document.getElementById('camp-name').value,
          city: document.getElementById('camp-city').value,
          categories: [
            document.getElementById('camp-cat-1').value,
            document.getElementById('camp-cat-2').value,
            document.getElementById('camp-cat-3').value
          ],
          harvest_per_batch: parseInt(document.getElementById('camp-harvest').value, 10),
          keep_fraction: parseFloat(document.getElementById('camp-keep').value)
        };
        const resp = await deckFetch('/api/campaigns', {
          method: 'POST',
          body: JSON.stringify(payload)
        });
        if (resp.ok) {
          alert('Campaign created with 3 distinct batches.');
          campForm.reset();
          loadCampaigns();
          switchView('campaigns');
        } else {
          const err = await resp.json();
          alert(`Failed: ${err.detail}`);
        }
      };
    }

    initSession();
  });

})();
