# Kokoro on GTX 1650: fix plan (for agy)

Problem: Kokoro-82M runs on CPU and is slow; GPU is never really used.
Hardware: GTX 1650 (Turing, compute 7.5, 4 GB VRAM). Arch Linux. Model is only 82M params, so it fits easily; this is a software/config problem, not a hardware limit.
File to change: `src/ghost/voice/kokoro.py` (+ small edits in `scripts/launch_ghost.sh`, `requirements-gpu.txt`).

## What is almost certainly wrong (from reading the current code)

1. **It loads the INT8 model.** `kokoro-v1.0.int8.onnx` uses quantized ops that the ONNX Runtime CUDA provider mostly has no kernels for. Those nodes silently fall back to CPU with GPU<->CPU copies, so even "working" GPU can be no faster, or slower, than pure CPU. INT8 is a CPU model. On GPU use `kokoro-v1.0.onnx` (fp32, ~310 MB). (fp16 also works on GPU but gives no benefit on a 1650 over fp32; start with fp32.)
2. **Failures are hidden.** `sess_opts.log_severity_level = 3` hides the warning "Failed to create CUDAExecutionProvider". And `ort.get_available_providers()` lists CUDA whenever the *GPU wheel* is installed, even if the CUDA libraries can't be loaded. The code then trusts it and falls back to CPU without telling anyone.
3. **Library mismatch (very common on Arch).** PyPI `onnxruntime-gpu` wheels are built against CUDA 12 + cuDNN 9. Arch's `cuda` package is usually newer, so `libcublas.so.12` / `libcudnn.so.9` aren't found. Also if both `onnxruntime` and `onnxruntime-gpu` are installed, the CPU one wins.
4. **Per-shape tuning.** The CUDA provider defaults to exhaustive cuDNN algorithm search. Speech has a different input length for every sentence, so every new sentence can trigger a slow re-tune. Set `cudnn_conv_algo_search` to `HEURISTIC`, and warm the model up at startup.
5. `scripts/launch_ghost.sh` points to `/home/pranc/kokoro-gpu/run_ghost_gpu.sh`, a separate venv that is not in the repo. Nobody can see what it installs. Replace it with a repo-tracked setup (below).

I could not test on a GPU, so treat the above as the ranked hypotheses; step 1 proves which one it is.

## Step 1: Diagnose (10 minutes, do this first)
Run `kokoro_gpu_diagnose.py` (included) in the venv Ghost uses:
```
python kokoro_gpu_diagnose.py --dir ~/.local/share/cool-shell/models/kokoro
```
It prints GPU/driver, installed onnxruntime packages, which CUDA libs are missing, whether CUDA really activates, and CPU-vs-GPU timing for each model file. Do not change code until you've read it.

## Step 2: Clean packages (own venv, repo-tracked)
Create `requirements-gpu.txt`:
```
kokoro-onnx
onnxruntime-gpu
soundfile
numpy
nvidia-cudnn-cu12
nvidia-cublas-cu12
nvidia-cuda-runtime-cu12
nvidia-cufft-cu12
nvidia-curand-cu12
```
```
python -m venv ~/.venvs/ghost-gpu && source ~/.venvs/ghost-gpu/bin/activate
pip uninstall -y onnxruntime || true      # never both
pip install -r requirements-gpu.txt
sudo pacman -S --needed espeak-ng          # phonemizer for British voices
```
NVIDIA driver must be working (`nvidia-smi` shows the 1650). System `cuda` package is NOT required for the pip wheels path.

## Step 3: Make the CUDA libraries findable
Newer onnxruntime (>= 1.21) has `onnxruntime.preload_dlls()` which loads CUDA/cuDNN from the `nvidia-*` pip wheels. Call it before creating the session (code below). If the diagnostic still shows MISSING libs, export in the launcher:
```
NVLIBS=$(python - <<'EOF'
import glob, os, nvidia
root = list(nvidia.__path__)[0]
print(":".join(sorted(glob.glob(os.path.join(root, "*", "lib")))))
EOF
)
export LD_LIBRARY_PATH="$NVLIBS:$LD_LIBRARY_PATH"
```
(Put this in `scripts/launch_ghost.sh` where it starts uvicorn, replacing the `/home/pranc/kokoro-gpu/run_ghost_gpu.sh` branch.)

## Step 4: Use the right model per device
Files from the kokoro-onnx model release: `kokoro-v1.0.onnx` (fp32), `kokoro-v1.0.fp16.onnx`, `kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`. Keep all in `~/.local/share/cool-shell/models/kokoro/`.
- GPU active -> `kokoro-v1.0.onnx`.
- CPU only -> `kokoro-v1.0.int8.onnx`.

## Step 5: Patch `src/ghost/voice/kokoro.py`
Replace `initialize()` with logic that (a) preloads libs, (b) shows warnings, (c) verifies the provider really is CUDA, (d) picks the model by device, (e) warms up. Add env `KOKORO_DEVICE=auto|cuda|cpu` (default auto). Keep the rest of the file as is.

