"""Desktop applications indexed from .desktop desktop entries."""
import os
import glob

def get_installed_apps():
    search_dirs = [
        "/usr/share/applications",
        os.path.expanduser("~/.local/share/applications"),
        "/var/lib/flatpak/exports/share/applications"
    ]
    apps = {}
    for d in search_dirs:
        if os.path.exists(d):
            for f in glob.glob(os.path.join(d, "*.desktop")):
                base = os.path.splitext(os.path.basename(f))[0]
                apps[base] = f
    return apps
