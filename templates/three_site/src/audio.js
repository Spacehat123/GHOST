// Procedural Web Audio Synthesizer (Zero Asset / Zero License Risk)
export class ProceduralAudioManager {
    constructor() {
        this.ctx = null;
        this.isMuted = true; // Rule: Muted by default
        this.ambientGain = null;
        this.activeHoverOsc = null; // Rule: No overlapping hover sounds
        this.isTransitioning = false; // Rule: Single transition sound per move
    }

    init() {
        if (!this.ctx) {
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (AudioCtx) {
                this.ctx = new AudioCtx();
            }
        }
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    }

    toggle() {
        this.init();
        this.isMuted = !this.isMuted;
        if (this.isMuted) {
            this.stopAmbient();
        } else {
            this.startAmbient();
        }
        return !this.isMuted;
    }

    startAmbient() {
        if (this.isMuted || !this.ctx) return;
        this.stopAmbient();

        const osc1 = this.ctx.createOscillator();
        const osc2 = this.ctx.createOscillator();
        const filter = this.ctx.createBiquadFilter();
        this.ambientGain = this.ctx.createGain();

        osc1.type = 'sine';
        osc1.frequency.setValueAtTime(110.0, this.ctx.currentTime);

        osc2.type = 'triangle';
        osc2.frequency.setValueAtTime(165.5, this.ctx.currentTime);

        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(320, this.ctx.currentTime);

        this.ambientGain.gain.setValueAtTime(0.001, this.ctx.currentTime);
        this.ambientGain.gain.exponentialRampToValueAtTime(0.08, this.ctx.currentTime + 3.0);

        osc1.connect(filter);
        osc2.connect(filter);
        filter.connect(this.ambientGain);
        this.ambientGain.connect(this.ctx.destination);

        osc1.start();
        osc2.start();
    }

    stopAmbient() {
        if (this.ambientGain && this.ctx) {
            this.ambientGain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + 0.5);
            setTimeout(() => {
                if (this.ambientGain) {
                    this.ambientGain.disconnect();
                    this.ambientGain = null;
                }
            }, 550);
        }
    }

    playCameraWhoosh(duration = 0.45) {
        if (this.isMuted || !this.ctx || this.isTransitioning) return;
        this.isTransitioning = true;

        const bufferSize = this.ctx.sampleRate * duration;
        const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            data[i] = Math.random() * 2 - 1;
        }

        const noise = this.ctx.createBufferSource();
        noise.buffer = buffer;

        const filter = this.ctx.createBiquadFilter();
        filter.type = 'bandpass';
        filter.frequency.setValueAtTime(400, this.ctx.currentTime);
        filter.frequency.exponentialRampToValueAtTime(2200, this.ctx.currentTime + duration * 0.5);
        filter.frequency.exponentialRampToValueAtTime(300, this.ctx.currentTime + duration);

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(0.001, this.ctx.currentTime);
        gain.gain.linearRampToValueAtTime(0.12, this.ctx.currentTime + duration * 0.2);
        gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + duration);

        noise.connect(filter);
        filter.connect(gain);
        gain.connect(this.ctx.destination);

        noise.start();
        setTimeout(() => {
            this.isTransitioning = false;
        }, duration * 1000 + 50);
    }

    playHoverBlip() {
        if (this.isMuted || !this.ctx) return;

        if (this.activeHoverOsc) {
            try {
                this.activeHoverOsc.stop();
                this.activeHoverOsc.disconnect();
            } catch (e) {}
            this.activeHoverOsc = null;
        }

        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, this.ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, this.ctx.currentTime + 0.08);

        gain.gain.setValueAtTime(0.05, this.ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + 0.08);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        this.activeHoverOsc = osc;
        osc.start();
        osc.stop(this.ctx.currentTime + 0.09);
        setTimeout(() => {
            if (this.activeHoverOsc === osc) {
                this.activeHoverOsc = null;
            }
        }, 95);
    }
}
