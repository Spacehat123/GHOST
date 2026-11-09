# Autonomous Screen Drawing & Mathematics Engine Documentation

## 1. Overview
This upgrade provides Simon 'Ghost' Riley with two major autonomous capabilities:
1. **Autonomous Screen Drawing Engine (`screen_draw`)**: Wayland-native transparent desktop overlay (Arch Linux + Hyprland) built using `GtkLayerShell` and `Cairo`. Ghost can draw circles, arrows, bounding boxes, text annotations, highlights, mathematical cards, and function plots with 100% click-through transparency. Includes a 4-tier desktop targeting engine that prioritizes exact window coordinates (`hyprctl`) and web DOM geometry (`Brave CDP`) over raw screen capture vision.
2. **Local Mathematics Engine (`math_solve`)**: Pure local computation powered by `SymPy` and `mpmath` with a security-hardened AST parser (strictly zero `eval()`). Solves equations, simplifies radicals and nested fractions, computes calculus (derivatives and integrals), generates step-by-step reasoning, distinguishes exact symbolic results from decimal approximations, and independently verifies solutions using back-substitution and calculus inversion.
3. **Tiered Tool & Prompt Optimization**: Slashed Ghost's baseline per-turn prompt from **~13,600 tokens down to ~4,460 tokens (67% reduction)** by introducing tiered toolsets (`CORE_TOOLS` vs `GHOST_TOOLS`) and modularizing the base system prompt.

---

## 2. Architecture & File Structure

```
/home/pranc/projects/ghost/
├── src/ghost/
│   ├── screen/
│   │   ├── __init__.py        # Exports ScreenOverlayClient and TargetingEngine
│   │   ├── overlay.py         # Wayland GtkLayerShell + Cairo daemon (/tmp/ghost_screen_overlay.sock)
│   │   ├── client.py          # Async IPC client with auto-spawning lifecycle management
│   │   └── targeting.py       # 4-tier desktop and web DOM targeting engine
│   ├── math/
│   │   ├── __init__.py        # Exports SafeMathParser, MathEngine, MathVerifier
│   │   ├── parser.py          # Security AST validator with strict operator & function whitelist
│   │   ├── engine.py          # SymPy solver, step generator, and 2D coordinate sampler
│   │   └── verifier.py        # Independent verification layer (back-substitution & calculus check)
│   └── core/
│       ├── brain.py           # GHOST_TOOLS, CORE_TOOLS, get_ghost_tools(), tool dispatch handlers
│       └── agent.py           # Uses tiered tools per context; token reduction
├── tests/
│   ├── test_math_engine.py    # Unit tests for radicals, algebra, calculus, AST security
│   └── test_screen_overlay.py # Unit tests for IPC client, vector rendering, targeting
└── docs/
    └── screen_drawing_and_math_engine.md # Architecture and usage guide
```

---

## 3. Autonomous Screen Drawing Subsystem

### 3.1 Technology & Protocols
- **Wayland Layer**: `GtkLayerShell.Layer.OVERLAY` anchors to all four monitor bounds.
- **Click-Through Mode**: Empty Cairo input region `gdk_win.input_shape_combine_region(cairo.Region(), 0, 0)` guarantees that all mouse clicks, scrolls, and keystrokes pass straight through to underlying applications.
- **IPC Protocol**: Unix Domain Socket at `/tmp/ghost_screen_overlay.sock`. Receives streaming JSON commands and returns execution receipts.

### 3.2 Supported Primitives
- `circle` / `ellipse`: Center `[cx, cy]`, radii, stroke width, color, fill, neon glow.
- `rect`: `[x, y, w, h]`, rounded corners, fill, stroke.
- `arrow`: Start `[x1, y1]`, end `[x2, y2]`, arrowhead angle and size.
- `line` / `freehand`: Points array `[[x1, y1], [x2, y2], ...]`, smoothed joins and caps.
- `text`: `[x, y]`, content, font size, color, background badge pill.
- `highlight`: Semi-transparent highlighter marker band.
- `math_card`: Technical on-screen card displaying title, equation, solution badge, and step list.
- `graph_plot`: Plotted 2D function curve $y = f(x)$ over coordinate axes.

### 3.3 4-Tier Targeting Hierarchy ("Better Than Screen Capture")
Instead of taking slow, lossy screenshots with vision model hallucination:
1. **Tier 1 (Web)**: Brave CDP (`:9222`) queries `getBoundingClientRect()` + Hyprland window origin. Sub-pixel exact DOM button/input targeting.
2. **Tier 2 (Apps)**: Hyprland IPC (`hyprctl activewindow -j` / `clients -j`) extracts exact `[x, y, w, h]` bounding boxes for any window.
3. **Tier 3 (Cursor)**: Hyprland `cursorpos` anchors annotations beside the mouse.
4. **Tier 4 (Fallback)**: `grim` visual capture for uninstrumented graphics.

---

## 4. Local Mathematics Engine Subsystem

### 4.1 Security Boundaries
- Strictly zero `eval()` or `exec()`.
- Input is sanitized and parsed into an `ast.Expression` abstract syntax tree.
- `ast.walk` verifies that every node belongs to the strict whitelist (`Expression`, `BinOp`, `UnaryOp`, `Call`, `Name`, `Constant`, `Compare`).
- Attributes (dot notation `.` like `__class__`), imports, statements, lambdas, and unauthorized function calls are immediately rejected with `UnsafeExpressionError`.

### 4.2 Mathematical Capabilities
- Radicals & Arithmetic: $\sqrt{144} = 12$, $\sqrt{25 + 144} = 13$, $3(8 - 2)^2 / 6 = 18$, nested fractions.
- Equations: $x^2 - 5x + 6 = 0 \implies x \in \{2, 3\}$.
- Calculus: $\frac{d}{dx}(\sin(x)\cdot e^x) = (\cos(x) + \sin(x))e^x$, $\int x^2 dx = \frac{x^3}{3}$.
- Exact vs. Decimal: Always returns both exact symbolic representation and high-precision decimal evaluation.
- Independent Verification:
  - Back-substitution: Substitutes candidate roots back into original equation ($|LHS - RHS| < 10^{-9}$).
  - Calculus check: Differentiates indefinite integrals to verify equivalence with the integrand.
  - High-precision mpmath cross-check.

---

## 5. Tool Usage Examples for Agent

### Math Calculation & Screen Projection
```json
{
  "name": "math_solve",
  "arguments": {
    "expression": "√(25 + 144)",
    "operation": "evaluate",
    "draw_on_screen": true,
    "duration": 5.0
  }
}
```

### Screen Targeting & Drawing
```json
{
  "name": "screen_draw",
  "arguments": {
    "action": "target_and_draw",
    "target": {
      "type": "active_window"
    },
    "duration": 4.0
  }
}
```
