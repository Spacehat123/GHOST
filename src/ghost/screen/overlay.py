#!/usr/bin/env python3
"""
Ghost Screen Drawing Overlay Daemon.
Wayland-native transparent desktop overlay using GTK 3, GtkLayerShell, and Cairo.
Runs on the OVERLAY layer with complete click-through input transparency.
Dynamically maps/unmaps the window and enforces empty input regions on all size allocations.
Exposes a Unix Domain Socket IPC interface at /tmp/ghost_screen_overlay.sock.
"""

import os
import sys
import json
import math
import time
import socket
import logging
from typing import Any, Dict, List, Optional, Tuple

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell, GLib
import cairo

SOCKET_PATH = "/tmp/ghost_screen_overlay.sock"
PID_FILE = "/tmp/ghost_screen_overlay.pid"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Overlay] %(message)s")
logger = logging.getLogger("GhostOverlay")


def hex_to_rgba(hex_str: str, default_alpha: float = 1.0) -> Tuple[float, float, float, float]:
    """Parse hex or rgba color strings to Cairo (r, g, b, a) tuples in [0.0, 1.0]."""
    s = hex_str.strip().lower()
    if s.startswith("rgba"):
        try:
            parts = s.replace("rgba(", "").replace(")", "").split(",")
            r = float(parts[0]) / 255.0
            g = float(parts[1]) / 255.0
            b = float(parts[2]) / 255.0
            a = float(parts[3])
            return (r, g, b, a)
        except Exception:
            return (0.0, 1.0, 0.4, default_alpha)
    if s.startswith("rgb"):
        try:
            parts = s.replace("rgb(", "").replace(")", "").split(",")
            r = float(parts[0]) / 255.0
            g = float(parts[1]) / 255.0
            b = float(parts[2]) / 255.0
            return (r, g, b, default_alpha)
        except Exception:
            return (0.0, 1.0, 0.4, default_alpha)

    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        return (r, g, b, default_alpha)
    if len(s) == 8:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        a = int(s[6:8], 16) / 255.0
        return (r, g, b, a)
    return (0.0, 1.0, 0.4, default_alpha)


