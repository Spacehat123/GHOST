/**
 * GHOST Cybernetic Animated HUD Emblem & 3D Gyroscope Engine
 * Implements the full cinematic intro sequence from ghost-intro-11s.mp4:
 *   - Phase A: Warp-in (0.0s - 0.6s) - High-speed 3D tumble, motion blur, diagonal speed streaks
 *   - Phase B: Gyroscope (0.6s - 1.9s) - 3D gimbal assembly, dual offset white arcs, diagonal laser axis,
 *              independent large dashed ring, floating square particles
 *   - Phase C: Lock (1.9s - 2.4s) - Smooth snap-to-flat rotation with subtle scale overshoot,
 *              HUD layers staggering in, white flash, glitch-revealed custom GHOST wordmark
 *   - Phase D: Settled Idle (2.4s+) - Multi-tiered counter-rotating concentric rings, compass ticks,
 *              corner brackets, cardinal crosshairs, ambient micro-embers
 *   - Phase E: Speech Reactivity - Inner neon ring and glow visibly brighten and swell with audio RMS
 *
 * Exposes deterministic seeking for automated test verification:
 *   window.ghostOrb.seek(t)
 */

// =============================================================================
// 1. EASING & INTERPOLATION HELPERS (§7)
// =============================================================================
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const easeOutCubic = t => 1 - Math.pow(1 - t, 3);
const easeInOutCubic = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const easeOutBack = (t, s = 1.15) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2);
const seg = (t, a, b) => clamp((t - a) / (b - a));

// =============================================================================
// 2. 3D PROJECTION & ROTATION MATH (§9.2)
// =============================================================================
function rotXYZ(p, rx, ry, rz) {
  let { x, y, z } = p;
  // Rz * Ry * Rx
  let c = Math.cos(rx), s = Math.sin(rx);
  let t = y * c - z * s; z = y * s + z * c; y = t;

  c = Math.cos(ry); s = Math.sin(ry);
  t = x * c + z * s; z = -x * s + z * c; x = t;

  c = Math.cos(rz); s = Math.sin(rz);
  t = x * c - y * s; y = x * s + y * c; x = t;

  return { x, y, z };
}

function project(p, cx, cy, scale, f = 900, D = 900) {
  const k = f / (D + p.z * scale);
  return { x: cx + p.x * scale * k, y: cy + p.y * scale * k, k };
}

function strokeArc3D(ctx, o) {
  // o: { radius, a0, a1, z, tilt, rig, cx, cy, scale, color, width, dash, steps }
  const steps = o.steps || 72;
  ctx.save();
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const a = o.a0 + (o.a1 - o.a0) * (i / steps);
    let p = { x: Math.cos(a) * o.radius, y: Math.sin(a) * o.radius, z: o.z || 0 };
    if (o.tilt) p = rotXYZ(p, o.tilt.rx, o.tilt.ry, o.tilt.rz);
    p = rotXYZ(p, o.rig.rx, o.rig.ry, o.rig.rz);
    const q = project(p, o.cx, o.cy, o.scale);
    if (i === 0) ctx.moveTo(q.x, q.y);
    else ctx.lineTo(q.x, q.y);
  }
  ctx.strokeStyle = o.color;
  ctx.lineWidth = o.width;
  if (o.dash) ctx.setLineDash(o.dash);
  else ctx.setLineDash([]);
  ctx.stroke();
  ctx.restore();
}

function strokeLine3D(ctx, p1, p2, rig, cx, cy, scale, color, width) {
  const q1 = project(rotXYZ(p1, rig.rx, rig.ry, rig.rz), cx, cy, scale);
  const q2 = project(rotXYZ(p2, rig.rx, rig.ry, rig.rz), cx, cy, scale);
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(q1.x, q1.y);
  ctx.lineTo(q2.x, q2.y);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
  ctx.restore();
}

// =============================================================================
// 3. INTRO TIMELINE & POSE CALCULATOR (§4, §6)
// =============================================================================
const INTRO = {
  A: [0.0, 0.6],
  B: [0.6, 1.9],
  C: [1.9, 2.4],
  DURATION: 2.4
};

function spinAngle(it) {
  // Piecewise angular speed integral:
  // Phase A: 14 -> 3 rad/s over 0.6s
  const thA = a => 14 * a - (11 / 1.2) * a * a;
  if (it <= 0.6) return thA(it);
  // Phase B: 3 -> 0.6 rad/s over 1.3s
  const s = Math.min(it, 1.9) - 0.6;
  const thB = thA(0.6) + 3 * s - (2.4 / 2.6) * s * s;
  return it <= 1.9 ? thB : thB + 0.6 * (it - 1.9);
}

function introPose(it, R) {
  const A = seg(it, INTRO.A[0], INTRO.A[1]);
  const B = seg(it, INTRO.B[0], INTRO.B[1]);
  const C = seg(it, INTRO.C[0], INTRO.C[1]);

  const spin = spinAngle(it);
  const lock = easeInOutCubic(C);

  let scale;
  if (it < INTRO.C[0]) {
    scale = lerp(0.35, 1.25, easeOutCubic(A)) * lerp(1.0, 0.84, easeInOutCubic(B));
  } else {
    scale = lerp(1.05, 1.0, easeOutBack(C, 1.15));
  }

  const baseRx = lerp(1.35, 0.8, easeInOutCubic(seg(it, 0, 1.9))) + 0.12 * Math.sin(0.9 * it);
  const baseRz = lerp(0.6, 0.55, B) + 0.1 * Math.sin(0.6 * it + 1);
  const wrappedRy = ((spin + Math.PI) % (2 * Math.PI)) - Math.PI;

  return {
    rig: {
      rx: baseRx * (1 - lock),
      ry: wrappedRy * (1 - lock),
      rz: baseRz * (1 - lock)
    },
    scale,
    opacity: clamp(it / 0.12),
    offset: {
      x: lerp(-0.25, 0, easeOutCubic(A)) * R,
      y: lerp(-0.2, 0, easeOutCubic(A)) * R
    },
    laser: 1 - seg(it, 2.0, 2.25),
    gimbalRing: 1 - seg(it, 1.9, 2.25),
    streaks: 1 - seg(it, 0.35, 0.6),
    hud: seg(it, 2.0, 2.4),
    coreDisc: seg(it, 1.95, 2.3),
    flash: Math.exp(-Math.max(0, it - 2.18) * 10) * (it >= 2.18 ? 1.6 : 0),
    glitch: it >= 2.10 && it <= 2.35
  };
}

