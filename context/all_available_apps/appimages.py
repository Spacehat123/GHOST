"""Custom standalone binaries, scripts, and AppImages."""
import os

def get_appimages():
    search_dirs = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/Applications"),
        "/opt"
    ]
    binaries = []
    for d in search_dirs:
        if os.path.exists(d):
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".AppImage") or os.access(os.path.join(root, f), os.X_OK):
                        binaries.append(os.path.join(root, f))
    return binaries