class ScreenOverlayApp:
    def __init__(self, monitor_index: int = 0):
        self.elements: Dict[str, Dict[str, Any]] = {}
        self.history: List[List[str]] = []
        self.redo_stack: List[List[Dict[str, Any]]] = []
        self.seq_id = 0

        # Initialize GTK Layer Shell Window
        self.window = Gtk.Window()
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self.window, "ghost-screen-overlay")
        GtkLayerShell.set_exclusive_zone(self.window, -1)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)

        # Disable all focus and input event masks on the window
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_can_focus(False)
        self.window.set_events(0)

        # Anchor to all four edges to cover monitor
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self.window, edge, True)

        # Select monitor if multi-monitor
        display = Gdk.Display.get_default()
        if display:
            n_monitors = display.get_n_monitors()
            if 0 <= monitor_index < n_monitors:
                gdk_mon = display.get_monitor(monitor_index)
                GtkLayerShell.set_monitor(self.window, gdk_mon)

        # Transparent background styling
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)

        # Drawing Area with zero event interception
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_events(0)
        self.drawing_area.connect("draw", self.on_draw)
        self.window.add(self.drawing_area)

        # Enforce empty input region across all lifecycle hooks (realize, map, size-allocate)
        self.window.connect("realize", self.enforce_click_through)
        self.window.connect("map", self.enforce_click_through)
        self.window.connect("size-allocate", self.enforce_click_through)
        self.window.connect("destroy", Gtk.main_quit)

        # Start with window realized but hidden until elements are drawn
        self.window.realize()
        self.enforce_click_through(self.window)

        # Socket IPC server
        self.server_sock: Optional[socket.socket] = None
        self.setup_ipc_server()

    def enforce_click_through(self, widget, *args):
        """Enforces a 100% empty input region on both the Gtk.Widget and Gdk.Window."""
        try:
            empty_region = cairo.Region()
            # 1. Apply to widget level (retained across GTK resizes)
            widget.input_shape_combine_region(empty_region)
            # 2. Apply to low-level GDK Wayland window
            gdk_win = widget.get_window()
            if gdk_win:
                gdk_win.input_shape_combine_region(empty_region, 0, 0)
        except Exception as e:
            logger.debug(f"Input shape combine error: {e}")

    def update_visibility(self):
        """Show window when elements exist; hide window when empty to free all compositor resources."""
        if not self.elements:
            if self.window.is_visible():
                self.window.hide()
                logger.info("Canvas empty: overlay window hidden.")
        else:
            if not self.window.is_visible():
                self.window.show_all()
                self.enforce_click_through(self.window)
                logger.info("Elements active: overlay window shown with click-through.")
            self.drawing_area.queue_draw()

    def setup_ipc_server(self):
        """Bind Unix Domain Socket and integrate into GLib main loop."""
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass

        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(SOCKET_PATH)
        self.server_sock.listen(8)
        self.server_sock.setblocking(False)

        # Add socket to GLib event loop
        GLib.io_add_watch(self.server_sock.fileno(), GLib.IO_IN, self.handle_socket_accept)
        logger.info(f"Screen Overlay IPC listening on {SOCKET_PATH}")

        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

    def handle_socket_accept(self, fd, condition):
        """Accept new IPC client connection."""
        try:
            client_sock, _ = self.server_sock.accept()
            client_sock.setblocking(False)
            GLib.io_add_watch(client_sock.fileno(), GLib.IO_IN, self.handle_client_data, client_sock)
        except Exception as e:
            logger.error(f"Error accepting connection: {e}")
        return True

    def handle_client_data(self, fd, condition, client_sock):
        """Read newline-delimited JSON commands from IPC client."""
        try:
            raw_data = client_sock.recv(65536)
            if not raw_data:
                client_sock.close()
                return False

            text = raw_data.decode("utf-8").strip()
            if not text:
                return True

            for line in text.split("\n"):
                if not line.strip():
                    continue
                req = json.loads(line)
                resp = self.dispatch_command(req)
                client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception as e:
            logger.error(f"Error handling IPC client data: {e}")
            try:
                client_sock.sendall(json.dumps({"status": "error", "message": str(e)}).encode("utf-8") + b"\n")
                client_sock.close()
            except Exception:
                pass
            return False
        return True

    def dispatch_command(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Process drawing and canvas commands."""
        cmd = req.get("cmd", "").lower().strip()

        if cmd == "ping":
            return {"status": "ok", "message": "pong", "elements_count": len(self.elements)}

        elif cmd == "clear":
            self.elements.clear()
            self.history.clear()
            self.redo_stack.clear()
            self.update_visibility()
            return {"status": "ok", "action": "clear", "count": 0}

        elif cmd == "draw":
            elem_specs = req.get("elements", [])
            if not isinstance(elem_specs, list):
                elem_specs = [elem_specs]

            created_ids = []
            for spec in elem_specs:
                self.seq_id += 1
                elem_id = spec.get("id") or f"elem_{self.seq_id}_{int(time.time()*1000)}"
                spec["id"] = elem_id
                self.elements[elem_id] = spec
                created_ids.append(elem_id)

                duration = float(spec.get("duration", 0.0))
                if duration > 0:
                    GLib.timeout_add(int(duration * 1000), self.remove_element, elem_id)

            if created_ids:
                self.history.append(created_ids)
                self.redo_stack.clear()

            self.update_visibility()
            return {
                "status": "ok",
                "action": "draw",
                "created_ids": created_ids,
                "total_active": len(self.elements)
            }

        elif cmd == "undo":
            if not self.history:
                return {"status": "ok", "action": "undo", "undone_count": 0, "message": "History empty"}
            last_ids = self.history.pop()
            stashed = []
            for eid in last_ids:
                if eid in self.elements:
                    stashed.append(self.elements.pop(eid))
            if stashed:
                self.redo_stack.append(stashed)
            self.update_visibility()
            return {"status": "ok", "action": "undo", "undone_ids": last_ids, "active": len(self.elements)}

        elif cmd == "redo":
            if not self.redo_stack:
                return {"status": "ok", "action": "redo", "redone_count": 0, "message": "Redo stack empty"}
            stashed = self.redo_stack.pop()
            restored_ids = []
            for item in stashed:
                eid = item["id"]
                self.elements[eid] = item
                restored_ids.append(eid)
            self.history.append(restored_ids)
            self.update_visibility()
            return {"status": "ok", "action": "redo", "restored_ids": restored_ids, "active": len(self.elements)}

        elif cmd == "erase":
            target_id = req.get("id")
            if target_id and target_id in self.elements:
                del self.elements[target_id]
                self.update_visibility()
                return {"status": "ok", "action": "erase", "id": target_id}
            return {"status": "not_found", "action": "erase", "id": target_id}

        elif cmd == "list":
            return {"status": "ok", "action": "list", "elements": list(self.elements.values())}

        elif cmd == "exit":
            GLib.idle_add(Gtk.main_quit)
            return {"status": "ok", "action": "exit"}

        return {"status": "error", "message": f"Unknown command: '{cmd}'"}

    def remove_element(self, elem_id: str) -> bool:
        """GLib timeout callback to auto-fade/remove an element."""
        if elem_id in self.elements:
            del self.elements[elem_id]
            self.update_visibility()
        return False

    def on_draw(self, widget, cr: cairo.Context):
        """Render active vector primitives onto the transparent canvas."""
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        alloc = widget.get_allocation()
        win_w = alloc.width
        win_h = alloc.height

        for elem in self.elements.values():
            etype = elem.get("type", "").lower()
            coords = elem.get("coords", [])
            color = hex_to_rgba(elem.get("color", "#00FF66"))
            width = float(elem.get("width", 3.5))

            def to_px(val, dim):
                return val * dim if 0.0 <= val <= 1.0 else val

            if etype in ["circle", "ellipse"]:
                if len(coords) >= 2:
                    cx = to_px(coords[0], win_w)
                    cy = to_px(coords[1], win_h)
                    rx = to_px(coords[2], win_w) if len(coords) > 2 else float(elem.get("radius", 40))
                    ry = to_px(coords[3], win_h) if len(coords) > 3 else rx

                    cr.save()
                    cr.translate(cx, cy)
                    cr.scale(rx, ry)
                    cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                    cr.restore()

                    if elem.get("fill"):
                        fill_color = hex_to_rgba(elem["fill"])
                        cr.set_source_rgba(*fill_color)
                        cr.fill_preserve()

                    # Neon glow stroke
                    cr.set_source_rgba(color[0], color[1], color[2], color[3] * 0.4)
                    cr.set_line_width(width * 2.2)
                    cr.stroke_preserve()

                    cr.set_source_rgba(*color)
                    cr.set_line_width(width)
                    cr.stroke()

            elif etype == "rect":
                if len(coords) >= 4:
                    x = to_px(coords[0], win_w)
                    y = to_px(coords[1], win_h)
                    w = to_px(coords[2], win_w)
                    h = to_px(coords[3], win_h)
                    radius = float(elem.get("corner_radius", 8.0))

                    cr.new_sub_path()
                    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
                    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
                    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
                    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
                    cr.close_path()

                    if elem.get("fill"):
                        cr.set_source_rgba(*hex_to_rgba(elem["fill"]))
                        cr.fill_preserve()

                    cr.set_source_rgba(color[0], color[1], color[2], color[3] * 0.35)
                    cr.set_line_width(width * 2.0)
                    cr.stroke_preserve()

                    cr.set_source_rgba(*color)
                    cr.set_line_width(width)
                    cr.stroke()

            elif etype == "arrow":
                if len(coords) >= 4:
                    x1 = to_px(coords[0], win_w)
                    y1 = to_px(coords[1], win_h)
                    x2 = to_px(coords[2], win_w)
                    y2 = to_px(coords[3], win_h)
                    head_size = float(elem.get("head_size", 16.0))
                    angle = math.atan2(y2 - y1, x2 - x1)

                    cr.set_source_rgba(*color)
                    cr.set_line_width(width)
                    cr.move_to(x1, y1)
                    cr.line_to(x2, y2)
                    cr.stroke()

                    cr.new_path()
                    cr.move_to(x2, y2)
                    cr.line_to(
                        x2 - head_size * math.cos(angle - math.pi / 6),
                        y2 - head_size * math.sin(angle - math.pi / 6)
                    )
                    cr.line_to(
                        x2 - head_size * math.cos(angle + math.pi / 6),
                        y2 - head_size * math.sin(angle + math.pi / 6)
                    )
                    cr.close_path()
                    cr.fill()

            elif etype in ["line", "freehand"]:
                pts = elem.get("points", coords)
                if len(pts) >= 2:
                    cr.set_source_rgba(*color)
                    cr.set_line_width(width)
                    cr.set_line_cap(cairo.LINE_CAP_ROUND)
                    cr.set_line_join(cairo.LINE_JOIN_ROUND)

                    start = pts[0]
                    cr.move_to(to_px(start[0], win_w), to_px(start[1], win_h))
                    for pt in pts[1:]:
                        cr.line_to(to_px(pt[0], win_w), to_px(pt[1], win_h))
                    cr.stroke()

            elif etype == "highlight":
                if len(coords) >= 4:
                    x = to_px(coords[0], win_w)
                    y = to_px(coords[1], win_h)
                    w = to_px(coords[2], win_w)
                    h = to_px(coords[3], win_h)

                    hl_color = hex_to_rgba(elem.get("color", "rgba(255, 235, 59, 0.4)"))
                    cr.set_source_rgba(*hl_color)
                    cr.rectangle(x, y, w, h)
                    cr.fill()

            elif etype == "text":
                if len(coords) >= 2:
                    tx = to_px(coords[0], win_w)
                    ty = to_px(coords[1], win_h)
                    text_str = str(elem.get("text", ""))
                    font_size = float(elem.get("font_size", 14.0))

                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                    cr.set_font_size(font_size)
                    extents = cr.text_extents(text_str)

                    pad = 8.0
                    pill_w = extents.width + pad * 2
                    pill_h = extents.height + pad * 2
                    pill_x = tx - pad
                    pill_y = ty - extents.height - pad

                    cr.set_source_rgba(0.08, 0.08, 0.12, 0.88)
                    cr.rectangle(pill_x, pill_y, pill_w, pill_h)
                    cr.fill()

                    cr.set_source_rgba(*color)
                    cr.set_line_width(1.5)
                    cr.rectangle(pill_x, pill_y, pill_w, pill_h)
                    cr.stroke()

                    cr.move_to(tx, ty)
                    cr.show_text(text_str)

            elif etype == "math_card":
                card_x = to_px(coords[0], win_w) if len(coords) >= 2 else (win_w - 380)
                card_y = to_px(coords[1], win_h) if len(coords) >= 2 else 60
                card_w = 340.0
                title = elem.get("title", "GHOST MATHEMATICS")
                expression = elem.get("expression", "")
                solution = elem.get("solution", "")
                steps = elem.get("steps", [])

                lines_count = len(steps) + 3
                card_h = 40.0 + (lines_count * 20.0)

                cr.set_source_rgba(0.06, 0.07, 0.1, 0.94)
                cr.rectangle(card_x, card_y, card_w, card_h)
                cr.fill()

                cr.set_source_rgba(0.0, 1.0, 0.55, 0.9)
                cr.set_line_width(2.0)
                cr.rectangle(card_x, card_y, card_w, card_h)
                cr.stroke()

                cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(12.0)
                cr.set_source_rgba(0.0, 1.0, 0.55, 1.0)
                cr.move_to(card_x + 14, card_y + 24)
                cr.show_text(f"[ {title} ]")

                cr.set_font_size(13.0)
                cr.set_source_rgba(0.9, 0.92, 0.96, 1.0)
                cr.move_to(card_x + 14, card_y + 48)
                cr.show_text(f"Expr: {expression}")

                cr.set_source_rgba(0.3, 0.8, 1.0, 1.0)
                cr.move_to(card_x + 14, card_y + 70)
                cr.show_text(f"Solution: {solution}")

                curr_y = card_y + 92
                cr.set_font_size(10.5)
                cr.set_source_rgba(0.75, 0.78, 0.82, 0.9)
                for step in steps[:6]:
                    clean_step = str(step).replace("`", "")[:42]
                    cr.move_to(card_x + 14, curr_y)
                    cr.show_text(clean_step)
                    curr_y += 18

            elif etype == "graph_plot":
                pts = elem.get("points", [])
                if len(pts) >= 2:
                    origin_x = to_px(coords[0], win_w) if len(coords) >= 2 else (win_w / 2)
                    origin_y = to_px(coords[1], win_h) if len(coords) >= 2 else (win_h / 2)
                    scale_x = float(elem.get("scale_x", 35.0))
                    scale_y = float(elem.get("scale_y", 35.0))

                    cr.set_source_rgba(0.3, 0.4, 0.5, 0.6)
                    cr.set_line_width(1.0)
                    cr.move_to(origin_x - 180, origin_y)
                    cr.line_to(origin_x + 180, origin_y)
                    cr.move_to(origin_x, origin_y - 120)
                    cr.line_to(origin_x, origin_y + 120)
                    cr.stroke()

                    cr.set_source_rgba(*color)
                    cr.set_line_width(width)
                    first = pts[0]
                    cr.move_to(origin_x + (first[0] * scale_x), origin_y - (first[1] * scale_y))
                    for pt in pts[1:]:
                        cr.line_to(origin_x + (pt[0] * scale_x), origin_y - (pt[1] * scale_y))
                    cr.stroke()


def run_overlay_daemon():
    """Main daemon runner."""
    app = ScreenOverlayApp()
    logger.info("Ghost Screen Overlay daemon initialized (window unmapped until elements drawn)...")
    try:
        Gtk.main()
    finally:
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
        if os.path.exists(PID_FILE):
            try:
                os.unlink(PID_FILE)
            except OSError:
                pass


if __name__ == "__main__":
    run_overlay_daemon()
