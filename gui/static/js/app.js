/**
 * Ghost Client Application
 * Connects WebSocket to Ghost Core + Runner Barge-in,
 * Real Neural Audio Streaming via /api/tts (edge-tts Simon Riley en-GB-RyanNeural),
 * Live FFT orbital filament audio reactivity, mic voice recognition,
 * and secure local OmniRoute connection settings modal.
 */
document.addEventListener('DOMContentLoaded', () => {
  const statusContainer = document.getElementById('status-container');
  const statusLabel = document.getElementById('status-label');
  const orbCanvas = document.getElementById('orb-canvas');
  const transcriptBubble = document.getElementById('transcript-bubble');
  const transcriptText = document.getElementById('transcript-text');
  const toolBadgeContainer = document.getElementById('tool-badge-container');
  const textInput = document.getElementById('text-input');
  const btnSend = document.getElementById('btn-send');
  const btnMic = document.getElementById('btn-mic');
  const micIcon = document.getElementById('mic-icon');
  const btnInterrupt = document.getElementById('btn-interrupt');
  const tokenStatsText = document.getElementById('token-stats-text');

  // Tactical Authorization Modal Elements
  const authModal = document.getElementById('auth-modal');
  const authDescription = document.getElementById('auth-description');
  const btnAuthAllow = document.getElementById('btn-auth-allow');
  const btnAuthDeny = document.getElementById('btn-auth-deny');
  let currentAuthId = null;

  // Settings Modal Elements
  const btnSettings = document.getElementById('btn-settings');
  const settingsModal = document.getElementById('settings-modal');
  const btnCloseModal = document.getElementById('btn-close-modal');
  const btnSaveSettings = document.getElementById('btn-save-settings');
  const cfgBaseUrl = document.getElementById('cfg-base-url');
  const cfgApiKey = document.getElementById('cfg-api-key');
  const cfgModel = document.getElementById('cfg-model');
  const cfgThinking = document.getElementById('cfg-thinking');
  const saveStatus = document.getElementById('save-status');

  // Initialize Cybernetic 3D Gyroscope & HUD Emblem (§9)
  const orb = new GhostOrb(orbCanvas);
  window.ghostOrb = orb;
  let currentState = 'IDLE';

  orb.onIntroDone = () => {
    if (telemetryStateStat && telemetryStatusTag) {
      telemetryStateStat.textContent = 'ONLINE';
      telemetryStatusTag.textContent = '[READY]';
      telemetryStatusTag.style.color = '#10b981';
      telemetryStatusTag.style.textShadow = '0 0 8px rgba(16, 185, 129, 0.4)';
    }
  };

  // Bottom Telemetry Elements
  const telemetryStateStat = document.getElementById('telemetry-state-stat');
  const telemetryStatusTag = document.querySelector('.telemetry-status-tag');

  function updateState(state) {
    currentState = state;
    orb.setState(state);
    statusLabel.textContent = state;
    statusContainer.className = `hud-status status-${state}`;

    if (telemetryStateStat && telemetryStatusTag) {
      if (state === 'LISTENING') {
        telemetryStateStat.textContent = 'RECEIVING DUPLEX';
        telemetryStatusTag.textContent = '[MIC ACTIVE]';
        telemetryStatusTag.style.color = '#00f0ff';
        telemetryStatusTag.style.textShadow = '0 0 8px rgba(0, 240, 255, 0.5)';
      } else if (state === 'THINKING') {
        telemetryStateStat.textContent = 'PROCESSING NEURAL';
        telemetryStatusTag.textContent = '[THINKING]';
        telemetryStatusTag.style.color = '#c084fc';
        telemetryStatusTag.style.textShadow = '0 0 8px rgba(192, 132, 252, 0.5)';
      } else if (state === 'SPEAKING') {
        telemetryStateStat.textContent = 'SYNTHESIZING SPEECH';
        telemetryStatusTag.textContent = '[TRANSMITTING]';
        telemetryStatusTag.style.color = '#ff0055';
        telemetryStatusTag.style.textShadow = '0 0 8px rgba(255, 0, 85, 0.6)';
      } else if (state === 'INTERRUPTED') {
        telemetryStateStat.textContent = 'BARGE-IN PURGE';
        telemetryStatusTag.textContent = '[INTERRUPTED]';
        telemetryStatusTag.style.color = '#f59e0b';
        telemetryStatusTag.style.textShadow = '0 0 8px rgba(245, 158, 11, 0.6)';
      } else {
        telemetryStateStat.textContent = 'ONLINE';
        telemetryStatusTag.textContent = '[READY]';
        telemetryStatusTag.style.color = '#10b981';
        telemetryStatusTag.style.textShadow = '0 0 8px rgba(16, 185, 129, 0.4)';
      }
    }
  }

  // Settings Modal Logic
  async function loadSettings() {
    try {
      const res = await fetch('/api/settings');
      if (res.ok) {
        const data = await res.json();
        cfgBaseUrl.value = data.base_url || 'http://127.0.0.1:20128/v1';
        cfgModel.value = data.model || 'antigravity/gemini-3.8-flash-low';
        cfgThinking.value = data.thinking_level || 'low';
        if (data.has_key) {
          cfgApiKey.placeholder = `Configured (${data.masked_key})`;
        } else {
          cfgApiKey.placeholder = 'Paste your API key here (saved locally)';
        }
      }
    } catch (e) {
      console.warn('Could not load settings:', e);
    }
  }

  btnSettings.addEventListener('click', () => {
    loadSettings();
    saveStatus.textContent = '';
    settingsModal.classList.remove('hidden');
  });

  btnCloseModal.addEventListener('click', () => {
    settingsModal.classList.add('hidden');
  });

  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
      settingsModal.classList.add('hidden');
    }
  });

  btnSaveSettings.addEventListener('click', async () => {
    saveStatus.textContent = 'Saving...';
    saveStatus.style.color = '#93c5fd';

    try {
      const payload = {
        base_url: cfgBaseUrl.value.trim() || 'http://127.0.0.1:20128/v1',
        api_key: cfgApiKey.value.trim(),
        model: cfgModel.value.trim() || 'gemini-3.8-flash',
        thinking_level: cfgThinking.value || 'low'
      };

      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        saveStatus.textContent = '✓ Saved successfully';
        saveStatus.style.color = '#34d399';
        cfgApiKey.value = '';
        loadSettings();
        setTimeout(() => {
          settingsModal.classList.add('hidden');
        }, 800);
      } else {
        const err = await res.json();
        saveStatus.textContent = `Error: ${err.detail || 'Failed'}`;
        saveStatus.style.color = '#f87171';
      }
    } catch (err) {
      saveStatus.textContent = `Error: ${err.message}`;
      saveStatus.style.color = '#f87171';
    }
  });

  // Audio Context & Output Engine
  let audioContext = null;
  let micAnalyser = null;
  let outputAnalyser = null;
  let ttsAudioElement = null;
  let ttsAudioSource = null;
  let micStream = null;
  let isListening = false;
  let recognition = null;

  async function ensureAudioContext() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
    return audioContext;
  }

  // High-Speed Neural TTS Clause Streaming & Audio Queue
  let clauseAudioQueue = [];
  let isPlayingClause = false;
  let activeSpeechSessionId = 0;

  function stopSpeaking() {
    activeSpeechSessionId++;
    clauseAudioQueue = [];
    isPlayingClause = false;
    if (ttsAudioElement) {
      try {
        ttsAudioElement.pause();
        ttsAudioElement.currentTime = 0;
      } catch (e) {}
      ttsAudioElement = null;
    }
    orb.setAmplitude(0.05);
  }

  function splitIntoClauses(text) {
    const clean = text.replace(/[*`_#]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) return [];
    // Split on terminal punctuation and pauses
    const rawParts = clean.split(/(?<=[.!?:;\n—–])\s+|(?<=,)\s+/);
    const merged = [];
    let buf = '';
    for (const p of rawParts) {
      const part = p.trim();
      if (!part) continue;
      if (buf.split(/\s+/).length < 4) {
        buf = (buf + ' ' + part).trim();
      } else {
        merged.push(buf);
        buf = part;
      }
    }
    if (buf) merged.push(buf);
    return merged.length > 0 ? merged : [clean];
  }

  async function speak(text) {
    const cleanText = text.replace(/[*`_#]/g, '').trim();
    if (!cleanText) return;

    await ensureAudioContext();
    stopSpeaking();
    const currentSession = activeSpeechSessionId;

    const clauses = splitIntoClauses(cleanText);
    if (!clauses.length) return;

    updateState('SPEAKING');

    // Queue clauses for seamless sequential playback with zero-latency pipelining
    clauseAudioQueue = [...clauses];
    playNextClauseInQueue(currentSession);
  }

  async function playNextClauseInQueue(sessionId) {
    if (sessionId !== activeSpeechSessionId) return;

    if (clauseAudioQueue.length === 0) {
      isPlayingClause = false;
      orb.setAmplitude(0.05);
      if (currentState === 'SPEAKING') {
        updateState('IDLE');
      }
      return;
    }

    isPlayingClause = true;
    const clause = clauseAudioQueue.shift();

    try {
      const url = `/api/tts?text=${encodeURIComponent(clause)}`;
      const audio = new Audio(url);
      audio.crossOrigin = 'anonymous';
      ttsAudioElement = audio;

      if (!outputAnalyser) {
        outputAnalyser = audioContext.createAnalyser();
        outputAnalyser.fftSize = 128;
      }

      try {
        ttsAudioSource = audioContext.createMediaElementSource(audio);
        ttsAudioSource.connect(outputAnalyser);
        outputAnalyser.connect(audioContext.destination);
      } catch (err) {}

      const bufferLength = outputAnalyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      let animId = null;

      function trackOutputFFT() {
        if (!audio || audio.paused || audio.ended || sessionId !== activeSpeechSessionId) {
          if (animId) cancelAnimationFrame(animId);
          return;
        }
        animId = requestAnimationFrame(trackOutputFFT);
        outputAnalyser.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < bufferLength; i++) sum += dataArray[i];
        const avg = sum / bufferLength;
        orb.setAmplitude(Math.min(1.0, avg / 60));

        const sixBins = new Float32Array(6);
        const step = Math.floor(bufferLength / 6);
        for (let b = 0; b < 6; b++) {
          let binSum = 0;
          for (let s = 0; s < step; s++) binSum += dataArray[b * step + s];
          sixBins[b] = Math.min(1.0, (binSum / step) / 110.0);
        }
        orb.setAudioBins(sixBins);
      }

      audio.onplay = () => {
        trackOutputFFT();
      };

      audio.onended = () => {
        if (sessionId === activeSpeechSessionId) {
          playNextClauseInQueue(sessionId);
        }
      };

      audio.onerror = (e) => {
        console.warn('Clause audio error, advancing queue:', e);
        if (sessionId === activeSpeechSessionId) {
          playNextClauseInQueue(sessionId);
        }
      };

      await audio.play();
    } catch (err) {
      console.error('Failed to play clause:', err);
      if (sessionId === activeSpeechSessionId) {
        playNextClauseInQueue(sessionId);
      }
    }
  }

  // WebSocket Connection
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
  let ws = null;

  function connectWs() {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Connected to Ghost Persistent Core.');
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleServerEvent(data);
    };

    ws.onclose = () => {
      console.warn('WebSocket closed. Reconnecting in 2s...');
      setTimeout(connectWs, 2000);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };
  }
  connectWs();

  function setSubtitle(speaker, text, isAccumulating = false) {
    const speakerEl = transcriptBubble.querySelector('.transcript-speaker');
    if (speakerEl) speakerEl.textContent = speaker;

    if (isAccumulating) {
      transcriptText.textContent += text;
    } else {
      transcriptText.textContent = text;
    }
    transcriptBubble.scrollTop = transcriptBubble.scrollHeight;
  }

  function addToolBadge(name, details) {
    toolBadgeContainer.innerHTML = '';
    const chip = document.createElement('div');
    chip.className = 'tool-chip';
    chip.textContent = `⚡ TOOL / ${name}`;
    toolBadgeContainer.appendChild(chip);

    setTimeout(() => {
      if (toolBadgeContainer.contains(chip)) {
        chip.style.opacity = '0';
        setTimeout(() => chip.remove(), 400);
      }
    }, 5000);
  }

  // Handle Server Events & Pipelined Speech
  let fullSpokenBuffer = '';

  function handleServerEvent(event) {
    if (event.type === 'state') {
      updateState(event.state);
      if (event.state === 'INTERRUPTED') {
        stopSpeaking();
        fullSpokenBuffer = '';
        setSubtitle('SYSTEM', '— Interrupted by Barge-in —');
      } else if (event.state === 'SPEAKING') {
        fullSpokenBuffer = '';
      } else if (event.state === 'IDLE' && fullSpokenBuffer) {
        // Synthesize spoken completion
        speak(fullSpokenBuffer);
        fullSpokenBuffer = '';
      }
    } else if (event.type === 'token') {
      setSubtitle('GHOST', event.text, true);
      fullSpokenBuffer += event.text;
      orb.setAmplitude(0.35 + Math.random() * 0.25);
    } else if (event.type === 'tool_start') {
      addToolBadge(event.tool, event.args);
      setSubtitle('GHOST', event.announcement || `Executing ${event.tool}...`);
    } else if (event.type === 'tool_end') {
      // Completed tool
    } else if (event.type === 'token_stats') {
      if (tokenStatsText) {
        tokenStatsText.textContent = `LAST: ${event.last || 0} | TODAY: ${event.today || 0}`;
      }
    } else if (event.type === 'auth_request') {
      currentAuthId = event.auth_id;
      if (authDescription) {
        authDescription.textContent = event.description || 'Action requires authorization, sir.';
      }
      if (authModal) {
        authModal.classList.remove('hidden');
      }
    } else if (event.type === 'transcript_final') {
      setSubtitle('GHOST', '');
    } else if (event.type === 'narrator.speak') {
      // §14.2: the orb narrates too — visible state without words.
      if (typeof event.payload === 'object' && event.payload && event.payload.utterance) {
        setSubtitle('GHOST', event.payload.utterance);
      }
      orb.setState('SPEAKING');
      clearTimeout(narratorWorkingTimer);
      narratorWorkingTimer = setTimeout(() => orb.setState('IDLE'), 6000);
    } else if (event.type === 'task.acknowledged' || event.type === 'job.enqueued') {
      // Background/long task running: subtle WORKING pulse (§14.2.5).
      orb.setState('WORKING');
      clearTimeout(narratorWorkingTimer);
    } else if (event.type === 'task.done' || event.type === 'task.interrupted'
               || event.type === 'runtime.stopped') {
      orb.setState('IDLE');
      clearTimeout(narratorWorkingTimer);
    }
  }

  let narratorWorkingTimer = null;

  // Microphone and AudioContext for Live FFT Analysis
  async function initAudio() {
    try {
      await ensureAudioContext();
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const source = audioContext.createMediaStreamSource(micStream);
      micAnalyser = audioContext.createAnalyser();
      micAnalyser.fftSize = 128;
      source.connect(micAnalyser);

      const bufferLength = micAnalyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      function pollMic() {
        requestAnimationFrame(pollMic);
        if (currentState === 'LISTENING') {
          micAnalyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < bufferLength; i++) sum += dataArray[i];
          const avg = sum / bufferLength;
          orb.setAmplitude(Math.min(1.0, avg / 75));

          const sixBins = new Float32Array(6);
          const step = Math.floor(bufferLength / 6);
          for (let b = 0; b < 6; b++) {
            let binSum = 0;
            for (let s = 0; s < step; s++) binSum += dataArray[b * step + s];
            sixBins[b] = Math.min(1.0, (binSum / step) / 128.0);
          }
          orb.setAudioBins(sixBins);
        }
      }
      pollMic();
      setupSpeechRecognition();
      return true;
    } catch (e) {
      console.warn("Microphone stream unavailable:", e);
      setupSpeechRecognition();
      return false;
    }
  }

  function setupSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
      isListening = true;
      micIcon.textContent = "🔴";
      btnMic.classList.replace('hud-btn-primary', 'hud-btn-danger');
    };

    recognition.onspeechstart = () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'speech_start' }));
      }
      stopSpeaking();
    };

    recognition.onresult = (e) => {
      let interim = '';
      let final = '';

      for (let i = e.resultIndex; i < e.results.length; ++i) {
        if (e.results[i].isFinal) final += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }

      if (interim) {
        setSubtitle('YOU', interim);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'speech_interim', transcript: interim }));
        }
      }

      if (final) {
        setSubtitle('YOU', final);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'speech_final', transcript: final }));
        }
      }
    };

    recognition.onend = () => {
      if (isListening) {
        try { recognition.start(); } catch(e) {}
      }
    };
  }

  // UI Event Handlers
  btnMic.addEventListener('click', async () => {
    await ensureAudioContext();
    if (!isListening) {
      if (!micStream) await initAudio();
      if (recognition) {
        try { recognition.start(); } catch(e) {}
      }
      isListening = true;
      micIcon.textContent = "🔴";
      updateState('LISTENING');
    } else {
      isListening = false;
      if (recognition) recognition.stop();
      micIcon.textContent = "🎙️";
      btnMic.classList.replace('hud-btn-danger', 'hud-btn-primary');
      updateState('IDLE');
    }
  });

  btnInterrupt.addEventListener('click', async () => {
    await ensureAudioContext();
    stopSpeaking();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'interrupt' }));
    }
    updateState('INTERRUPTED');
  });

  async function sendTextMessage() {
    await ensureAudioContext();
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = '';

    setSubtitle('YOU', text);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'speech_final', transcript: text }));
    }
  }

  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      sendTextMessage();
    }
  });

  if (btnSend) {
    btnSend.addEventListener('click', (e) => {
      e.preventDefault();
      sendTextMessage();
    });
  }

  // Tactical Authorization Handlers
  if (btnAuthAllow) {
    btnAuthAllow.addEventListener('click', () => {
      if (authModal) authModal.classList.add('hidden');
      if (ws && ws.readyState === WebSocket.OPEN && currentAuthId) {
        ws.send(JSON.stringify({ action: 'auth_response', auth_id: currentAuthId, allowed: true }));
        currentAuthId = null;
      }
    });
  }

  if (btnAuthDeny) {
    btnAuthDeny.addEventListener('click', () => {
      if (authModal) authModal.classList.add('hidden');
      if (ws && ws.readyState === WebSocket.OPEN && currentAuthId) {
        ws.send(JSON.stringify({ action: 'auth_response', auth_id: currentAuthId, allowed: false }));
        currentAuthId = null;
      }
    });
  }

  // Tactical Pill HUD Buttons (Reference Video)
  const btnHudHistory = document.getElementById('btn-hud-history');
  const btnHudLogs = document.getElementById('btn-hud-logs');
  const btnHudDev = document.getElementById('btn-hud-dev');
  const btnHudClear = document.getElementById('btn-hud-clear');

  if (btnHudClear) {
    btnHudClear.addEventListener('click', () => {
      document.body.classList.toggle('clean-focus');
      btnHudClear.classList.toggle('active', document.body.classList.contains('clean-focus'));
    });
  }

  // Click canvas or press Escape to exit clean focus view
  if (orbCanvas) {
    orbCanvas.addEventListener('click', () => {
      if (document.body.classList.contains('clean-focus')) {
        document.body.classList.remove('clean-focus');
        if (btnHudClear) btnHudClear.classList.remove('active');
      }
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('clean-focus')) {
      document.body.classList.remove('clean-focus');
      if (btnHudClear) btnHudClear.classList.remove('active');
    }
  });

  if (btnHudHistory) {
    btnHudHistory.addEventListener('click', () => {
      if (transcriptBubble) {
        transcriptBubble.scrollIntoView({ behavior: 'smooth' });
        transcriptBubble.style.borderColor = 'rgba(255, 0, 85, 0.6)';
        setTimeout(() => {
          transcriptBubble.style.borderColor = '';
        }, 1200);
      }
    });
  }

  if (btnHudLogs) {
    btnHudLogs.addEventListener('click', () => {
      const openBtn = document.getElementById('btn-open-deck');
      if (openBtn) openBtn.click();
      const eventsTab = document.querySelector('.deck-tab-btn[data-view="events"]');
      if (eventsTab) eventsTab.click();
    });
  }

  if (btnHudDev) {
    btnHudDev.addEventListener('click', () => {
      // Replay cinematic intro sequence on DEV click (§9.4)
      orb.startIntro(true);
    });
  }

  // Keyboard shortcut: Shift + I to replay intro (§9.4)
  document.addEventListener('keydown', (e) => {
    if (e.shiftKey && (e.key === 'I' || e.key === 'i')) {
      orb.startIntro(true);
    }
  });

  // Global click resume audio context
  document.body.addEventListener('click', () => {
    ensureAudioContext();
  }, { once: true });
});


