#!/usr/bin/env python3
"""Kokoro GPU diagnostic. Run inside the SAME venv that Ghost uses.

Usage:
  python kokoro_gpu_diagnose.py --dir ~/.local/share/cool-shell/models/kokoro

It prints, in order: GPU/driver, onnxruntime packages, CUDA library loading,
whether the CUDA provider really activates, and timing for each model file
(fp32 / fp16 / int8) on CPU and on GPU, so you see the real cause instead of guessing.
Paste the full output back to whoever is helping.
"""
import argparse, ctypes, os, subprocess, sys, time

TEXT = ["Understood, boss.", "Three demos are built and waiting for your review.", "The deployment gate is open whenever you are ready."]


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:  # noqa
        return f"(failed: {e})"


def section(t):
    print(f"\n=== {t} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/.local/share/cool-shell/models/kokoro"))
    ap.add_argument("--voice", default="bm_george")
    a = ap.parse_args()

    section("GPU / driver")
    print(sh("nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv"))
    print(sh("nvidia-smi | head -4 | tail -2"))  # shows driver's max CUDA version
    print("nvcc:", sh("nvcc --version | tail -1") or "(not installed; fine for pip wheels)")

    section("Python packages")
    print(sys.version.split()[0], sys.executable)
    print(sh(f"{sys.executable} -m pip list 2>/dev/null | grep -iE 'onnxruntime|kokoro|nvidia|torch|numpy|soundfile|espeak|phonemizer|misaki'"))
    print("NOTE: having BOTH `onnxruntime` and `onnxruntime-gpu` installed breaks the GPU provider. Keep only onnxruntime-gpu.")

    section("onnxruntime")
    import onnxruntime as ort
    print("version:", ort.__version__, "| device:", ort.get_device())
    print("available providers:", ort.get_available_providers())
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
            print("ort.preload_dlls(): ok")
        except Exception as e:
            print("ort.preload_dlls() failed:", e)

    section("CUDA shared libraries the CUDA provider needs (CUDA 12 / cuDNN 9 builds)")
    for lib in ["libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcufft.so.11", "libcurand.so.10", "libcudnn.so.9"]:
        try:
            ctypes.CDLL(lib)
            print(f"  OK       {lib}")
        except OSError as e:
            print(f"  MISSING  {lib}  -> {e}")
    print("LD_LIBRARY_PATH =", os.environ.get("LD_LIBRARY_PATH", "(unset)"))

    section("Does the CUDA provider really activate? (warnings are shown, not hidden)")
    models = {}
    for name in ["kokoro-v1.0.onnx", "kokoro-v1.0.fp16.onnx", "kokoro-v1.0.int8.onnx"]:
        p = os.path.join(a.dir, name)
        if os.path.exists(p):
            models[name] = p
    voices = os.path.join(a.dir, "voices-v1.0.bin")
    if not models or not os.path.exists(voices):
        print("No model/voice files found in", a.dir)
        return
    print("models found:", {k: f"{os.path.getsize(v)/1e6:.0f} MB" for k, v in models.items()})

    from kokoro_onnx import Kokoro

    def make_session(path, use_cuda):
        so = ort.SessionOptions()
        so.log_severity_level = 2  # WARNING: shows "Failed to create CUDAExecutionProvider"
        if use_cuda:
            prov = [("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC"}), "CPUExecutionProvider"]
        else:
            so.intra_op_num_threads = os.cpu_count() or 4
            prov = ["CPUExecutionProvider"]
        return ort.InferenceSession(path, so, providers=prov)

    def bench(label, path, use_cuda):
        try:
            sess = make_session(path, use_cuda)
        except Exception as e:
            print(f"[{label}] could not create session: {e}")
            return
        active = sess.get_providers()
        k = Kokoro.from_session(sess, voices)
        times = []
        audio_secs = 0.0
        for t in TEXT + TEXT:  # second pass shows steady-state
            t0 = time.perf_counter()
            samples, sr = k.create(t, voice=a.voice, speed=1.05, lang="en-gb")
            times.append(time.perf_counter() - t0)
            audio_secs += len(samples) / sr
        first, rest = times[:3], times[3:]
        print(f"[{label}] providers={active}")
        print(f"    1st pass: {[round(x,2) for x in first]} s   2nd pass: {[round(x,2) for x in rest]} s")
        print(f"    real-time factor (audio seconds per compute second, 2nd pass): {sum(len(k.create(t, voice=a.voice, speed=1.05, lang='en-gb')[0])/24000 for t in TEXT)/max(sum(rest),1e-6):.1f}x")
        if use_cuda and active[0] != "CUDAExecutionProvider":
            print("    >>> CUDA was requested but the session is running on CPU. Fix the libraries above first.")

    for name, path in models.items():
        bench(f"{name} CPU", path, False)
        bench(f"{name} GPU", path, True)

    section("How to read this")
    print("* Any MISSING CUDA lib -> install the matching nvidia-*-cu12 pip wheels in this venv (see plan, step 3).")
    print("* GPU session shows only CPUExecutionProvider -> libraries or duplicate onnxruntime packages (steps 2-3).")
    print("* int8 GPU slower than int8 CPU, fp32 GPU faster -> use the fp32 model on GPU (step 4).")
    print("* 1st pass much slower than 2nd on GPU -> warm-up + cudnn_conv_algo_search=HEURISTIC (step 5).")


if __name__ == "__main__":
    main()