```python
KOKORO_DIR = os.path.expanduser("~/.local/share/cool-shell/models/kokoro")
MODEL_FP32 = os.environ.get("KOKORO_MODEL_FP32", os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"))
MODEL_INT8 = os.environ.get("KOKORO_MODEL_INT8", os.path.join(KOKORO_DIR, "kokoro-v1.0.int8.onnx"))
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES_PATH", os.path.join(KOKORO_DIR, "voices-v1.0.bin"))
KOKORO_DEVICE = os.environ.get("KOKORO_DEVICE", "auto").lower()

def initialize(self):
    if self._initialized:
        return
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()          # loads CUDA/cuDNN from pip nvidia-* wheels
        except Exception as e:
            logger.warning(f"preload_dlls failed: {e}")

    want_gpu = KOKORO_DEVICE in ("auto", "cuda") and "CUDAExecutionProvider" in ort.get_available_providers()
    sess = None
    if want_gpu and os.path.exists(MODEL_FP32):
        so = ort.SessionOptions()
        so.log_severity_level = 2        # show provider-load warnings
        try:
            sess = ort.InferenceSession(
                MODEL_FP32, so,
                providers=[("CUDAExecutionProvider", {
                    "device_id": 0,
                    "cudnn_conv_algo_search": "HEURISTIC",
                    "arena_extend_strategy": "kSameAsRequested",
                    "gpu_mem_limit": 2 * 1024 ** 3,       # 1650 has 4 GB; leave room for the display
                }), "CPUExecutionProvider"],
            )
            if sess.get_providers()[0] != "CUDAExecutionProvider":
                logger.warning("CUDA requested but session is on CPU; falling back to INT8 CPU path. "
                               "Run scripts/kokoro_gpu_diagnose.py")
                sess = None
        except Exception as e:
            logger.warning(f"CUDA session failed ({e}); falling back to CPU")
            sess = None
        if sess is None and KOKORO_DEVICE == "cuda":
            raise RuntimeError("KOKORO_DEVICE=cuda but the CUDA provider could not start")

    if sess is None:                      # CPU path
        so = ort.SessionOptions()
        so.intra_op_num_threads = os.cpu_count() or 4
        so.inter_op_num_threads = 1
        path = MODEL_INT8 if os.path.exists(MODEL_INT8) else MODEL_FP32
        sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])

    self._active_provider = sess.get_providers()[0]
    self._kokoro = Kokoro.from_session(sess, KOKORO_VOICES_PATH)
    self._initialized = True
    logger.warning(f"Kokoro online. Provider={self._active_provider}")   # warning level so it always shows

    # warm-up: first call pays kernel/cache setup; do it now, not on the user's first sentence
    try:
        for t in ("Online.", "Understood, boss. Three demos are ready for review."):
            self._kokoro.create(t, voice=self.voice, speed=1.05, lang="en-gb")
    except Exception as e:
        logger.warning(f"Warm-up failed: {e}")
```
Also: remove the unconditional `log_severity_level = 3`; fix the hardcoded `/home/pranc` defaults to use `KOKORO_DIR`; add the active provider and last synthesis time to the `/api/tts` response headers (already has `X-Provider`; add `X-Synth-Ms`).

## Step 6: Streaming/latency improvements (after GPU works)
- Keep one `KokoroSpeechEngine` instance (already a singleton); never re-create per request.
- `synthesize_stream_clauses` already splits into clauses; keep clause 1 short (4-8 words) for fast first audio.
- Run phonemization and inference in the same worker thread (`asyncio.to_thread` is fine); use a single-worker executor so two requests don't contend for the GPU.
- Cache WAV bytes for fixed phrases ("Understood.", "On it.", greetings) in a small LRU.

## Step 7: Plan B if ONNX GPU keeps failing: PyTorch Kokoro
The PyTorch wheels bundle their own CUDA runtime, so there is no system-CUDA mismatch.
```
pip install torch --index-url https://download.pytorch.org/whl/cu126   # CUDA 12.x build; Turing (sm_75) is supported
pip install kokoro soundfile
```
```python
from kokoro import KPipeline
pipe = KPipeline(lang_code="b", device="cuda")          # 'b' = British English
for _, _, audio in pipe(text, voice="bm_george", speed=1.05):
    ...   # float32 numpy 24 kHz, same as ONNX path
```
Put it behind the same `KokoroSpeechEngine` interface (`synthesize_wav`, `synthesize_stream_clauses`) selected by `KOKORO_BACKEND=onnx|torch`. Weights download once from Hugging Face; make sure that happens at install time, not at first request.

## Step 8: Tests and acceptance
- Unit: `initialize()` with a monkeypatched `ort` whose CUDA session reports only `CPUExecutionProvider` -> logs the warning, uses the INT8 CPU path, `KOKORO_DEVICE=cuda` raises.
- Unit: `KOKORO_DEVICE=cpu` never touches CUDA.
- Manual acceptance on the 1650 (record the numbers in `PLAN.md`):
  - `nvidia-smi` shows the Python process using GPU memory (~0.5-1 GB) during synthesis.
  - Log line says `Provider=CUDAExecutionProvider`.
  - A 10-word sentence synthesizes in well under 0.5 s after warm-up and the diagnostic shows a real-time factor clearly above the CPU result (target: at least 5x faster than the current CPU path; if not, send the diagnostic output back).
  - First clause of a long reply starts playing in under 1 s.
