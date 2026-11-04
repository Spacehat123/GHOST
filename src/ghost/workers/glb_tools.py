"""3D GLB Model Inspection and Optimization Tools for Ghost.
Enforces non-negotiable architectural invariant:
Ghost works on copies, never mutates originals in place, and never invents
metrics or claims tools ran when CLIs are unavailable.
"""
import os
import json
import struct
import shutil
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("ghost.glb_tools")


class GLBTools:
    """Zero-dependency pure Python GLB binary inspector with optional CLI optimization."""

    @classmethod
    def inspect(cls, file_path: str) -> Dict[str, Any]:
        """
        Inspects a 3D GLB file directly from binary chunks.
        Extracts mesh count, triangle count, materials, textures, animations, and bounds.
        """
        if not file_path or not os.path.exists(file_path):
            return {
                "ok": False,
                "status": "failed",
                "error": f"File not found: '{file_path}'",
                "tool": "glb_inspect"
            }

        abs_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(abs_path):
            return {
                "ok": False,
                "status": "failed",
                "error": f"Path is not a regular file: '{abs_path}'",
                "tool": "glb_inspect"
            }

        file_size = os.path.getsize(abs_path)
        if file_size < 20:
            return {
                "ok": False,
                "status": "failed",
                "error": "File too small to be a valid GLB container.",
                "tool": "glb_inspect"
            }

        try:
            with open(abs_path, "rb") as f:
                header = f.read(12)
                magic, version, total_len = struct.unpack("<4sII", header)
                if magic != b"glTF" or version != 2:
                    return {
                        "ok": False,
                        "status": "failed",
                        "error": f"Invalid GLB header (magic={magic}, version={version}). Must be glTF 2.0 binary.",
                        "tool": "glb_inspect"
                    }

                chunk_header = f.read(8)
                chunk_len, chunk_type = struct.unpack("<I4s", chunk_header)
                if chunk_type != b"JSON":
                    return {
                        "ok": False,
                        "status": "failed",
                        "error": f"Expected JSON chunk 0, got {chunk_type}.",
                        "tool": "glb_inspect"
                    }

                json_bytes = f.read(chunk_len)
                gltf = json.loads(json_bytes.decode("utf-8", errors="replace"))

            # Calculate geometry statistics
            accessors = gltf.get("accessors", [])
            meshes = gltf.get("meshes", [])
            materials = gltf.get("materials", [])
            textures = gltf.get("textures", [])
            images = gltf.get("images", [])
            animations = gltf.get("animations", [])

            total_triangles = 0
            bounds = {"min": [float("inf")] * 3, "max": [float("-inf")] * 3}

            for mesh in meshes:
                for prim in mesh.get("primitives", []):
                    # Triangles from indices
                    indices_idx = prim.get("indices")
                    if indices_idx is not None and indices_idx < len(accessors):
                        acc = accessors[indices_idx]
                        count = acc.get("count", 0)
                        total_triangles += count // 3
                    else:
                        # Non-indexed primitives
                        pos_idx = prim.get("attributes", {}).get("POSITION")
                        if pos_idx is not None and pos_idx < len(accessors):
                            acc = accessors[pos_idx]
                            count = acc.get("count", 0)
                            total_triangles += count // 3

                    # Compute bounding box
                    pos_idx = prim.get("attributes", {}).get("POSITION")
                    if pos_idx is not None and pos_idx < len(accessors):
                        acc = accessors[pos_idx]
                        acc_min = acc.get("min")
                        acc_max = acc.get("max")
                        if acc_min and len(acc_min) == 3:
                            bounds["min"] = [min(bounds["min"][i], acc_min[i]) for i in range(3)]
                        if acc_max and len(acc_max) == 3:
                            bounds["max"] = [max(bounds["max"][i], acc_max[i]) for i in range(3)]

            # Clean bounds if untouched
            if bounds["min"][0] == float("inf"):
                bounds = None

            # Check CLI availability for advanced transforms
            gltf_cli = shutil.which("gltf-transform")
            blender_cli = shutil.which("blender")

            cli_detected = []
            if gltf_cli:
                cli_detected.append("gltf-transform")
            if blender_cli:
                cli_detected.append("blender")

            return {
                "ok": True,
                "status": "success",
                "tool": "glb_inspect",
                "file": abs_path,
                "size_bytes": file_size,
                "triangle_count": total_triangles,
                "mesh_count": len(meshes),
                "material_count": len(materials),
                "texture_count": max(len(textures), len(images)),
                "animation_count": len(animations),
                "has_animations": len(animations) > 0,
                "bounds": bounds,
                "cli_available": cli_detected,
                "install_hint": None if gltf_cli else "Install @gltf-transform/cli via: npm install -g @gltf-transform/cli"
            }

        except Exception as e:
            logger.error(f"Failed to inspect GLB file '{file_path}': {e}", exc_info=True)
            return {
                "ok": False,
                "status": "failed",
                "error": f"Error parsing GLB file: {str(e)}",
                "tool": "glb_inspect"
            }

    @classmethod
    async def optimize(
        cls,
        source_path: str,
        output_path: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Optimizes a GLB model using @gltf-transform/cli.
        Always works on a copy; never mutates the original in place.
        If CLI is unavailable, returns explicit 'unavailable' status with install hint.
        """
        if not source_path or not os.path.exists(source_path):
            return {
                "ok": False,
                "status": "failed",
                "error": f"Source file not found: '{source_path}'",
                "tool": "glb_optimize"
            }

        abs_source = os.path.abspath(os.path.expanduser(source_path))
        dest_path = output_path or abs_source.replace(".glb", "_optimized.glb")
        abs_dest = os.path.abspath(os.path.expanduser(dest_path))

        gltf_cli = shutil.which("gltf-transform")
        if not gltf_cli:
            return {
                "ok": False,
                "status": "unavailable",
                "tool": "glb_optimize",
                "error": "@gltf-transform/cli is not installed on this system.",
                "install_hint": "Install @gltf-transform/cli globally via: npm install -g @gltf-transform/cli",
                "original_file": abs_source
            }

        # Work on a copy
        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
        shutil.copy2(abs_source, abs_dest)

        opts = options or {}
        compress = opts.get("compress", "draco")

        cmd = [gltf_cli, "optimize", abs_dest, abs_dest]
        if compress in ["draco", "meshopt"]:
            cmd.extend(["--compress", compress])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)

            orig_size = os.path.getsize(abs_source)
            new_size = os.path.getsize(abs_dest)
            saved_pct = round(((orig_size - new_size) / max(1, orig_size)) * 100, 1)

            return {
                "ok": True,
                "status": "success",
                "tool": "glb_optimize",
                "original_file": abs_source,
                "optimized_file": abs_dest,
                "original_size_bytes": orig_size,
                "optimized_size_bytes": new_size,
                "reduction_percent": max(0.0, saved_pct),
                "compression": compress
            }
        except Exception as e:
            logger.error(f"Failed running gltf-transform optimization: {e}")
            return {
                "ok": False,
                "status": "failed",
                "tool": "glb_optimize",
                "error": f"Optimization process error: {str(e)}",
                "install_hint": "Verify @gltf-transform/cli installation."
            }
