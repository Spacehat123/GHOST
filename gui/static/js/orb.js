/**
 * Cybernetic Animated HUD Emblem Engine for Ghost / Rose
 * Reconstructs the exact visual signature from ghost-intro-11s.mp4:
 * - Multi-tiered counter-rotating concentric segmented geometric rings
 * - Radial compass / targeting reticle dial with degree ticks and crosshairs
 * - Live audio-reactive waveform / equalizer ring modulating with speech FFT
 * - Intense glowing neon magenta / crimson core with multi-stage bloom
 * - Stylized cybernetic stencil typography ("ROSE" / "GHOST")
 * - Ambient micro-particle embers with orbital physics
 */
class GhostOrb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'IDLE';
    this.amplitude = 0.05;
    this.targetAmplitude = 0.05;
    this.time = 0;

    // 6-band audio FFT energy buffer
    this.audioBins = new Float32Array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]);

    // Active emblem text mode ("ROSE" by default per reference video)
    this.emblemText = 'ROSE';

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
        neonAccent: '#00f0ff', // Electric cyan accents in reticle when listening
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
        coreGlow: '#c084fc', // Purple / high computational frequency
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
      INTERRUPTED: {
        coreGlow: '#f59e0b', // Amber / gold flare
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

    // Initialize 55 orbiting micro-particles (embers)
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

    // Rotational offsets for concentric rings
    this.rotOuterTicks = 0;
    this.rotSegmentedTrack = 0;
    this.rotInnerDashes = 0;
    this.rotGearCore = 0;

    // Scanline & glitch state
    this.glitchTimer = 0;
    this.glitchOffset = 0;

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
    this.glitchTimer = 6; // Trigger slight digital strobe on state change
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

  /**
   * Draws pure vector stylized cybernetic stencil typography ("ROSE" or "GHOST")
   */
  drawStencilText(ctx, cx, cy, baseRadius, palette) {
    ctx.save();
    ctx.translate(cx, cy);

    // Apply glitch displacement on audio spikes or state transitions
    if (this.glitchTimer > 0) {
      this.glitchTimer--;
      const gx = (Math.random() - 0.5) * 4;
      const gy = (Math.random() - 0.5) * 2;
      ctx.translate(gx, gy);
    }

    const scale = (baseRadius / 150) * (1.0 + this.amplitude * 0.08);
    ctx.scale(scale, scale);

    const isSpeaking = this.state === 'SPEAKING';
    const textGlow = isSpeaking ? 30 : (16 + this.amplitude * 14);

    ctx.shadowColor = palette.textGlow;
    ctx.shadowBlur = textGlow;
    ctx.fillStyle = palette.textCore;
    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 1.2;

    if (this.emblemText === 'ROSE') {
      this._drawRoseGlyph(ctx);
    } else {
      this._drawGhostGlyph(ctx);
    }

    // Horizontal cybernetic cut/slash overlay line
    ctx.beginPath();
    ctx.strokeStyle = '#06070a';
    ctx.lineWidth = 2.2;
    ctx.moveTo(-50, 0);
    ctx.lineTo(50, 0);
    ctx.stroke();

    ctx.restore();
  }

  _drawRoseGlyph(ctx) {
    // Cybernetic Stencil "ROSE" Vector Construction
    // R [-40, -18]
    ctx.beginPath();
    ctx.moveTo(-40, 16); ctx.lineTo(-40, -16); ctx.lineTo(-24, -16);
    ctx.lineTo(-20, -12); ctx.lineTo(-20, -2); ctx.lineTo(-25, 0);
    ctx.lineTo(-18, 16); ctx.lineTo(-24, 16); ctx.lineTo(-29, 2);
    ctx.lineTo(-34, 2); ctx.lineTo(-34, 16); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // R inner hole
    ctx.fillStyle = '#07090e';
    ctx.beginPath();
    ctx.moveTo(-34, -4); ctx.lineTo(-34, -11); ctx.lineTo(-26, -11);
    ctx.lineTo(-25, -9); ctx.lineTo(-25, -6); ctx.lineTo(-27, -4);
    ctx.closePath();
    ctx.fill();

    // O [-14, 8] (Hexagonal cybernetic ring)
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(-10, -16); ctx.lineTo(4, -16); ctx.lineTo(8, -8);
    ctx.lineTo(8, 8); ctx.lineTo(4, 16); ctx.lineTo(-10, 16);
    ctx.lineTo(-14, 8); ctx.lineTo(-14, -8); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // O inner hole
    ctx.fillStyle = '#07090e';
    ctx.beginPath();
    ctx.moveTo(-7, -10); ctx.lineTo(1, -10); ctx.lineTo(3, -5);
    ctx.lineTo(3, 5); ctx.lineTo(1, 10); ctx.lineTo(-7, 10);
    ctx.lineTo(-9, 5); ctx.lineTo(-9, -5); ctx.closePath();
    ctx.fill();

    // S [14, 34] (Angular cybernetic S)
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(34, -16); ctx.lineTo(18, -16); ctx.lineTo(14, -8);
    ctx.lineTo(28, -5); ctx.lineTo(34, 1); ctx.lineTo(34, 16);
    ctx.lineTo(18, 16); ctx.lineTo(14, 8); ctx.lineTo(20, 8);
    ctx.lineTo(28, 11); ctx.lineTo(28, 0); ctx.lineTo(14, -2);
    ctx.lineTo(14, -16); ctx.lineTo(34, -16); ctx.closePath();
    ctx.fill(); ctx.stroke();

    // E [38, 56]
    ctx.beginPath();
    ctx.moveTo(56, -16); ctx.lineTo(38, -16); ctx.lineTo(38, 16);
    ctx.lineTo(56, 16); ctx.lineTo(56, 11); ctx.lineTo(44, 11);
    ctx.lineTo(44, 3); ctx.lineTo(52, 3); ctx.lineTo(52, -2);
    ctx.lineTo(44, -2); ctx.lineTo(44, -11); ctx.lineTo(56, -11);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
  }

  _drawGhostGlyph(ctx) {
    ctx.font = '900 24px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('GHOST', 0, 0);
    ctx.strokeText('GHOST', 0, 0);
  }

  animate() {
    this.time += 0.024;
    // Smooth amplitude interpolation
    this.amplitude += (this.targetAmplitude - this.amplitude) * 0.15;

    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    const cx = this.width / 2;
    // Optical center slightly raised like reference monitor
    const cy = this.height / 2 - 20;

    // Scale responsive base radius
    const baseRadius = Math.max(110, Math.min(this.width, this.height) * 0.165);
    const palette = this.palettes[this.state] || this.palettes.IDLE;
    const speed = palette.speedMult;

    // Advance ring rotation angles at differentiated velocities
    this.rotOuterTicks += 0.0035 * speed;
    this.rotSegmentedTrack += -0.009 * speed;
    this.rotInnerDashes += 0.018 * speed;
    this.rotGearCore += -0.025 * speed;

    // -------------------------------------------------------------
    // 1. AMBIENT BACKGROUND BLOOM (Deep crimson/magenta radial aura)
    // -------------------------------------------------------------
    const auraRadius = baseRadius * (2.1 + this.amplitude * 0.4);
    const auraGrad = ctx.createRadialGradient(cx, cy, baseRadius * 0.2, cx, cy, auraRadius);
    auraGrad.addColorStop(0, `rgba(255, 0, 85, ${palette.bloomAlpha * 1.4})`);
    auraGrad.addColorStop(0.45, `rgba(255, 42, 112, ${palette.bloomAlpha * 0.7})`);
    auraGrad.addColorStop(0.8, `rgba(180, 0, 60, ${palette.bloomAlpha * 0.2})`);
    auraGrad.addColorStop(1, 'rgba(4, 6, 12, 0)');

    ctx.fillStyle = auraGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, auraRadius, 0, Math.PI * 2);
    ctx.fill();

    // -------------------------------------------------------------
    // 2. AMBIENT ORBITING MICRO-PARTICLES (Embers)
    // -------------------------------------------------------------
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

    // -------------------------------------------------------------
    // 3. OUTER TELEMETRY DIAL & COMPASS TICKS (R ~ 1.55x)
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
    // 4. CORNER TACTICAL HUD BRACKETS & CARDINAL CROSSHAIRS
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);

    // Fixed cardinal crosshair guides
    const chLen = baseRadius * 1.68;
    ctx.strokeStyle = palette.crosshair;
    ctx.lineWidth = 0.9;
    ctx.setLineDash([4, 6]);

    // Horizontal & Vertical axes
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
      const bx = Math.cos(a) * brR;
      const by = Math.sin(a) * brR;
      ctx.beginPath();
      ctx.arc(0, 0, brR, a - 0.08, a + 0.08);
      ctx.stroke();
    });
    ctx.restore();

    // -------------------------------------------------------------
    // 5. COUNTER-ROTATING SEGMENTED ARC TRACKS (R ~ 1.25x - 1.35x)
    // -------------------------------------------------------------
    // Track A: Segmented clockwise heavy arc ring
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

      // Small accent dot at end of arc
      const dotX = Math.cos(endAngle) * rTrackA;
      const dotY = Math.sin(endAngle) * rTrackA;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(dotX, dotY, 2.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    // Track B: Segmented counter-clockwise dashed track
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
    // 6. AUDIO-REACTIVE EQUALIZER WAVE RING (R ~ 0.95x - 1.1x)
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);

    const rWaveBase = baseRadius * 0.98;
    const wavePoints = 64;
    ctx.beginPath();
    for (let w = 0; w <= wavePoints; w++) {
      const theta = (w / wavePoints) * Math.PI * 2;
      const binIdx = Math.floor((w / wavePoints) * 6) % 6;
      const binAmp = this.audioBins[binIdx] || 0.1;

      // Harmonic sine ripple + live audio FFT displacement
      const ripple = Math.sin(theta * 8 + this.time * 4) * 3;
      const audioDisp = binAmp * 26 * (this.amplitude * 1.8 + 0.2);
      const r = rWaveBase + ripple + audioDisp;

      const wx = Math.cos(theta) * r;
      const wy = Math.sin(theta) * r;

      if (w === 0) ctx.moveTo(wx, wy);
      else ctx.lineTo(wx, wy);
    }
    ctx.closePath();

    ctx.strokeStyle = palette.neonAccent;
    ctx.lineWidth = 1.4;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 14 + this.amplitude * 16;
    ctx.stroke();

    // Radiating radial equalizer pips
    const pipCount = 32;
    for (let p = 0; p < pipCount; p++) {
      const pAngle = (p / pipCount) * Math.PI * 2;
      const pBin = this.audioBins[p % 6] || 0.1;
      const pipH = 3 + pBin * 18 * (this.amplitude * 1.5 + 0.3);

      const x1 = Math.cos(pAngle) * rWaveBase;
      const y1 = Math.sin(pAngle) * rWaveBase;
      const x2 = Math.cos(pAngle) * (rWaveBase + pipH);
      const y2 = Math.sin(pAngle) * (rWaveBase + pipH);

      ctx.beginPath();
      ctx.strokeStyle = palette.neonAccent;
      ctx.lineWidth = 1.2;
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    ctx.restore();

    // -------------------------------------------------------------
    // 7. GLOWING NEON CORE RIM & NOTCHED GEAR RING (R ~ 0.78x)
    // -------------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.rotGearCore);

    const rCoreRim = baseRadius * 0.78;

    // Glowing core boundary ring
    ctx.beginPath();
    ctx.strokeStyle = palette.coreGlow;
    ctx.lineWidth = 3.0;
    ctx.shadowColor = palette.coreGlow;
    ctx.shadowBlur = 24 + this.amplitude * 18;
    ctx.arc(0, 0, rCoreRim, 0, Math.PI * 2);
    ctx.stroke();

    // High-speed notched gear ticks on the core rim (24 notches)
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

    // -------------------------------------------------------------
    // 8. INNER GLOW DISK & SPECULAR HIGHLIGHT
    // -------------------------------------------------------------
    const coreDiskR = baseRadius * 0.74;
    const coreDiskGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreDiskR);
    coreDiskGrad.addColorStop(0, 'rgba(255, 0, 85, 0.45)');
    coreDiskGrad.addColorStop(0.65, 'rgba(255, 42, 112, 0.22)');
    coreDiskGrad.addColorStop(0.95, 'rgba(10, 14, 24, 0.85)');
    coreDiskGrad.addColorStop(1, '#06070a');

    ctx.fillStyle = coreDiskGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, coreDiskR, 0, Math.PI * 2);
    ctx.fill();

    // -------------------------------------------------------------
    // 9. STYLIZED CYBERNETIC STENCIL TYPOGRAPHY ("ROSE" / "GHOST")
    // -------------------------------------------------------------
    this.drawStencilText(ctx, cx, cy, baseRadius, palette);

    // Continue high-performance render loop
    requestAnimationFrame(this.animate);
  }
}

window.GhostOrb = GhostOrb;