// =============================================================================
// 4. PROCEDURAL WEB AUDIO INTRO STING (§8)
// =============================================================================
class GhostIntroAudio {
  constructor() {
    this.ctx = null;
    this.played = false;
  }

  play() {
    if (this.played) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!this.ctx) {
        this.ctx = new AudioCtx();
      }
      if (this.ctx.state === 'suspended') {
        this.ctx.resume().catch(() => {});
      }
      if (this.ctx.state !== 'running') {
        const onGesture = () => {
          document.removeEventListener('click', onGesture);
          document.removeEventListener('keydown', onGesture);
          if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume().then(() => this.synthesizeSting()).catch(() => {});
          }
        };
        document.addEventListener('click', onGesture, { once: true });
        document.addEventListener('keydown', onGesture, { once: true });
        return;
      }
      this.synthesizeSting();
    } catch (e) {
      console.warn('GhostIntroAudio play notice:', e);
    }
  }

  synthesizeSting() {
    if (this.played || !this.ctx || this.ctx.state !== 'running') return;
    this.played = true;
    try {
      const now = this.ctx.currentTime;
      const masterGain = this.ctx.createGain();
      masterGain.gain.setValueAtTime(0.22, now); // ~ -13 dBFS
      masterGain.connect(this.ctx.destination);

      // 1. Noise whoosh with resonant bandpass sweep (0.0s -> 2.2s)
      const bufferSize = Math.floor(this.ctx.sampleRate * 2.5);
      const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
      const output = noiseBuffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        output[i] = Math.random() * 2 - 1;
      }
      const whiteNoise = this.ctx.createBufferSource();
      whiteNoise.buffer = noiseBuffer;

      const filter = this.ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.Q.setValueAtTime(3.2, now);
      filter.frequency.setValueAtTime(220, now);
      filter.frequency.exponentialRampToValueAtTime(3800, now + 2.0);

      const noiseGain = this.ctx.createGain();
      noiseGain.gain.setValueAtTime(0.001, now);
      noiseGain.gain.linearRampToValueAtTime(0.18, now + 0.35);
      noiseGain.gain.setValueAtTime(0.18, now + 1.8);
      noiseGain.gain.linearRampToValueAtTime(0.001, now + 2.2);

      whiteNoise.connect(filter);
      filter.connect(noiseGain);
      noiseGain.connect(masterGain);
      whiteNoise.start(now);
      whiteNoise.stop(now + 2.4);

      // 2. Sub thump at lock moment (t ≈ 1.95s -> 2.2s)
      const subOsc = this.ctx.createOscillator();
      const subGain = this.ctx.createGain();
      subOsc.type = 'sine';
      subOsc.frequency.setValueAtTime(65, now + 1.95);
      subOsc.frequency.exponentialRampToValueAtTime(32, now + 2.2);

      subGain.gain.setValueAtTime(0.001, now);
      subGain.gain.setValueAtTime(0.35, now + 1.95);
      subGain.gain.exponentialRampToValueAtTime(0.001, now + 2.25);

      subOsc.connect(subGain);
      subGain.connect(masterGain);
      subOsc.start(now + 1.95);
      subOsc.stop(now + 2.3);

      // 3. High shimmer / chime at lock
      const chimeOsc = this.ctx.createOscillator();
      const chimeGain = this.ctx.createGain();
      chimeOsc.type = 'triangle';
      chimeOsc.frequency.setValueAtTime(2400, now + 2.15);
      chimeOsc.frequency.exponentialRampToValueAtTime(1200, now + 2.35);

      chimeGain.gain.setValueAtTime(0.001, now);
      chimeGain.gain.setValueAtTime(0.12, now + 2.15);
      chimeGain.gain.exponentialRampToValueAtTime(0.001, now + 2.4);

      chimeOsc.connect(chimeGain);
      chimeGain.connect(masterGain);
      chimeOsc.start(now + 2.15);
      chimeOsc.stop(now + 2.45);
    } catch (e) {
      console.warn('GhostIntroAudio synthesis notice:', e);
    }
  }
}

// =============================================================================
// 5. MAIN GHOST ORB CLASS (§9)
// =============================================================================
class GhostOrb {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'IDLE';
    this.amplitude = 0.05;
    this.targetAmplitude = 0.05;
    this.time = 0;
    this.lastFrameTime = performance.now();

