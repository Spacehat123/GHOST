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
      const resp = await fetch('/api/auth/session');
      if (resp.ok) {
        const data = await resp.json();
        sessionToken = data.session_token;
        console.log('Command Deck authenticated with session token.');
        initWebSocket();
        loadOperator();
        loadCampaigns();
        loadLeads();
      } else {
        console.error('Failed to obtain session token from server.');
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

  function handleLiveEvent(evt) {
    appendActivityLog(evt);
    // Refresh kanban or queue on relevant state changes
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

    if (viewId === 'kanban') loadLeads();
    if (viewId === 'campaigns') loadCampaigns();
    if (viewId === 'confirmation') loadConfirmationQueue();
    if (viewId === 'templates') loadTemplates();
    if (viewId === 'suppression') loadSuppression();
    if (viewId === 'events') loadEvents();
  }

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
    }).then(() => openLeadInspector(currentLead.slug));
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
            <a href="/api/documents/${escapeHtml(doc.id)}/download?token=${encodeURIComponent(sessionToken)}" target="_blank" class="deck-btn">Download .MD</a>
            <a href="/api/documents/${escapeHtml(doc.id)}/download?format=docx&token=${encodeURIComponent(sessionToken)}" target="_blank" class="deck-btn">Download .DOCX</a>
          </div>
        </div>
      `;
    });
    html += '</div>';
    listEl.innerHTML = html;
  }

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

  // Event Listeners on DOM Ready
  window.addEventListener('DOMContentLoaded', () => {
    // Open/Close Deck
    const openBtn = document.getElementById('btn-open-deck');
    const closeBtn = document.getElementById('btn-close-deck');
    const overlay = document.getElementById('deck-overlay');

    if (openBtn) openBtn.onclick = () => overlay.classList.remove('hidden');
    if (closeBtn) closeBtn.onclick = () => overlay.classList.add('hidden');

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
