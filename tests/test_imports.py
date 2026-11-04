"""Smoke test verifying all modules under src/ghost can be imported cleanly.
Prevents missing typing/runtime import regressions.
"""
import os
import importlib
import unittest


class TestModuleImports(unittest.TestCase):

    def test_all_ghost_modules_import_cleanly(self):
        """Walk src/ghost and import every single Python module."""
        src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        ghost_root = os.path.join(src_root, "ghost")
        
        imported_count = 0
        failures = []

        for root, _, files in os.walk(ghost_root):
            for file in sorted(files):
                if file.endswith(".py") and not file.startswith("."):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, src_root)
                    module_name = rel_path[:-3].replace(os.sep, ".")

                    try:
                        importlib.import_module(module_name)
                        imported_count += 1
                    except Exception as e:
                        failures.append(f"{module_name}: {type(e).__name__}: {str(e)}")

        self.assertGreater(imported_count, 40, "Expected at least 40 modules to be discovered under src/ghost")
        if failures:
            self.fail(f"Failed to import {len(failures)} module(s):\n" + "\n".join(failures))
