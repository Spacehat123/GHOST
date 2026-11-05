"""Plane boundary contracts (ROADMAP_TOTAL_AUTONOMY.md §2) — ratchet test.

Target layout: brain / control / effects / verify / store / supervisor / sim.
Rules enforced on the plane map below:
  brain   -> must not import effects modules, core.secrets, or send libs
             (smtplib / razorpay / stripe / requests)
  effects -> must not import brain modules
  verify  -> must not import brain modules

Existing violations are recorded in tests/plane_contract_baseline.json (a
ratchet): this test FAILS only when a NEW violation appears. T1-T7 shrink the
baseline to zero, at which point the rules are absolute. The numbered list is
printed on every run so the debt stays visible.
"""
import ast
import json
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ghost"
BASELINE = Path(__file__).parent / "plane_contract_baseline.json"

# Current module locations per plane (updated as T1-T7 git mv modules).
PLANE_MAP = {
    "brain": ["ghost.core.brain"],
    "control": ["ghost.policy.engine", "ghost.autonomy.ladder", "ghost.core.halt",
                "ghost.core.grants", "ghost.core.jobqueue", "ghost.autonomy.outbound"],
    "effects": ["ghost.autonomy.sender", "ghost.workers.deploy",
                "ghost.workers.retainers", "ghost.comms.email_service"],
    "verify": ["ghost.core.three_qa", "ghost.workers.site_monitor", "ghost.core.dnscheck"],
}
BANNED_LIBS = {
    "brain": {"smtplib", "razorpay", "stripe", "requests"},
    "effects": set(),
    "verify": set(),
}
BANNED_PLANES = {"brain": {"effects"}, "effects": {"brain"}, "verify": {"brain"}}
EXTRA_BANNED_MODULES = {"brain": ["ghost.core.secrets"]}

PLANE_PACKAGES = ("brain", "control", "effects", "verify", "store", "supervisor", "sim")


def _module_of(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return "ghost." + rel.as_posix().replace("/", ".")


def _plane_of(module: str):
    for plane, prefixes in PLANE_MAP.items():
        for p in prefixes:
            if module == p or module.startswith(p + "."):
                return plane
    return None


def _imports_of(pyfile: Path) -> set:
    tree = ast.parse(pyfile.read_text(encoding="utf-8", errors="replace"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import — resolve against the file's package
                pkg = _module_of(pyfile)
                for _ in range(node.level):
                    pkg = pkg.rsplit(".", 1)[0]
                base = f"{pkg}.{base}" if base else pkg
            if base:
                mods.add(base)
                for alias in node.names:
                    mods.add(f"{base}.{alias.name}")
    return mods


def collect_violations() -> list:
    violations = set()
    for pyfile in sorted(SRC.rglob("*.py")):
        module = _module_of(pyfile)
        plane = _plane_of(module)
        if plane is None:
            continue
        for imp in _imports_of(pyfile):
            root = imp.split(".")[0]
            if root in BANNED_LIBS.get(plane, set()):
                violations.add(f"{module} -> lib:{root}")
                continue
            if plane == "brain" and any(
                    imp == b or imp.startswith(b + ".")
                    for b in EXTRA_BANNED_MODULES.get(plane, [])):
                violations.add(f"{module} -> {imp}")
                continue
            target_plane = _plane_of(imp)
            if target_plane in BANNED_PLANES.get(plane, set()):
                violations.add(f"{module} -> {imp}")
    return sorted(violations)


class TestPlaneContracts(unittest.TestCase):

    def test_no_new_boundary_violations(self):
        current = collect_violations()
        baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else []
        new = [v for v in current if v not in baseline]
        print(f"\nplane contracts: {len(current)} known violations "
              f"(baseline {len(baseline)}), {len(new)} NEW — burn these down in T1-T7:")
        for i, v in enumerate(current, 1):
            print(f"  {i}. {v}")
        self.assertEqual(new, [], "new plane-boundary violations:\n" + "\n".join(new))

    def test_plane_packages_exist(self):
        for plane in PLANE_PACKAGES:
            init = SRC / plane / "__init__.py"
            self.assertTrue(init.exists(), f"missing plane package ghost/{plane}/__init__.py")


if __name__ == "__main__":
    unittest.main()
