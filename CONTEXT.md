# GHOST & ROSE: The Ground-Truth Architecture & Blueprint Dossier
**Location:** `/home/pranc/projects/ghost/CONTEXT.md`  
**Purpose:** Serves as the definitive, single-source ground-truth document for the autonomous Ghost agent, detailing the real source code, visual shaders, full-duplex audio mechanics, and system integrations.

---

## 1. Visual Ground Truth (From Extracted Reel & Reference Image)

### 1.1 The Visual Anatomy of the Rose/Ghost Orb
Based on frame analysis of `/tmp/rose_reel.mp4` and the uploaded reference image `/home/pranc/projects/ghost/rose_reference.jpg`:

```
┌─────────────────────────────────────────────────────────────┐
│ Background: Deep Void / Obsidian Navy                       │
│ - Radial Gradient: #050811 (inner center) -> #020408 (edges)│
│                                                             │
│                (  Undulating Orbital Filaments  )           │
│              ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~            │
│            ~                                     ~          │
│          ~     ┌───────────────────────────┐       ~        │
│         ~      │   Concentric Outer Halos   │        ~       │
│        ~       │   #1e295d -> #3b42c4       │         ~      │
│        ~       │   ┌─────────────────────┐ │         ~      │
│        ~       │   │   Inner Cyan Aura   │ │         ~      │
│        ~       │   │   #525ceb -> #60a5fa│ │         ~      │
│        ~       │   │   ┌───────────────┐ │ │         ~      │
│        ~       │   │   │ Luminous Core │ │ │         ~      │
│        ~       │   │   │  #ffffff      │ │ │         ~      │
│        ~       │   │   └───────────────┘ │ │         ~      │
│        ~       │   └─────────────────────┘ │         ~      │
│         ~      └───────────────────────────┘        ~       │
│          ~                                         ~        │
│            ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~          │
│                                                             │
│ Bottom: Frameless Ambient Subtitle / Status Chip            │
└─────────────────────────────────────────────────────────────┘
```

1. **The Luminous White-Hot Core**:
   - High-intensity pure white center (`#ffffff`) fading into electric cyan (`#93c5fd`).
   - Scales smoothly with live RMS volume ($1.0\times$ to $1.35\times$).
2. **Concentric Ambient Halos**:
   - Two soft-falloff Gaussian radial gradient discs:
     - Inner Halo: `#3b42c4` with 65% opacity.
     - Outer Ambient Disc: `#1e295d` with 30% opacity extending to 1.8× base radius.
3. **The 6 Intertwined Harmonic Filaments (Signature Feature)**:
   - 6 closed-loop sinusoidal wave ribbons orbiting around the core at varying angular speeds and harmonic orders:
     $$r(\theta) = R_{\text{base}} + \sum_{k=1}^{3} A_k \sin(n_k \theta + \omega_k t + \phi_k)$$
   - When idle: Calm, breathing orbital motion.
   - When speaking / listening: High-frequency organic deformation responding to FFT frequency bins.
4. **UI Layout Philosophy**:
   - Zero bulky boxes, no ChatGPT-style grey conversation panels.
   - Full-viewport ambient Canvas with floating, translucent subtitle chips at the bottom.

---

## 2. Creator Source Code Truth: Huw Prosser's Engine

Analyzed directly from Huw Prosser's repositories (`jarvis-mlx` and `fury-sdk`):

### 2.1 The Runner Pattern (`fury-sdk`)
From `fury/agent.py` and `fury/runtime.py`:
- Traditional agents block until an LLM completes its generation.
- The **Runner Pattern** yields execution control to an async generator loop.
- Interruption is handled via an explicit cancellation token:
  ```python
  runner = agent.runner()
  # If speech is detected:
  runner.interrupt()  # instantly aborts token generation mid-stream
  ```
- History is kept compact using `HistoryManager` and target-context trimming.

### 2.2 Voice Activity Detection (VAD) & Timing
From `jarvis-mlx/stt/VoiceActivityDetection.py`:
- Uses **Silero VAD** with 32ms frames (512 samples @ 16kHz).
- Sensitivity threshold: `0.3` to `0.4` speech probability.
- Audio buffer uses pre-roll memory (saving 150ms before speech onset) so the user's first syllable is never clipped.

### 2.3 Prompt Engineering & Persona Anchoring
From `jarvis-mlx/main.py` and `fury-sdk/SOUL.md`:
- **The 3-Sentence Rule**:
  > *"Answer the following input from the user in no more than three sentences. Only respond with the dialogue, nothing else."*
- Strictly prevents voice models from droning on with long paragraphs.

---

## 3. Persistent `agy` Session Continuity

Ghost is powered by the Google Antigravity CLI (`agy`). The ground truth for session persistence:

1. **Stateless vs. Persistent**:
   - Running `agy -p "..."` without flags creates a detached, one-off context.
   - Running with `--output-format json` yields:
     ```json
     {
       "conversation_id": "c983a1ef-7bf2-416b-9c2f-e8b846e4e5aa",
       "response": "..."
     }
     ```
2. **Resuming the Exact Conversation**:
   - On all subsequent queries, Ghost must pass:
     ```bash
     /home/pranc/.local/bin/agy -p "..." --conversation <conversation_id> --dangerously-skip-permissions --output-format json
     ```
   - This maintains the exact same session thread, variable memory, tool history, and identity across reboots and restarts.

---

## 4. System & Desktop Tool Infrastructure (Arch Linux + Hyprland)

Ghost runs natively in your environment with direct tool bridges:

1. **Brave Browser (Chrome DevTools Protocol)**:
   - Remote debugging active on `http://127.0.0.1:9222`.
   - Direct JSON/WebSocket endpoints for listing open tabs, navigating URLs, and reading page contents.
2. **Audio & Media (`playerctl` & WirePlumber)**:
   - MPRIS media player control: `playerctl play-pause`, `playerctl next`, `playerctl metadata`.
   - Audio sink/volume: `wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+` / `5%-`.
3. **Desktop Apps & Hyprland**:
   - Hyprland workspace queries: `hyprctl activewindow -j`, `hyprctl workspaces -j`.
   - Desktop notifications: `notify-send "Ghost" "Message"`.
   - Safe app launches: `solidcraft`, `obsidian`, `kitty`, `dolphin`, `blender`, `freecad`.
4. **REA (Reverse Engineer Anything)**:
   - Direct integration with `/home/pranc/projects/rea/scripts/rea.mjs` for binary disassembly, symbol tracing, and application graph reconstruction.