// ===================================================== Operator Control (T9)
const btnOperator = document.getElementById('btn-operator');
const operatorModal = document.getElementById('operator-modal');
const btnCloseOperator = document.getElementById('btn-close-operator');
const opSections = document.getElementById('operator-sections');
const opStatus = document.getElementById('op-status');
const opTestResult = document.getElementById('op-test-result');
const reservedQueue = document.getElementById('reserved-queue');

const SECTION_LABELS = {
  razorpay: 'Razorpay (payments)',
  smtp: 'Outreach SMTP (the one sending path)',
  domain: 'Sending domain',
  authority: 'Grant authority'
};

async function loadOperator() {
  try {
    const [secRes, resRes] = await Promise.all([
      fetch('/api/settings/sections'), fetch('/api/reserved')
    ]);
    const sections = await secRes.json();
    opSections.innerHTML = '';
    for (const [name, keys] of Object.entries(sections)) {
      if (name === 'settable' || typeof keys !== 'object' || !keys) continue;
      const card = document.createElement('div');
      card.style.cssText = 'border:1px solid #2a2f45; border-radius:8px; padding:8px;';
      const rows = Object.entries(keys).filter(([k]) => k.includes('_')).map(([key, info]) => `
        <div style="display:flex; gap:6px; align-items:center; margin:3px 0;">
          <span style="flex:1; font-size:11px; opacity:.75;">${key}</span>
          <input data-section="${name}" data-key="${key}" placeholder="${info.masked || 'not set'}"
                 style="flex:1.2; background:#0d1020; border:1px solid #2a2f45; color:#e5e7eb;
                        border-radius:4px; padding:3px 6px; font-size:11px;" />
          <span style="font-size:10px; opacity:.6;">${info.set ? '● set' : '○ unset'}</span>
        </div>`).join('');
      const dnsNote = name === 'domain' && keys.dns
        ? `<div style="font-size:10px; opacity:.7;">DNS: ${keys.dns.ready ? '✓ ready' : 'incomplete'}${keys.dns.missing ? ' — ' + keys.dns.missing.length + ' missing' : ''}</div>`
        : '';
      card.innerHTML = `<div style="font-size:12px; font-weight:600; margin-bottom:4px;">
                          ${SECTION_LABELS[name] || name}</div>${rows}${dnsNote}
                        <button class="hud-btn op-save" data-section="${name}"
                                style="margin-top:6px; font-size:11px; padding:3px 10px;">Save ${name}</button>`;
      opSections.appendChild(card);
    }
    opSections.querySelectorAll('.op-save').forEach(btn => {
      btn.addEventListener('click', async () => {
        const section = btn.dataset.section;
        const payload = {};
        opSections.querySelectorAll(`input[data-section="${section}"]`).forEach(inp => {
          if (inp.value.trim()) payload[inp.dataset.key] = inp.value.trim();
        });
        const res = await fetch(`/api/settings/section/${section}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        opStatus.textContent = res.ok ? `✓ ${section} saved` : `error: ${(await res.json()).detail || 'failed'}`;
        opStatus.style.color = res.ok ? '#34d399' : '#f87171';
        loadOperator();
      });
    });
    // reserved acts queue
    const { packages } = await resRes.json();
    const ready = packages.filter(p => p.state === 'ready');
    reservedQueue.innerHTML = ready.length ? '' :
      '<div style="font-size:11px; opacity:.6;">Nothing awaiting signature — Ghost is running the business.</div>';
    ready.forEach(p => {
      const row = document.createElement('div');
      row.style.cssText = 'border:1px solid #2a2f45; border-radius:8px; padding:8px;';
      row.innerHTML = `
        <div style="font-size:12px; font-weight:600;">${p.kind.toUpperCase()} — ${p.subject}</div>
        <div style="font-size:11px; opacity:.75; margin:3px 0;">${p.recommendation || ''}</div>
        <details style="font-size:10px; opacity:.7;"><summary>pre-filled details</summary>
          <pre style="white-space:pre-wrap;">${JSON.stringify(p.payload, null, 1).slice(0, 600)}</pre></details>
        <div style="display:flex; gap:6px; margin-top:6px;">
          <button class="hud-btn hud-btn-primary op-approve" data-id="${p.id}" style="font-size:11px; padding:3px 10px;">Sign / Approve</button>
          <button class="hud-btn op-dismiss" data-id="${p.id}" style="font-size:11px; padding:3px 10px;">Dismiss</button>
        </div>`;
      reservedQueue.appendChild(row);
    });
    reservedQueue.querySelectorAll('.op-approve').forEach(btn => {
      btn.addEventListener('click', async () => {
        const res = await fetch(`/api/reserved/${btn.dataset.id}/approve`, {method: 'POST'});
        const out = await res.json();
        opStatus.textContent = res.ok ? `✓ executed: ${out.state}` : `blocked: ${out.detail || 'error'}`;
        opStatus.style.color = res.ok ? '#34d399' : '#f87171';
        loadOperator();
      });
    });
    reservedQueue.querySelectorAll('.op-dismiss').forEach(btn => {
      btn.addEventListener('click', async () => {
        await fetch(`/api/reserved/${btn.dataset.id}/dismiss`, {method: 'POST'});
        loadOperator();
      });
    });
  } catch (e) {
    opStatus.textContent = `load error: ${e}`;
    opStatus.style.color = '#f87171';
  }
}

btnOperator.addEventListener('click', () => {
  opStatus.textContent = '';
  opTestResult.textContent = '';
  operatorModal.classList.remove('hidden');
  loadOperator();
});
btnCloseOperator.addEventListener('click', () => operatorModal.classList.add('hidden'));
operatorModal.addEventListener('click', (e) => {
  if (e.target === operatorModal) operatorModal.classList.add('hidden');
});
document.getElementById('btn-op-reload').addEventListener('click', loadOperator);
document.querySelectorAll('.op-test').forEach(btn => {
  btn.addEventListener('click', async () => {
    opTestResult.textContent = `testing ${btn.dataset.target}...`;
    opTestResult.style.color = '#93c5fd';
    const res = await fetch(`/api/settings/test/${btn.dataset.target}`, {method: 'POST'});
    const out = await res.json();
    opTestResult.textContent = `${btn.dataset.target}: ${out.state}${out.error ? ' — ' + out.error : ''}${out.missing ? ' — missing: ' + out.missing.length : ''}`;
    opTestResult.style.color = out.ok ? '#34d399' : '#f87171';
  });
});
