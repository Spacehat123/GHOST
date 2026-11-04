"""Procedural Web Audio Manager for Ghost 3D & Motion Sites.
Enforces non-negotiable architectural invariant:
1. Procedural Web Audio preferred (zero assets, zero licensing risk, zero bandwidth overhead).
2. Global audio toggle is strictly OFF by default (user must click to enable).
3. One transition sound per camera move.
4. No overlapping hover sounds (cancels/debounces previous blip).
"""
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger("ghost.audio_manager")


class ProceduralAudioManager:
    """Generates standalone procedural Web Audio synthesizer code for Three.js/motion sites."""

    @classmethod
    def get_procedural_audio_script(cls, palette: str = "warm") -> str:
        """
        Returns complete, framework-free TypeScript / JavaScript audio synthesis code.
        Implements ambient drone, camera whoosh transition, and UI interaction blips.
        """
        # Frequency presets per palette
        base_freq = "110.0"  # A2 warm
        if palette == "industrial":
            base_freq = "73.42"  # D2 industrial low
        elif palette == "winter":
            base_freq = "146.83" # D3 crystalline cold

        return f"""// Procedural Web Audio Synthesizer (Zero Asset / Zero License Risk)
// Palette: {palette} (Base freq: {base_freq}Hz)

export class ProceduralAudioManager {{
    private ctx: AudioContext | null = null;
    private isMuted: boolean = true; // Rule 1: Muted by default until visitor enables toggle
    private ambientGain: GainNode | null = null;
    private activeHoverOsc: OscillatorNode | null = null; // Rule 4: No overlapping hover sounds
    private isTransitioning: boolean = false; // Rule 3: One sound per camera move

    constructor() {{
        this.isMuted = true;
    }}

    public init(): void {{
        if (!this.ctx) {{
            const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
            this.ctx = new AudioCtx();
        }}
        if (this.ctx && this.ctx.state === 'suspended') {{
            this.ctx.resume();
        }}
    }}

    public toggle(): boolean {{
        this.init();
        this.isMuted = !this.isMuted;
        if (this.isMuted) {{
            this.stopAmbient();
        }} else {{
            this.startAmbient();
        }}
        return !this.isMuted;
    }}

    public getIsMuted(): boolean {{
        return this.isMuted;
    }}

    // Ambient Loop: Filtered detuned sine/saw oscillators with subtle lowpass breathing
    public startAmbient(): void {{
        if (this.isMuted || !this.ctx) return;
        this.stopAmbient();

        const osc1 = this.ctx.createOscillator();
        const osc2 = this.ctx.createOscillator();
        const filter = this.ctx.createBiquadFilter();
        this.ambientGain = this.ctx.createGain();

        osc1.type = 'sine';
        osc1.frequency.setValueAtTime({base_freq}, this.ctx.currentTime);

        osc2.type = 'triangle';
        osc2.frequency.setValueAtTime({base_freq} * 1.505, this.ctx.currentTime); // Subtle fifth + chorus beat

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
    }}

    public stopAmbient(): void {{
        if (this.ambientGain && this.ctx) {{
            this.ambientGain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + 0.8);
            setTimeout(() => {{
                if (this.ambientGain) {{
                    this.ambientGain.disconnect();
                    this.ambientGain = null;
                }}
            }}, 850);
        }}
    }}

    // Camera Whoosh: Noise burst + exponential filter sweep on camera move
    public playCameraWhoosh(duration: number = 0.45): void {{
        if (this.isMuted || !this.ctx || this.isTransitioning) return;
        this.isTransitioning = true;

        const bufferSize = this.ctx.sampleRate * duration;
        const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {{
            data[i] = Math.random() * 2 - 1;
        }}

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
        setTimeout(() => {{
            this.isTransitioning = false;
        }}, duration * 1000 + 50);
    }}

    // UI Hover Blip: Short FM blip. Cancels previous blip to prevent audio clutter.
    public playHoverBlip(): void {{
        if (this.isMuted || !this.ctx) return;

        // Cancel previous blip if active
        if (this.activeHoverOsc) {{
            try {{
                this.activeHoverOsc.stop();
                this.activeHoverOsc.disconnect();
            }} catch (e) {{}}
            this.activeHoverOsc = null;
        }}

        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, this.ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, this.ctx.currentTime + 0.08);

        gain.gain.setValueAtTime(0.06, this.ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + 0.08);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        this.activeHoverOsc = osc;
        osc.start();
        osc.stop(this.ctx.currentTime + 0.09);
        setTimeout(() => {{
            if (this.activeHoverOsc === osc) {{
                this.activeHoverOsc = null;
            }}
        }}, 95);
    }}
}}
"""

    @classmethod
    def lint_audio_contract(cls, script_content: str) -> Tuple[bool, List[str]]:
        """
        Verifies that an audio implementation complies with Ghost's audio invariants:
        1. Muted by default (`isMuted: boolean = true`).
        2. Single move transition guard (`isTransitioning`).
        3. No overlapping hover sounds (`activeHoverOsc` or cancellation).
        """
        violations = []
        if "isMuted: boolean = true" not in script_content and "this.isMuted = true" not in script_content:
            violations.append("Audio contract violation: Sound must be disabled/muted by default.")
        if "isTransitioning" not in script_content:
            violations.append("Audio contract violation: Transition sound must enforce single-move guard.")
        if "activeHoverOsc" not in script_content and "cancel" not in script_content.lower():
            violations.append("Audio contract violation: Hover sounds must cancel previous blips to prevent overlap.")

        return len(violations) == 0, violations
