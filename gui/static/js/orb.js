/**
 * Ghost / Rose Multi-Filament Harmonized Orbital Wave Sphere
 * Renders the exact visual signature from CONTEXT.md Section 1:
 * - Radiant white-hot core (#ffffff -> electric cyan #93c5fd)
 * - Concentric outer halos (#1e295d -> #3b42c4)
 * - Inner cyan aura (#525ceb -> #60a5fa)
 * - 6 intertwined harmonic sinusoidal orbital filaments responding to live audio FFT bins:
 *   r(theta) = R_base + sum_{k=1}^3 A_k * sin(n_k * theta + omega_k * t + phi_k)
 */
class GhostOrb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'IDLE';
    this.amplitude = 0.05;
    this.time = 0;
    // FFT frequency bins array for live reactivity
    this.audioBins = new Float32Array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]);

    // Color palettes mapped strictly to CONTEXT.md Section 1
    this.palettes = {
      IDLE: {
        core: '#ffffff',
        cyanAura: 'rgba(96, 165, 250, 0.85)',
        innerAura: 'rgba(82, 92, 235, 0.65)',
        midHalo: 'rgba(59, 66, 196, 0.65)',     // #3b42c4 (65% opacity)
        outerHalo: 'rgba(30, 41, 93, 0.30)',     // #1e295d (30% opacity)
        filaments: [147, 197, 253]               // #93c5fd
      },
      LISTENING: {
        core: '#ffffff',
        cyanAura: 'rgba(6, 182, 212, 0.9)',
        innerAura: 'rgba(14, 165, 233, 0.75)',
        midHalo: 'rgba(3, 105, 161, 0.65)',
        outerHalo: 'rgba(8, 47, 73, 0.35)',
        filaments: [56, 189, 248]
      },
      THINKING: {
        core: '#ffffff',
        cyanAura: 'rgba(192, 132, 252, 0.9)',
        innerAura: 'rgba(147, 51, 234, 0.7)',
        midHalo: 'rgba(107, 33, 168, 0.65)',
        outerHalo: 'rgba(59, 7, 100, 0.35)',
        filaments: [216, 180, 254]
      },
      SPEAKING: {
        core: '#ffffff',
        cyanAura: 'rgba(147, 197, 253, 0.95)',
        innerAura: 'rgba(82, 92, 235, 0.8)',
        midHalo: 'rgba(59, 66, 196, 0.75)',
        outerHalo: 'rgba(30, 41, 93, 0.40)',
        filaments: [147, 197, 253]
      },
      INTERRUPTED: {
        core: '#ffffff',
        cyanAura: 'rgba(251, 191, 36, 0.9)',
        innerAura: 'rgba(217, 119, 6, 0.75)',
        midHalo: 'rgba(180, 83, 9, 0.65)',
        outerHalo: 'rgba(69, 26, 3, 0.35)',
        filaments: [252, 211, 77]
      }
    };

    // Filament configuration: 6 intertwined harmonic orbitals
    this.filamentParams = [
      { order1: 3, order2: 5, speed: 0.9,  phase: 0.0,              radiusFactor: 1.14 },
      { order1: 4, order2: 2, speed: -0.75, phase: Math.PI / 3,      radiusFactor: 1.18 },
      { order1: 5, order2: 3, speed: 1.1,  phase: (2 * Math.PI) / 3, radiusFactor: 1.22 },
      { order1: 2, order2: 4, speed: -0.85, phase: Math.PI,          radiusFactor: 1.26 },
      { order1: 4, order2: 6, speed: 1.05, phase: (4 * Math.PI) / 3, radiusFactor: 1.30 },
      { order1: 3, order2: 2, speed: -1.2, phase: (5 * Math.PI) / 3, radiusFactor: 1.34 }
    ];

    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  resize() {
    this.width = window.innerWidth;
    this.height = window.innerHeight;
    this.canvas.width = this.width * window.devicePixelRatio;
    this.canvas.height = this.height * window.devicePixelRatio;
    this.ctx.resetTransform();
    this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
  }

  setState(state) {
    this.state = state || 'IDLE';
  }

  setAmplitude(amp) {
    // Smooth live RMS reactivity
    this.amplitude = this.amplitude * 0.75 + amp * 0.25;
  }

  setAudioBins(bins) {
    if (!bins || bins.length < 6) return;
    for (let i = 0; i < 6; i++) {
      this.audioBins[i] = this.audioBins[i] * 0.7 + bins[i] * 0.3;
    }
  }

  animate() {
    this.time += 0.022;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    const cx = this.width / 2;
    const cy = this.height / 2;
    // Base radius derived from viewport
    const baseRadius = Math.min(this.width, this.height) * 0.175;
    const palette = this.palettes[this.state] || this.palettes.IDLE;
    const [fr, fg, fb] = palette.filaments;

    // 1. Concentric Outer Ambient Halo (#1e295d at 30% opacity extending to 1.8x base radius)
    const outerHaloRadius = baseRadius * (1.8 + this.amplitude * 0.3);
    const outerHaloGrad = ctx.createRadialGradient(cx, cy, baseRadius * 0.3, cx, cy, outerHaloRadius);
    outerHaloGrad.addColorStop(0, palette.midHalo);
    outerHaloGrad.addColorStop(0.55, palette.outerHalo);
    outerHaloGrad.addColorStop(1, 'rgba(2, 4, 8, 0)');

    ctx.fillStyle = outerHaloGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, outerHaloRadius, 0, Math.PI * 2);
    ctx.fill();

    // 2. Concentric Inner Halo (#3b42c4 at 65% opacity) & Inner Cyan Aura (#525ceb -> #60a5fa)
    const innerHaloRadius = baseRadius * (1.15 + this.amplitude * 0.2);
    const innerHaloGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, innerHaloRadius);
    innerHaloGrad.addColorStop(0, palette.cyanAura);
    innerHaloGrad.addColorStop(0.45, palette.innerAura);
    innerHaloGrad.addColorStop(0.85, palette.midHalo);
    innerHaloGrad.addColorStop(1, 'rgba(5, 8, 17, 0)');

    ctx.fillStyle = innerHaloGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, innerHaloRadius, 0, Math.PI * 2);
    ctx.fill();

    // 3. The 6 Intertwined Harmonic Filaments (CONTEXT.md Section 1.1)
    // Formula: r(theta) = R_base + sum_{k=1}^3 A_k * sin(n_k * theta + omega_k * t + phi_k)
    const pointsPerLoop = 180;
    const isSpeakingOrListening = (this.state === 'SPEAKING' || this.state === 'LISTENING');

    for (let f = 0; f < 6; f++) {
      const p = this.filamentParams[f];
      const binEnergy = this.audioBins[f] || 0.1;
      // High-frequency organic deformation responding to FFT frequency bins
      const dynamicAmp1 = (isSpeakingOrListening ? (16 + binEnergy * 42) : 10) * (1.0 + this.amplitude * 1.2);
      const dynamicAmp2 = (isSpeakingOrListening ? (10 + binEnergy * 28) : 6);
      const dynamicAmp3 = (isSpeakingOrListening ? (6 + binEnergy * 16) : 3);

      const fBaseRadius = baseRadius * p.radiusFactor;

      ctx.beginPath();
      for (let i = 0; i <= pointsPerLoop; i++) {
        const theta = (i / pointsPerLoop) * Math.PI * 2;

        // 3 harmonic sinusoidal components
        const h1 = Math.sin(p.order1 * theta + p.speed * this.time + p.phase);
        const h2 = Math.sin(p.order2 * theta - (p.speed * 0.7) * this.time + p.phase * 1.5);
        const h3 = Math.cos((p.order1 + p.order2) * theta + this.time * 0.5);

        const r = fBaseRadius + (h1 * dynamicAmp1) + (h2 * dynamicAmp2) + (h3 * dynamicAmp3);
        const x = cx + Math.cos(theta) * r;
        const y = cy + Math.sin(theta) * r;

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();

      // Delicate electric-blue / state glowing stroke
      const filamentAlpha = 0.55 + Math.sin(this.time * 2.5 + f * 1.1) * 0.2;
      ctx.strokeStyle = `rgba(${fr}, ${fg}, ${fb}, ${filamentAlpha})`;
      ctx.lineWidth = 1.35;
      ctx.shadowColor = `rgba(${fr}, ${fg}, ${fb}, 0.85)`;
      ctx.shadowBlur = 12;
      ctx.stroke();
    }

    // 4. Radiant White-Hot Core (scales smoothly with live RMS volume 1.0x to 1.35x)
    const coreScale = 1.0 + Math.min(0.35, this.amplitude * 0.35);
    const coreRadius = baseRadius * 0.32 * coreScale;

    const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreRadius);
    coreGrad.addColorStop(0, '#ffffff');
    coreGrad.addColorStop(0.35, '#ffffff');
    coreGrad.addColorStop(0.70, '#93c5fd'); // Electric cyan transition
    coreGrad.addColorStop(1, 'rgba(147, 197, 253, 0)');

    ctx.shadowColor = '#ffffff';
    ctx.shadowBlur = 24 + this.amplitude * 18;
    ctx.fillStyle = coreGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, coreRadius, 0, Math.PI * 2);
    ctx.fill();

    // Reset shadow blur
    ctx.shadowBlur = 0;

    requestAnimationFrame(this.animate);
  }
}

window.GhostOrb = GhostOrb;
