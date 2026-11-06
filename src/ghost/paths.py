"""One convention for runtime file locations.

Runtime state lives under <repo>/data. Several modules resolved it by
walking up from __file__, and the packages two levels deep landed on
src/data instead — the grant nonce db, the sim receipt and the darkrun
state ended up scattered inside the SOURCE tree. This helper is the
single resolution point:

- GHOST_DATA_DIR overrides the data directory (tests, relocated installs)
- GHOST_PROJECT_ROOT pins the repo root (same knob core/heartbeat and
  core/halt already honor)

Stdlib only — the authority daemon imports code that uses this.
"""
import os


def repo_root() -> str:
    return os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT")
                           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "..", ".."))


def data_dir() -> str:
    return os.path.abspath(os.environ.get("GHOST_DATA_DIR")
                           or os.path.join(repo_root(), "data"))