    // 6-band audio FFT energy buffer
    this.audioBins = new Float32Array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]);

    // Active emblem text mode ("GHOST" by default per Section 1)
    this.emblemText = 'GHOST';

    // State color palettes (Vibrant neon crimson / magenta cybernetic aesthetic)
    this.palettes = {
      IDLE: {
        coreGlow: '#ff0055',
        neonAccent: '#ff2a70',
        ringSecondary: 'rgba(255, 42, 112, 0.55)',
        ringTertiary: 'rgba(255, 0, 85, 0.25)',
        crosshair: 'rgba(255, 255, 255, 0.45)',
        textCore: '#ffffff',
        textGlow: '#ff0055',
        particleColor: '255, 42, 112',
        speedMult: 1.0,
        bloomAlpha: 0.18
      },
      LISTENING: {
        coreGlow: '#ff0055',
        neonAccent: '#00f0ff',
        ringSecondary: 'rgba(0, 240, 255, 0.75)',
        ringTertiary: 'rgba(255, 42, 112, 0.35)',
        crosshair: 'rgba(0, 240, 255, 0.85)',
        textCore: '#ffffff',
        textGlow: '#ff2a70',
        particleColor: '0, 240, 255',
        speedMult: 1.6,
        bloomAlpha: 0.25
      },
      THINKING: {
        coreGlow: '#c084fc',
        neonAccent: '#e879f9',
        ringSecondary: 'rgba(192, 132, 252, 0.75)',
        ringTertiary: 'rgba(232, 121, 249, 0.35)',
        crosshair: 'rgba(255, 255, 255, 0.7)',
        textCore: '#ffffff',
        textGlow: '#c084fc',
        particleColor: '192, 132, 252',
        speedMult: 2.8,
        bloomAlpha: 0.3
      },
      SPEAKING: {
        coreGlow: '#ff0055',
        neonAccent: '#ffffff',
        ringSecondary: 'rgba(255, 255, 255, 0.85)',
        ringTertiary: 'rgba(255, 42, 112, 0.45)',
        crosshair: 'rgba(255, 255, 255, 0.95)',
        textCore: '#ffffff',
        textGlow: '#ff0055',
        particleColor: '255, 80, 140',
        speedMult: 2.0,
        bloomAlpha: 0.35
      },
      WORKING: {
        coreGlow: '#8a4b2a',
        neonAccent: 'rgba(255, 170, 60, 0.8)',
        ringSecondary: 'rgba(255, 170, 60, 0.4)',
        ringTertiary: 'rgba(255, 0, 85, 0.15)',
        crosshair: 'rgba(255, 200, 120, 0.5)',
        textCore: 'rgba(255, 220, 160, 0.9)',
        textGlow: 'rgba(255, 170, 60, 0.6)',
        particleColor: '255, 170, 60',
        speedMult: 0.6,
        bloomAlpha: 0.12
      },
      INTERRUPTED: {
        coreGlow: '#f59e0b',
        neonAccent: '#fbbf24',
        ringSecondary: 'rgba(245, 158, 11, 0.75)',
        ringTertiary: 'rgba(251, 191, 36, 0.3)',
        crosshair: 'rgba(255, 255, 255, 0.8)',
        textCore: '#ffffff',
        textGlow: '#f59e0b',
        particleColor: '245, 158, 11',
        speedMult: 3.5,
        bloomAlpha: 0.4
      }
    };

    // 55 orbiting micro-particles for settled HUD
    this.particles = [];
    for (let i = 0; i < 55; i++) {
      this.particles.push({
        radiusFactor: 1.1 + Math.random() * 0.95,
        angle: Math.random() * Math.PI * 2,
        angularSpeed: (Math.random() * 0.008 + 0.002) * (Math.random() > 0.5 ? 1 : -1),
        radialOscillationSpeed: Math.random() * 0.03 + 0.01,
        radialAmp: Math.random() * 12 + 4,
        size: Math.random() * 1.8 + 0.8,
        baseAlpha: Math.random() * 0.6 + 0.25,
        phase: Math.random() * Math.PI * 2
      });
    }

    // 25 3D particles for intro gyroscope (§5.3)
    this.introParticles = [];
    for (let i = 0; i < 25; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = (Math.random() - 0.5) * Math.PI;
      const dist = 110 + Math.random() * 100;
      this.introParticles.push({
        x: Math.cos(theta) * Math.cos(phi) * dist,
        y: Math.sin(phi) * dist,
        z: Math.sin(theta) * Math.cos(phi) * dist,
        size: Math.random() * 2.5 + 1.5,
        alpha: Math.random() * 0.5 + 0.4,
        color: Math.random() > 0.3 ? '#ffffff' : '#ff0055'
      });
    }

    // 40 speed streaks for Phase A (§6)
    this.streaks = [];
    for (let i = 0; i < 40; i++) {
      this.streaks.push({
        x: (Math.random() - 0.5) * 360,
        y: (Math.random() - 0.5) * 360,
        len: Math.random() * 140 + 70,
        speed: Math.random() * 800 + 1400,
        alpha: Math.random() * 0.5 + 0.35,
        color: Math.random() > 0.4 ? '#ffffff' : '#ff0055'
      });
    }

    // Rotational offsets for concentric rings
    this.rotOuterTicks = 0;
    this.rotSegmentedTrack = 0;
    this.rotInnerDashes = 0;
    this.rotGearCore = 0;
    this.rotOctagon = 0;

    // Scanline & glitch state
    this.glitchTimer = 0;

    // Audio synthesizer
    this.audio = new GhostIntroAudio();

    // Intro timeline settings
    this.fixedTime = null;
    this.introStart = null;
    this.introDone = false;
    this.onIntroDone = null;

    // Evaluate URL parameters & accessibility preferences
    const params = typeof window !== 'undefined' && window.location ? new URLSearchParams(window.location.search) : new URLSearchParams();
    const skipParam = params.get('intro') === '0';
    const forceParam = params.get('intro') === '1';
    const prefersReducedMotion = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false;
    this.introEnabled = !skipParam && !prefersReducedMotion && (opts.intro !== false);

    const sessionPlayed = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('ghostIntroPlayed') : null;

    if (this.introEnabled && (!sessionPlayed || forceParam)) {
      this.startIntro();
    } else {
      this.skipIntro();
    }

    this.resize();
    if (typeof window !== 'undefined') {
      window.addEventListener('resize', () => this.resize());
      window.ghostOrb = this;
    }

    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  startIntro(force = true) {
    this.introDone = false;
    this.fixedTime = null;
    this.introStart = performance.now();
    if (this.audio) {
      this.audio.played = false;
      this.audio.play();
    }
  }

  skipIntro() {
    this.introDone = true;
    this.fixedTime = null;
    this.introStart = null;
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.setItem('ghostIntroPlayed', '1');
    }
    if (this.onIntroDone) {
      this.onIntroDone();
      this.onIntroDone = null;
    }
  }

  seek(t) {
    this.fixedTime = t;
    this.introDone = t >= INTRO.DURATION;
  }

  resize() {
    // Prefer the canvas's own CSS box (the HUD sizes it compactly, bottom-right,
    // reference aesthetic) and fall back to the window for a fullscreen stage.
    const box = (typeof this.canvas !== 'undefined' && this.canvas.clientWidth > 0)
      ? { w: this.canvas.clientWidth, h: this.canvas.clientHeight }
      : null;
    this.width = box ? box.w : (typeof window !== 'undefined' ? window.innerWidth : 1920);
    this.height = box ? box.h : (typeof window !== 'undefined' ? window.innerHeight : 1080);
    const dpr = typeof window !== 'undefined' ? (window.devicePixelRatio || 1) : 1;
    this.canvas.width = this.width * dpr;
    this.canvas.height = this.height * dpr;
    this.ctx.resetTransform();
    this.ctx.scale(dpr, dpr);
  }

  setState(state) {
    this.state = state || 'IDLE';
    this.glitchTimer = 6;
  }

  setAmplitude(amp) {
    this.targetAmplitude = Math.max(0.02, Math.min(1.0, amp));
  }

  setAudioBins(bins) {
    if (!bins || bins.length < 6) return;
    for (let i = 0; i < 6; i++) {
      this.audioBins[i] = this.audioBins[i] * 0.65 + bins[i] * 0.35;
    }
  }

  setEmblemText(text) {
    if (text) this.emblemText = text.toUpperCase();
  }

  // ===========================================================================
  // 6. VECTOR CYBERNETIC STENCIL TYPOGRAPHY (§5.4)
  // ===========================================================================
  drawStencilText(ctx, cx, cy, baseRadius, palette, extraGlitch = false) {
    ctx.save();
    ctx.translate(cx, cy);

    // Apply digital glitch slice displacement during state change or intro reveal
    const isGlitching = extraGlitch || (this.glitchTimer > 0);
    if (isGlitching) {
      if (this.glitchTimer > 0) this.glitchTimer--;
      const gx = (Math.random() - 0.5) * 6;
      const gy = (Math.random() - 0.5) * 3;
      ctx.translate(gx, gy);
    }

    const scale = (baseRadius / 150) * (1.0 + this.amplitude * 0.08);
    ctx.scale(scale, scale);

    const isSpeaking = this.state === 'SPEAKING';
    const textGlow = isSpeaking ? 32 : (16 + this.amplitude * 16);

    ctx.shadowColor = palette.textGlow;
    ctx.shadowBlur = textGlow;
    ctx.fillStyle = palette.textCore;
    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 1.2;

    const drawGlyph = () => {
      if (this.emblemText === 'ROSE') this._drawRoseGlyph(ctx);
      else this._drawGhostGlyph(ctx);
    };
    drawGlyph();

    // Glitch (reveal / state change only, a few frames): two thin horizontal slices
    // of the wordmark are re-drawn shifted sideways. No bars, no strike-through.
    if (isGlitching) {
      [[-9, 5], [4, 4]].forEach(([y, h]) => {
        ctx.save();
        ctx.beginPath();
        ctx.rect(-80, y, 160, h);
        ctx.clip();
        ctx.translate((Math.random() - 0.5) * 14, 0);
        ctx.globalAlpha *= 0.9;
        drawGlyph();
        ctx.restore();
      });
    }

    ctx.restore();
  }

  _drawRoseGlyph(ctx) {
    // Cybernetic Stencil "ROSE"
    ctx.beginPath();
    ctx.moveTo(-40, 16); ctx.lineTo(-40, -16); ctx.lineTo(-24, -16);
    ctx.lineTo(-20, -12); ctx.lineTo(-20, -2); ctx.lineTo(-25, 0);
    ctx.lineTo(-18, 16); ctx.lineTo(-24, 16); ctx.lineTo(-29, 2);
    ctx.lineTo(-34, 2); ctx.lineTo(-34, 16); ctx.closePath();
    ctx.fill(); ctx.stroke();

    ctx.fillStyle = '#07090e';
    ctx.beginPath();
    ctx.moveTo(-34, -4); ctx.lineTo(-34, -11); ctx.lineTo(-26, -11);
    ctx.lineTo(-25, -9); ctx.lineTo(-25, -6); ctx.lineTo(-27, -4);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(-10, -16); ctx.lineTo(4, -16); ctx.lineTo(8, -8);
    ctx.lineTo(8, 8); ctx.lineTo(4, 16); ctx.lineTo(-10, 16);
    ctx.lineTo(-14, 8); ctx.lineTo(-14, -8); ctx.closePath();
    ctx.fill(); ctx.stroke();

    ctx.fillStyle = '#07090e';
    ctx.beginPath();
    ctx.moveTo(-7, -10); ctx.lineTo(1, -10); ctx.lineTo(3, -5);
    ctx.lineTo(3, 5); ctx.lineTo(1, 10); ctx.lineTo(-7, 10);
    ctx.lineTo(-9, 5); ctx.lineTo(-9, -5); ctx.closePath();
    ctx.fill();

    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(34, -16); ctx.lineTo(18, -16); ctx.lineTo(14, -8);
    ctx.lineTo(28, -5); ctx.lineTo(34, 1); ctx.lineTo(34, 16);
    ctx.lineTo(18, 16); ctx.lineTo(14, 8); ctx.lineTo(20, 8);
    ctx.lineTo(28, 11); ctx.lineTo(28, 0); ctx.lineTo(14, -2);
    ctx.lineTo(14, -16); ctx.lineTo(34, -16); ctx.closePath();
    ctx.fill(); ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(56, -16); ctx.lineTo(38, -16); ctx.lineTo(38, 16);
    ctx.lineTo(56, 16); ctx.lineTo(56, 11); ctx.lineTo(44, 11);
    ctx.lineTo(44, 3); ctx.lineTo(52, 3); ctx.lineTo(52, -2);
    ctx.lineTo(44, -2); ctx.lineTo(44, -11); ctx.lineTo(56, -11);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
  }

  _drawGhostGlyph(ctx) {
    // Custom Hand-Brushed Cybernetic Stencil Wordmark: "GHOST" (§5.4)
    ctx.save();
    // Slanted italic ~ -8 degrees
    ctx.transform(1, 0, Math.tan(-8 * Math.PI / 180), 1, 0, 0);

    // 1. G: Aggressive upper overhang, sharp vertical spine, inward barb
    ctx.beginPath();
    ctx.moveTo(-58, -16); ctx.lineTo(-40, -16); ctx.lineTo(-40, -9);
    ctx.lineTo(-51, -9); ctx.lineTo(-51, 8); ctx.lineTo(-42, 8);
    ctx.lineTo(-42, 0); ctx.lineTo(-46, 0); ctx.lineTo(-46, -6);
    ctx.lineTo(-36, -6); ctx.lineTo(-36, 16); ctx.lineTo(-58, 16);
    ctx.closePath();
    ctx.fill(); ctx.stroke();

    // G splatter barbs
    ctx.beginPath();
    ctx.moveTo(-62, -12); ctx.lineTo(-58, -16); ctx.lineTo(-60, -7); ctx.closePath();
    ctx.moveTo(-56, 16); ctx.lineTo(-62, 21); ctx.lineTo(-53, 17); ctx.closePath();
    ctx.fill();

    // 2. H: Twin cybernetic pillars with angled bridge
    ctx.beginPath();
    ctx.moveTo(-33, -16); ctx.lineTo(-25, -16); ctx.lineTo(-25, -3);
    ctx.lineTo(-17, -3); ctx.lineTo(-17, -16); ctx.lineTo(-9, -16);
    ctx.lineTo(-9, 16); ctx.lineTo(-17, 16); ctx.lineTo(-17, 4);
    ctx.lineTo(-25, 4); ctx.lineTo(-25, 16); ctx.lineTo(-33, 16);
    ctx.closePath();
    ctx.fill(); ctx.stroke();

    // H barbs
    ctx.beginPath();
    ctx.moveTo(-36, -14); ctx.lineTo(-33, -16); ctx.lineTo(-34, -8); ctx.closePath();
    ctx.moveTo(-15, 16); ctx.lineTo(-8, 20); ctx.lineTo(-11, 14); ctx.closePath();
    ctx.fill();

    // 3. O: Hexagonal cyber ring with targeting crosshair aperture
    ctx.beginPath();
    ctx.moveTo(-4, -16); ctx.lineTo(8, -16); ctx.lineTo(14, -5);
    ctx.lineTo(14, 5); ctx.lineTo(8, 16); ctx.lineTo(-4, 16);
    ctx.lineTo(-10, 5); ctx.lineTo(-10, -5); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // O inner hole
    ctx.fillStyle = '#07090e';
    ctx.beginPath();
    ctx.moveTo(-3, -9); ctx.lineTo(4, -9); ctx.lineTo(8, -3);
    ctx.lineTo(8, 3); ctx.lineTo(4, 9); ctx.lineTo(-3, 9);
    ctx.lineTo(-5, 3); ctx.lineTo(-5, -3); ctx.closePath();
    ctx.fill();

    // Center targeting reticle lines intersecting O
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(-12, 0); ctx.lineTo(16, 0);
    ctx.moveTo(2, -18); ctx.lineTo(2, 18);
    ctx.stroke();

    // 4. S: Angular lightning cybernetic S
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(37, -16); ctx.lineTo(21, -16); ctx.lineTo(17, -8);
    ctx.lineTo(31, -5); ctx.lineTo(37, 1); ctx.lineTo(37, 16);
    ctx.lineTo(21, 16); ctx.lineTo(17, 8); ctx.lineTo(23, 8);
    ctx.lineTo(31, 11); ctx.lineTo(31, 0); ctx.lineTo(17, -2);
    ctx.lineTo(17, -16); ctx.lineTo(37, -16); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // S barbs
    ctx.beginPath();
    ctx.moveTo(19, -16); ctx.lineTo(15, -20); ctx.lineTo(20, -12); ctx.closePath();
    ctx.moveTo(37, 16); ctx.lineTo(43, 21); ctx.lineTo(35, 14); ctx.closePath();
    ctx.fill();

    // 5. T: Wide overhang crossbar and central spine
    ctx.beginPath();
    ctx.moveTo(41, -16); ctx.lineTo(64, -16); ctx.lineTo(64, -9);
    ctx.lineTo(57, -9); ctx.lineTo(57, 16); ctx.lineTo(49, 16);
    ctx.lineTo(49, -9); ctx.lineTo(41, -9); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // T barbs
    ctx.beginPath();
    ctx.moveTo(63, -16); ctx.lineTo(68, -13); ctx.lineTo(61, -12); ctx.closePath();
    ctx.moveTo(47, 16); ctx.lineTo(50, 22); ctx.lineTo(53, 16); ctx.closePath();
    ctx.fill();

    // Micro splatter particles
    // NOTE: each dot needs its own moveTo. Chained arcs() get joined by straight
    // lines, and fill() then paints the polygon between them: that was the solid
    // white bar striking through the wordmark.
    ctx.beginPath();
    [[-65, -5, 1.2], [-63, 8, 1.0], [67, 3, 1.3], [65, -9, 0.9]].forEach(([x, y, r]) => {
      ctx.moveTo(x + r, y);
      ctx.arc(x, y, r, 0, Math.PI * 2);
    });
    ctx.fill();

    ctx.restore();
  }

  // ===========================================================================
  // 7. 3D INTRO RENDER PASS (PHASES A, B, C)
  // ===========================================================================
  drawIntro(it, now) {
    const ctx = this.ctx;
    const cx = this.width / 2;
    const cy = this.height / 2 - 20;
    const baseRadius = Math.max(110, Math.min(this.width, this.height) * 0.165);
    const pose = introPose(it, baseRadius);

    const rigCx = cx + pose.offset.x;
    const rigCy = cy + pose.offset.y;
    const scale = pose.scale;

    // 1. Ambient Background Halo
    const haloRadius = baseRadius * 2.2 * scale;
    const haloGrad = ctx.createRadialGradient(rigCx, rigCy, baseRadius * 0.2, rigCx, rigCy, haloRadius);
    haloGrad.addColorStop(0, `rgba(255, 0, 85, ${0.28 * pose.opacity})`);
    haloGrad.addColorStop(0.5, `rgba(255, 42, 112, ${0.12 * pose.opacity})`);
    haloGrad.addColorStop(1, 'rgba(3, 6, 12, 0)');
    ctx.fillStyle = haloGrad;
    ctx.beginPath();
    ctx.arc(rigCx, rigCy, haloRadius, 0, Math.PI * 2);
    ctx.fill();

    // 2. Phase A: Speed Streaks (§6)
    if (pose.streaks > 0.01) {
      ctx.save();
      const streakAngle = 0.52; // ~30° off vertical
      const cosS = Math.cos(streakAngle);
      const sinS = Math.sin(streakAngle);

      for (let i = 0; i < this.streaks.length; i++) {
        const s = this.streaks[i];
        const progress = ((now * 0.001 * s.speed + i * 47) % 700) - 350;
        const sx = rigCx + s.x + progress * cosS;
        const sy = rigCy + s.y + progress * sinS;

        ctx.strokeStyle = s.color;
        ctx.globalAlpha = s.alpha * pose.streaks;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + s.len * cosS, sy + s.len * sinS);
        ctx.stroke();
      }
      ctx.restore();
    }

    // 3. Large Dashed Gimbal Ring (§5.3) - Independent Tilt Plane
    if (pose.gimbalRing > 0.01) {
      const gimbalTilt = { rx: 0.45, ry: -0.6, rz: 0.8 };
      const gimbalSpin = -0.5 * it;
      const rGimbal = baseRadius * 1.38;

      ctx.save();
      ctx.globalAlpha = pose.gimbalRing * pose.opacity;
      strokeArc3D(ctx, {
        radius: rGimbal,
        a0: gimbalSpin,
        a1: gimbalSpin + Math.PI * 2,
        z: 0,
        tilt: gimbalTilt,
        rig: pose.rig,
        cx: rigCx,
        cy: rigCy,
        scale,
        color: 'rgba(255, 255, 255, 0.4)',
        width: 1.2,
        dash: [6, 10],
        steps: 84
      });

      // Accent tick blocks on gimbal ring
      for (let b = 0; b < 6; b++) {
        const bAngle = gimbalSpin + (b / 6) * Math.PI * 2;
        strokeArc3D(ctx, {
          radius: rGimbal,
          a0: bAngle,
          a1: bAngle + 0.12,
          z: 0,
          tilt: gimbalTilt,
          rig: pose.rig,
          cx: rigCx,
          cy: rigCy,
          scale,
          color: (b % 2 === 0) ? '#ff0055' : '#ffffff',
          width: 2.8,
          steps: 8
        });
      }
      ctx.restore();
    }

    // 4. Central Diagonal Laser Axis Line (§5.3)
    if (pose.laser > 0.01) {
      ctx.save();
      ctx.globalAlpha = pose.laser * pose.opacity;
      const laserL = baseRadius * 1.65;
      const laserAngle = 0.65; // ~37° diagonal lower-left to upper-right
      const cosL = Math.cos(laserAngle);
      const sinL = Math.sin(laserAngle);

      const p1 = { x: -laserL * cosL, y: -laserL * sinL, z: 0 };
      const p2 = { x: laserL * cosL, y: laserL * sinL, z: 0 };

      // Outer neon red glow pass
      strokeLine3D(ctx, p1, p2, pose.rig, rigCx, rigCy, scale, 'rgba(255, 0, 85, 0.55)', 4.0);
      // Bright white core line
      strokeLine3D(ctx, p1, p2, pose.rig, rigCx, rigCy, scale, '#ffffff', 1.4);
      ctx.restore();
    }

    // 5. Core Disc (fading in during Phase C)
    if (pose.coreDisc > 0.01) {
      this.drawCoreDisc(ctx, rigCx, rigCy, baseRadius, pose.coreDisc, scale);
    }

    // 6. 3D Gyroscope Rings (§5.3): White Ring B (Rear), Red Ring, White Ring A (Front)
    ctx.save();
    ctx.globalAlpha = pose.opacity;

    // White Ring B (-z offset, ~250° arc)
    strokeArc3D(ctx, {
      radius: baseRadius * 0.52,
      a0: -Math.PI * 0.7,
      a1: Math.PI * 0.7,
      z: -18,
      tilt: null,
      rig: pose.rig,
      cx: rigCx,
      cy: rigCy,
      scale,
      color: 'rgba(255, 255, 255, 0.85)',
      width: 1.5,
      steps: 64
    });

    // Primary Neon Red Ring (Center, thick, multiple glow passes)
    const rPrimary = baseRadius * 0.55;
    // Outer additive bloom pass
    strokeArc3D(ctx, {
      radius: rPrimary,
      a0: 0,
      a1: Math.PI * 2,
      z: 0,
      tilt: null,
      rig: pose.rig,
      cx: rigCx,
      cy: rigCy,
      scale,
      color: 'rgba(255, 0, 85, 0.45)',
      width: 12.0,
      steps: 84
    });
    // Main neon hot stroke
    strokeArc3D(ctx, {
      radius: rPrimary,
      a0: 0,
      a1: Math.PI * 2,
      z: 0,
      tilt: null,
      rig: pose.rig,
      cx: rigCx,
      cy: rigCy,
      scale,
      color: '#ff0055',
      width: 5.5,
      steps: 84
    });
    // Core white-pink highlight
    strokeArc3D(ctx, {
      radius: rPrimary,
      a0: 0,
      a1: Math.PI * 2,
      z: 0,
      tilt: null,
      rig: pose.rig,
      cx: rigCx,
      cy: rigCy,
      scale,
      color: '#ff99bb',
      width: 2.0,
      steps: 84
    });

    // White Ring A (+z offset, ~300° arc)
    strokeArc3D(ctx, {
      radius: baseRadius * 0.58,
      a0: 0.2,
      a1: Math.PI * 2 - 0.4,
      z: 18,
      tilt: null,
      rig: pose.rig,
      cx: rigCx,
      cy: rigCy,
      scale,
      color: '#ffffff',
      width: 1.8,
      steps: 72
    });
    ctx.restore();

    // 7. 3D Floating Particles (§5.3)
    ctx.save();
    ctx.globalAlpha = pose.opacity;
    for (let i = 0; i < this.introParticles.length; i++) {
      const p = this.introParticles[i];
      const q = project(rotXYZ(p, pose.rig.rx, pose.rig.ry, pose.rig.rz), rigCx, rigCy, scale);
      const sz = p.size * q.k;
      ctx.fillStyle = p.color;
      ctx.globalAlpha = p.alpha * pose.opacity;
      ctx.fillRect(q.x - sz / 2, q.y - sz / 2, sz, sz);
    }
    ctx.restore();

    // 8. Settled HUD Layers (staggering in during Phase C, hud > 0)
    if (pose.hud > 0.01) {
      ctx.save();
      ctx.globalAlpha = pose.hud;
      this.drawHudLayers(ctx, rigCx, rigCy, baseRadius, this.palettes[this.state] || this.palettes.IDLE);
      ctx.restore();
    }

    // 9. Wordmark Reveal with Glitch & Flash (§5.4)
    if (pose.coreDisc > 0.3) {
      const logoAlpha = clamp((it - 2.1) / 0.2);
      ctx.save();
      ctx.globalAlpha = logoAlpha;
      this.drawStencilText(ctx, rigCx, rigCy, baseRadius, this.palettes[this.state] || this.palettes.IDLE, pose.glitch);
      ctx.restore();
    }

    // 10. Lock Moment White Flash (§6)
    if (pose.flash > 0.01) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const flashGrad = ctx.createRadialGradient(rigCx, rigCy, 0, rigCx, rigCy, baseRadius * 1.2);
      flashGrad.addColorStop(0, `rgba(255, 255, 255, ${0.85 * pose.flash})`);
      flashGrad.addColorStop(0.4, `rgba(255, 0, 85, ${0.45 * pose.flash})`);
      flashGrad.addColorStop(1, 'rgba(255, 0, 85, 0)');
      ctx.fillStyle = flashGrad;
      ctx.beginPath();
      ctx.arc(rigCx, rigCy, baseRadius * 1.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  // ===========================================================================
  // 8. SETTLED HUD RENDERING PASS (PHASE D & E)
  // ===========================================================================
  drawHud(now) {
    const ctx = this.ctx;
    const cx = this.width / 2;
    const cy = this.height / 2 - 20;
    const baseRadius = Math.max(110, Math.min(this.width, this.height) * 0.165);
    const palette = this.palettes[this.state] || this.palettes.IDLE;
    const speed = palette.speedMult * (1.0 + this.amplitude * 1.5);

    // Advance rotational offsets
    this.rotOuterTicks += 0.0035 * speed;
    this.rotSegmentedTrack += -0.009 * speed;
    this.rotInnerDashes += 0.018 * speed;
    this.rotGearCore += -0.025 * speed;
    this.rotOctagon += 0.0012 * speed;      // very slow drift; keeps the octagon calm

    // 1. Ambient Background Bloom (Audio-Reactive Halo)
    const auraRadius = baseRadius * (2.1 + this.amplitude * 0.4);
    const auraGrad = ctx.createRadialGradient(cx, cy, baseRadius * 0.2, cx, cy, auraRadius);
    auraGrad.addColorStop(0, `rgba(255, 0, 85, ${palette.bloomAlpha * 1.4 * (1.0 + this.amplitude * 0.4)})`);
    auraGrad.addColorStop(0.45, `rgba(255, 42, 112, ${palette.bloomAlpha * 0.7})`);
    auraGrad.addColorStop(0.8, `rgba(180, 0, 60, ${palette.bloomAlpha * 0.2})`);
    auraGrad.addColorStop(1, 'rgba(4, 6, 12, 0)');

    ctx.fillStyle = auraGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, auraRadius, 0, Math.PI * 2);
    ctx.fill();

    // 2. Ambient Orbiting Micro-Particles (Embers)
    ctx.save();
    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      p.angle += p.angularSpeed * speed;
      const r = baseRadius * p.radiusFactor + Math.sin(this.time * p.radialOscillationSpeed + p.phase) * p.radialAmp;
      const px = cx + Math.cos(p.angle) * r;
      const py = cy + Math.sin(p.angle) * r;

      const alpha = p.baseAlpha * (0.6 + Math.sin(this.time * 3 + p.phase) * 0.4);
      ctx.fillStyle = `rgba(${palette.particleColor}, ${alpha})`;
      ctx.shadowColor = `rgba(${palette.particleColor}, 0.8)`;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.arc(px, py, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    // 3. Shared HUD Layer Passes
    this.drawHudLayers(ctx, cx, cy, baseRadius, palette);

    // 4. Core Disk (shared with the intro so the hand-off has no pop)
    this.drawCoreDisc(ctx, cx, cy, baseRadius, 1.0);

    // 5. Stylized Cybernetic Stencil Typography ("GHOST")
    this.drawStencilText(ctx, cx, cy, baseRadius, palette);
  }

  drawCoreDisc(ctx, cx, cy, baseRadius, alpha = 1.0, scale = 1.0) {
    // Dark glassy centre so the white wordmark pops; crimson energy only builds
    // toward the rim (like light caught on a lens) and swells with speech.
    const r = baseRadius * 0.74 * scale;
    const a = this.amplitude;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0.00, `rgba(12, 3, 10, ${0.90 * alpha})`);
    g.addColorStop(0.55, `rgba(38, 5, 22, ${0.86 * alpha})`);
    g.addColorStop(0.86, `rgba(110, 8, 50, ${(0.62 + a * 0.2) * alpha})`);
    g.addColorStop(1.00, `rgba(255, 0, 85, ${(0.30 + a * 0.25) * alpha})`);
    ctx.save();
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  drawHudLayers(ctx, cx, cy, baseRadius, palette) {
    // -------------------------------------------------------------
    // A. OUTER TELEMETRY DIAL & COMPASS TICKS (R ~ 1.52x)
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotOuterTicks);

    const rOuter = baseRadius * 1.52;
    ctx.strokeStyle = palette.ringTertiary;
    ctx.lineWidth = 1.0;
    ctx.beginPath();
    ctx.arc(0, 0, rOuter, 0, Math.PI * 2);
    ctx.stroke();

    // Radial degree ticks around circumference (72 ticks)
    const tickCount = 72;
    for (let t = 0; t < tickCount; t++) {
      const angle = (t / tickCount) * Math.PI * 2;
      const isMajor = (t % 6 === 0);
      const isCardinal = (t % 18 === 0);
      const len = isCardinal ? 12 : (isMajor ? 8 : 4);

      const r1 = rOuter - len;
      const r2 = rOuter;

      const x1 = Math.cos(angle) * r1;
      const y1 = Math.sin(angle) * r1;
      const x2 = Math.cos(angle) * r2;
      const y2 = Math.sin(angle) * r2;

      ctx.beginPath();
      ctx.strokeStyle = isMajor ? palette.neonAccent : palette.ringSecondary;
      ctx.lineWidth = isMajor ? 1.5 : 0.8;
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }

    // Rotating telemetry scan arc
    ctx.beginPath();
    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 2.5;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 12;
    ctx.arc(0, 0, rOuter, 0, Math.PI * 0.28);
    ctx.stroke();
    ctx.restore();

    // -------------------------------------------------------------
    // B. CORNER TACTICAL RETICLE BRACKETS & CARDINAL CROSSHAIRS
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);

    // Fixed cardinal crosshair guides
    const chLen = baseRadius * 1.68;
    ctx.strokeStyle = palette.crosshair;
    ctx.lineWidth = 0.9;
    ctx.setLineDash([4, 6]);

    ctx.beginPath();
    ctx.moveTo(-chLen, 0); ctx.lineTo(-baseRadius * 1.25, 0);
    ctx.moveTo(baseRadius * 1.25, 0); ctx.lineTo(chLen, 0);
    ctx.moveTo(0, -chLen); ctx.lineTo(0, -baseRadius * 1.25);
    ctx.moveTo(0, baseRadius * 1.25); ctx.lineTo(0, chLen);
    ctx.stroke();
    ctx.setLineDash([]);

    // Corner targeting reticle brackets at 45°, 135°, 225°, 315°
    const brR = baseRadius * 1.44;
    const brAngles = [Math.PI * 0.25, Math.PI * 0.75, Math.PI * 1.25, Math.PI * 1.75];
    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 1.6;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 8;

    brAngles.forEach((a) => {
      ctx.beginPath();
      ctx.arc(0, 0, brR, a - 0.08, a + 0.08);
      ctx.stroke();
    });
    ctx.restore();

    // -------------------------------------------------------------
    // C. COUNTER-ROTATING SEGMENTED ARC TRACKS
    // -------------------------------------------------------------
    // Track A: Clockwise heavy segmented arc ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotSegmentedTrack);

    const rTrackA = baseRadius * 1.32;
    const segsA = 4;
    for (let s = 0; s < segsA; s++) {
      const startAngle = (s / segsA) * Math.PI * 2;
      const endAngle = startAngle + (Math.PI * 2 / segsA) * 0.68;

      ctx.beginPath();
      ctx.strokeStyle = palette.neonAccent;
      ctx.lineWidth = 2.4;
      ctx.shadowColor = palette.coreGlow;
      ctx.shadowBlur = 10;
      ctx.arc(0, 0, rTrackA, startAngle, endAngle);
      ctx.stroke();

      // Accent dot at end of arc
      const dotX = Math.cos(endAngle) * rTrackA;
      const dotY = Math.sin(endAngle) * rTrackA;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(dotX, dotY, 2.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    // Track B: Counter-clockwise dashed track
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotInnerDashes);

    const rTrackB = baseRadius * 1.18;
    ctx.strokeStyle = palette.ringSecondary;
    ctx.lineWidth = 1.6;
    ctx.setLineDash([6, 9]);
    ctx.beginPath();
    ctx.arc(0, 0, rTrackB, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    // -------------------------------------------------------------
    // D. AUDIO-REACTIVE OCTAGON (R ~ 0.98x)
    // A true regular octagon: straight edges, softly rounded corners. Audio no
    // longer makes the outline lumpy; it nudges each corner outward a little,
    // brightens the glow and stretches the edge-centre ticks.
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotOctagon || 0);

    const oR = baseRadius * 0.98;               // circumradius (centre → corner)
    const amp = this.amplitude;
    const corner = [];
    for (let k = 0; k < 8; k++) {
      const ang = Math.PI / 8 + k * (Math.PI / 4);   // flat edges at top/bottom/left/right
      const bin = this.audioBins[k % 6] || 0.1;
      const rr = oR * (1 + bin * amp * 0.07);
      corner.push({ x: Math.cos(ang) * rr, y: Math.sin(ang) * rr, ang });
    }

    // Outline with rounded corners (arcTo between midpoints keeps edges perfectly straight)
    const cornerRound = baseRadius * 0.07;
    ctx.beginPath();
    for (let k = 0; k < 8; k++) {
      const p0 = corner[k], p1 = corner[(k + 1) % 8];
      const mid = { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 };
      if (k === 0) {
        const pm = { x: (corner[7].x + p0.x) / 2, y: (corner[7].y + p0.y) / 2 };
        ctx.moveTo(pm.x, pm.y);
      }
      ctx.arcTo(p0.x, p0.y, mid.x, mid.y, cornerRound);
    }
    ctx.closePath();

    // glow pass + crisp pass
    ctx.strokeStyle = palette.ringSecondary;
    ctx.lineWidth = 4;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 14 + amp * 18;
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // Corner nodes (small squares) and edge-centre ticks
    for (let k = 0; k < 8; k++) {
      const c = corner[k];
      ctx.save();
      ctx.translate(c.x, c.y);
      ctx.rotate(c.ang);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(-1.8, -1.8, 3.6, 3.6);
      ctx.restore();

      const mAng = k * (Math.PI / 4);             // edge-centre direction
      const apothem = oR * Math.cos(Math.PI / 8);
      const bin = this.audioBins[k % 6] || 0.1;
      const tickLen = 5 + bin * 14 * (amp * 1.5 + 0.3);
      ctx.beginPath();
      ctx.strokeStyle = palette.neonAccent;
      ctx.lineWidth = 1.6;
      ctx.moveTo(Math.cos(mAng) * apothem, Math.sin(mAng) * apothem);
      ctx.lineTo(Math.cos(mAng) * (apothem + tickLen), Math.sin(mAng) * (apothem + tickLen));
      ctx.stroke();
    }
    ctx.restore();

    // -------------------------------------------------------------
    // E. GLOWING NEON CORE RIM & NOTCHED GEAR RING (R ~ 0.78x)
    // Pulses strongly with speech audio amplitude (§5.2, §6)
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotGearCore);

    const rCoreRim = baseRadius * 0.78;
    const coreLineWidth = 3.0 * (1.0 + this.amplitude * 0.5);

    ctx.beginPath();
    ctx.strokeStyle = palette.coreGlow;
    ctx.lineWidth = coreLineWidth;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 24 + this.amplitude * 24;
    ctx.arc(0, 0, rCoreRim, 0, Math.PI * 2);
    ctx.stroke();

    // Notched gear ticks on the core rim (24 notches)
    const gearCount = 24;
    for (let g = 0; g < gearCount; g++) {
      const gAngle = (g / gearCount) * Math.PI * 2;
      const gx1 = Math.cos(gAngle) * (rCoreRim - 4);
      const gy1 = Math.sin(gAngle) * (rCoreRim - 4);
      const gx2 = Math.cos(gAngle) * (rCoreRim + 4);
      const gy2 = Math.sin(gAngle) * (rCoreRim + 4);

      ctx.beginPath();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.2;
      ctx.moveTo(gx1, gy1);
      ctx.lineTo(gx2, gy2);
      ctx.stroke();
    }
    ctx.restore();
  }

  // ===========================================================================
  // 9. ANIMATION LOOP & DETERMINISTIC SEEK ROUTER
  // ===========================================================================
  animate(now) {
    const dt = Math.min(0.1, (now - this.lastFrameTime) / 1000 || 0.016);
    this.lastFrameTime = now;
    this.time += dt * 1.5;

    // Smooth amplitude interpolation (250ms return to idle on silence)
    this.amplitude += (this.targetAmplitude - this.amplitude) * 0.15;

    // Clear canvas
    this.ctx.clearRect(0, 0, this.width, this.height);

    // Calculate elapsed intro time
    const it = this.fixedTime !== null ? this.fixedTime : (this.introStart !== null ? (now - this.introStart) / 1000 : Infinity);

    if (it < INTRO.DURATION && !this.introDone) {
      this.drawIntro(it, now);
    } else {
      if (!this.introDone) {
        this.introDone = true;
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.setItem('ghostIntroPlayed', '1');
        }
        if (this.onIntroDone) {
          this.onIntroDone();
          this.onIntroDone = null;
        }
      }
      this.drawHud(now);
    }

    requestAnimationFrame(this.animate);
  }
}

// Export to window global
if (typeof window !== 'undefined') {
  window.GhostOrb = GhostOrb;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { GhostOrb, INTRO, introPose, rotXYZ, project, clamp, lerp };
}
