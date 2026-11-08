"""Section Kit — Ghost's component library (the Webild steal, done the Ghost way).

Webild's quality ceiling comes from COMPOSING premium pre-built sections rather
than generating raw HTML. Ghost's version is stricter: sections live as
validated, individually-addressable directories under ``templates/sections/``,
and a deterministic composer assembles them into the demo page. The LLM (and
everyone else) selects and fills; nobody invents structure.

Anatomy of one component (see templates/sections/README.md)::

    templates/sections/<name>/
      SECTION.md      # manifest — <!-- section ... --> header (capability format)
      section.html    # Jinja2 markup (autoescaped; dossier strings can't inject)
      section.css     # optional — no url()/@import (CSP-safe, zero external fetches)
      section.js      # optional — no fetch/XHR/eval/inline handlers

Behavior (booking link updates, theme, entrance motion, lead form, svg decor)
lives in ``_base/reveal.js`` — sections are markup + styles only.

Contracts enforced here (the house rules, structurally):
- booking element ids (``booking-service/detail/date``, ``whatsapp-direct-btn``)
- ``.reveal-el`` / ``.spring-card`` motion classes
- archetype design tokens as CSS variables (``_base/tokens.css``)
- photo doctrine: missing slots render ``[Inquire for quotation: photography]``
- ONE svg.js decor element (a hand-drawn divider whose static stroke ships in
  the markup — the page is complete with JS disabled)
"""
import datetime
import json
import logging
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment
from jinja2 import meta as jinja_meta
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger("ghost.workers.section_kit")

SECTION_HEADER_RE = re.compile(r"<!--\s*section(.*?)-->", re.S)
INQUIRE_PHOTOGRAPHY = "[Inquire for quotation: photography]"


class SectionValidationError(Exception):
    """A kit component broke a house rule — refuse, never warn."""


def sections_dir() -> str:
    env = os.environ.get("GHOST_SECTIONS_DIR")
    if env:
        return env
    from ghost.paths import repo_root
    return os.path.join(repo_root(), "templates", "sections")


# ---------------------------------------------------------------------------
# The reference library (_library) — raw sources the operator drops in
# ---------------------------------------------------------------------------

_LIBRARY_HEADER_RE = re.compile(r"<!--\s*library(.*?)-->", re.S)


def library_dir() -> str:
    env = os.environ.get("GHOST_LIBRARY_DIR")
    if env:
        return env
    from ghost.paths import repo_root
    return os.path.join(sections_dir(), "_library")


def library_index() -> List[Dict[str, Any]]:
    """Index of the reference component library (templates/sections/_library/*):
    raw downloaded sources (React/motion components, standalone vanilla pages)
    organized by category (heroes, auth, cards, maps, modals, effects, ...).
    These are ADAPTATION MATERIAL, never rendered directly — the kit validates
    vanilla HTML/CSS/JS only, so an item must be hand-adapted into a real
    section before a build can use it."""
    root = library_dir()
    items: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return items
    for cat in sorted(os.listdir(root)):
        cdir = os.path.join(root, cat)
        if not os.path.isdir(cdir) or cat.startswith("_") or cat.startswith("."):
            continue
        for name in sorted(os.listdir(cdir)):
            mpath = os.path.join(cdir, name, "MANIFEST.md")
            if not os.path.isfile(mpath):
                continue
            with open(mpath, "r", encoding="utf-8") as f:
                content = f.read()
            meta: Dict[str, Any] = {}
            m = _LIBRARY_HEADER_RE.search(content)
            if m:
                for line in m.group(1).strip().splitlines():
                    if ":" in line:
                        key, _, val = line.partition(":")
                        meta[key.strip()] = val.strip()
            items.append({
                "name": meta.get("name", name),
                "category": cat,
                "source": meta.get("source", ""),
                "kind": meta.get("kind", ""),
                "license": meta.get("license", ""),
                "summary": meta.get("summary", ""),
                "has_code": meta.get("files", "none") not in ("", "none"),
            })
    return items


def parse_section_header(content: str) -> Dict[str, Any]:
    """Parse the ``<!-- section ... -->`` manifest header (capability format)."""
    cap: Dict[str, Any] = {}
    m = SECTION_HEADER_RE.search(content)
    if not m:
        return cap
    for line in m.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        cap[key.strip()] = val.strip()
    for list_key in ("slots", "optional_slots", "applies_to", "requires"):
        if list_key in cap:
            cap[list_key] = [t.strip().lower() for t in cap[list_key].split(",") if t.strip()]
    return cap


# Context keys every section may reference (the PageContext contract).
GLOBAL_CONTEXT_KEYS = frozenset({
    "slug", "business_name", "main_title", "sub_title", "tagline",
    "location", "city", "rating", "rating_badge",
    "phone", "clean_phone_digits", "has_phone", "is_uk_landline",
    "initial_booking_href", "initial_btn_text", "currency_symbol",
    "services", "has_services", "is_sample_services", "show_sample",
    "reviews", "hours", "faqs", "steps", "booking_fields",
    "photos_policy", "hero_bg", "bento_img1", "bento_img2",
    "gallery_1", "gallery_2", "gallery_3",
    "copy", "arch", "present_ids", "form_path", "philosophy_body_1",
})


class SectionSpec:
    """One kit component: manifest + sources."""

    def __init__(self, name: str, directory: str, manifest: Dict[str, Any],
                 html: str, css: str, js: str):
        self.name = name
        self.directory = directory
        self.manifest = manifest
        self.html = html
        self.css = css
        self.js = js

    @property
    def category(self) -> str:
        return str(self.manifest.get("category", "unknown"))

    @property
    def summary(self) -> str:
        return str(self.manifest.get("summary", ""))

    @property
    def slots(self) -> List[str]:
        return list(self.manifest.get("slots", []))

    @property
    def optional_slots(self) -> List[str]:
        return list(self.manifest.get("optional_slots", []))


_EXTERNAL_BANS_CSS = (re.compile(r"url\s*\(", re.I), re.compile(r"@import", re.I))
_EXTERNAL_BANS_CODE = (
    re.compile(r"fetch\s*\("), re.compile(r"XMLHttpRequest"),
    re.compile(r"\beval\s*\("), re.compile(r"document\.write"),
    re.compile(r"<script[^>]+src=[\"']https?:", re.I),
)
_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


def _validate_sources(spec: SectionSpec) -> None:
    """House rules: zero external fetches, zero inline handlers. Refuse."""
    for pattern in _EXTERNAL_BANS_CSS:
        if pattern.search(spec.css):
            raise SectionValidationError(
                f"section '{spec.name}': {pattern.pattern!r} in CSS — external "
                f"fetches are banned (CSP-safe house rule)")
    for pattern in _EXTERNAL_BANS_CODE:
        for what, blob in (("HTML", spec.html), ("JS", spec.js)):
            if pattern.search(blob):
                raise SectionValidationError(
                    f"section '{spec.name}': {pattern.pattern!r} in {what} — "
                    f"external/eval code is banned; behavior lives in _base/reveal.js")
    if _INLINE_HANDLER.search(spec.html) or _INLINE_HANDLER.search(spec.js):
        raise SectionValidationError(
            f"section '{spec.name}': inline on*= handler — behavior belongs in _base/reveal.js")


def _validate_template_vars(spec: SectionSpec, env: Environment) -> None:
    undeclared = jinja_meta.find_undeclared_variables(env.parse(spec.html))
    allowed = set(GLOBAL_CONTEXT_KEYS) | set(spec.slots) | set(spec.optional_slots) | {"context"}
    unknown = undeclared - allowed
    if unknown:
        raise SectionValidationError(
            f"section '{spec.name}': template references undeclared variables "
            f"{sorted(unknown)} — declare them in SECTION.md slots or use PageContext keys")


class SectionLibrary:
    """Discovers, validates and serves the kit. Discovery is automatic: a
    directory with SECTION.md under templates/sections/ IS a component."""

    def __init__(self, root: Optional[str] = None):
        self.root = root or sections_dir()
        self._specs: Dict[str, SectionSpec] = {}
        self._loaded = False

    def discover(self) -> Dict[str, SectionSpec]:
        if self._loaded:
            return self._specs
        for entry in sorted(os.listdir(self.root)):
            sdir = os.path.join(self.root, entry)
            manifest_path = os.path.join(sdir, "SECTION.md")
            if entry.startswith("_") or not os.path.isdir(sdir) or not os.path.isfile(manifest_path):
                continue
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = parse_section_header(f.read())
            if not manifest.get("name"):
                raise SectionValidationError(f"{manifest_path}: missing 'name' in the section header")
            html_path = os.path.join(sdir, "section.html")
            if not os.path.isfile(html_path):
                raise SectionValidationError(f"{manifest_path}: no section.html beside the manifest")
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            css = js = ""
            css_path = os.path.join(sdir, "section.css")
            js_path = os.path.join(sdir, "section.js")
            if os.path.isfile(css_path):
                with open(css_path, "r", encoding="utf-8") as f:
                    css = f.read()
            if os.path.isfile(js_path):
                with open(js_path, "r", encoding="utf-8") as f:
                    js = f.read()
            name = manifest["name"]
            if name != entry:
                raise SectionValidationError(
                    f"{manifest_path}: name '{name}' must match its directory '{entry}'")
            spec = SectionSpec(name, sdir, manifest, html, css, js)
            env = SandboxedEnvironment(autoescape=True)
            _validate_template_vars(spec, env)
            _validate_sources(spec)
            env.parse(spec.html)  # Jinja syntax must parse — refuse broken markup
            self._specs[name] = spec
        self._loaded = True
        return self._specs

    def get(self, name: str) -> SectionSpec:
        specs = self.discover()
        if name not in specs:
            raise KeyError(f"section '{name}' is not in the kit ({sorted(specs)})")
        return specs[name]

    def names(self) -> List[str]:
        return sorted(self.discover().keys())


# ---------------------------------------------------------------------------
# Planning — deterministic selection with written reasons (need/pay culture)
# ---------------------------------------------------------------------------

def plan_sections(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ordered build plan. Every include/omit decision carries its reason —
    the same written-reasons culture as need/pay scoring."""
    gallery_slots = [ctx.get(k) for k in ("gallery_1", "gallery_2", "gallery_3")]
    has_services = bool(ctx.get("has_services"))
    is_sample = bool(ctx.get("is_sample_services"))
    show_sample = bool(ctx.get("show_sample"))
    reviews = ctx.get("reviews") or []

    plan: List[Dict[str, Any]] = [
        {"name": "nav_floating", "reason": "page chrome", "include": True},
        {"name": "hero_editorial",
         "reason": ("real photo hero — the business's own staged image"
                    if ctx.get("hero_bg") else
                    "typography-led hero — no real photo staged, none invented"),
         "include": True},
        {"name": "gap_banner", "reason": "value strip", "include": True},
        {"name": "story_philosophy", "reason": "voice + standards", "include": True},
        {"name": "services_bento",
         "reason": ("verified scraped services" if has_services and not is_sample else
                    "labelled sample layout" if is_sample and show_sample else ""),
         "include": (has_services and not is_sample) or (is_sample and show_sample)},
        {"name": "proof_reviews",
         "reason": f"{len(reviews)} scraped review(s)" if reviews else "",
         "include": bool(reviews)},
        {"name": "gallery_strip",
         "reason": (f"{sum(1 for g in gallery_slots if g)} real photo slot(s)" if any(gallery_slots) else ""),
         "include": any(gallery_slots)},
        {"name": "journey_steps", "reason": "engagement protocol", "include": True},
        {"name": "booking_module", "reason": "direct-inquiry contract", "include": True},
        {"name": "faq_answers", "reason": "answer-first FAQ (AEO)",
         "include": bool(ctx.get("faqs"))},
        {"name": "hours_location", "reason": "NAP block (LocalBusiness consistency)",
         "include": bool(ctx.get("location"))},
        {"name": "lead_capture", "reason": "the leads inbox — every site captures leads",
         "include": True},
        {"name": "footer_mono", "reason": "closing wordmark", "include": True},
    ]
    return plan


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def _wobble_path(width: int = 560) -> str:
    """A deterministic hand-wobble line: irregular amplitude, never a sine."""
    d = "M 4 12"
    x, i = 4, 0
    while x < width - 8:
        step = 22 + ((i * 37) % 19)
        dy = (1 if i % 2 == 0 else -1) * (2 + ((i * 13) % 5)) * 0.9
        x += step
        d += f" Q {x - step / 2:.1f} {12 + dy * 2:.1f} {x:.1f} {12 + dy * 0.4:.1f}"
        i += 1
    return d


def _base_env(autoescape: bool) -> Environment:
    return SandboxedEnvironment(autoescape=autoescape)


def _render_base(template: str, values: Dict[str, Any], autoescape: bool) -> str:
    with open(os.path.join(sections_dir(), "_base", template), "r", encoding="utf-8") as f:
        src = f.read()
    return _base_env(autoescape).from_string(src).render(**values)


def ensure_vendor(out_dir: str) -> str:
    """svg.min.js rides into the demo (idempotent), WITH its provenance row in
    SOURCES.json — an unledgered asset is exactly the kind of thing the
    evidence gates (and the 2026-11-08 executor loop) choke on."""
    from ghost.paths import repo_root
    vendor_dir = os.path.join(out_dir, "assets", "vendor")
    os.makedirs(vendor_dir, exist_ok=True)
    dst = os.path.join(vendor_dir, "svg.min.js")
    src = os.path.join(repo_root(), "templates", "svg", "svg.min.js")
    if os.path.isfile(src) and not os.path.isfile(dst):
        shutil.copy(src, dst)

    sources_path = os.path.join(out_dir, "SOURCES.json")
    rel = "assets/vendor/svg.min.js"
    row = {
        "type": "vendor_library",
        "file": rel,
        "source_url": "https://github.com/svgdotjs/svg.js",
        "license": "MIT (svgdotjs/svg.js v3.2.4)",
        "scraped_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verified": True,
        "notes": "Vendored locally from templates/svg/ — no CDN, CSP-safe.",
    }
    try:
        if os.path.isfile(sources_path):
            with open(sources_path, "r", encoding="utf-8") as f:
                sources = json.load(f)
        else:
            sources = {"assets": []}
        assets = sources.setdefault("assets", [])
        if not any(a.get("file") == rel and a.get("type") == "vendor_library" for a in assets):
            assets.append(row)
            with open(sources_path, "w", encoding="utf-8") as f:
                json.dump(sources, f, indent=1)
    except Exception:
        pass  # ledger is provenance, not build-critical — never break the build
    return dst


def compose_page(plan: List[Dict[str, Any]], ctx: Dict[str, Any], arch: Dict[str, Any],
                 starter_kit: str = "") -> Tuple[str, List[Dict[str, Any]]]:
    """Assemble the full page from planned sections. Returns (html, records)
    where records feed SECTIONS.json — the address map the edit lane targets."""
    library = SectionLibrary()
    library.discover()

    present_ids = [p["name"] for p in plan if p.get("include")]
    ctx = dict(ctx)
    ctx["present_ids"] = present_ids

    # tokens.css carries Ghost-owned archetype values — rendered unescaped.
    tokens_css = _render_base("tokens.css", {"arch": arch}, autoescape=False)

    rendered: List[str] = []
    records: List[Dict[str, Any]] = []
    # The ONE svg.js decor — static stroke in the markup (no-JS complete),
    # animated once on view when motion is allowed. It sits between the
    # engagement story and the ask (journey -> booking; falls back to just
    # before the footer when booking is absent).
    divider_svg = (
        '<div class="py-10 text-center" aria-hidden="true">'
        f'<svg id="kit-divider-svg" class="kit-divider" viewBox="0 0 560 24" '
        f'preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">'
        f'<path d="{_wobble_path()}"/></svg></div>'
    )
    for item in plan:
        if not item.get("include"):
            continue
        if item["name"] == "booking_module" or (item["name"] == "footer_mono" and "booking_module" not in present_ids):
            rendered.append(divider_svg)
        spec = library.get(item["name"])
        env = SandboxedEnvironment(autoescape=True)
        html = env.from_string(spec.html).render(**ctx, context=ctx)
        rendered.append(html)
        if spec.css:
            rendered.append(f"<style>{spec.css}</style>")
        records.append({
            "name": spec.name,
            "category": spec.category,
            "source": os.path.relpath(spec.directory, os.path.dirname(sections_dir())),
            "slots_used": sorted((set(spec.slots) | set(spec.optional_slots)) & set(ctx.keys())),
        })
    sections_html = "\n".join(rendered)

    business = {
        "name": ctx.get("main_title", ""),
        "phone": ctx.get("phone", "") or "",
        "cleanPhone": ctx.get("clean_phone_digits", "") or "",
        "isLandline": bool(ctx.get("is_uk_landline")),
        "hasPhone": bool(ctx.get("has_phone")),
    }
    reveal_js = _render_base("reveal.js", {"business": business}, autoescape=False)

    html = _render_base("shell.html", {
        "page_title": f"{ctx.get('main_title', '')} — {ctx.get('tagline', '')}",
        "starter_kit": starter_kit,
        "tokens_css": tokens_css,
        "sections_html": sections_html,
        "divider_svg": "",
        "reveal_js": reveal_js,
    }, autoescape=True)
    return html, records


def write_section_map(out_dir: str, records: List[Dict[str, Any]], ctx: Dict[str, Any]) -> str:
    """SECTIONS.json — per-demo component map (stable ids for the edit lane)."""
    payload = {
        "built_with": "section_kit/1",
        "slug": ctx.get("slug", ""),
        "sections": records,
        "contracts": {
            "booking_ids": ["booking-service", "booking-detail", "booking-date",
                            "whatsapp-direct-btn", "booking-btn-text"],
            "motion_classes": [".reveal-el", ".spring-card"],
            "decor": "one svg.js hand-drawn divider (#kit-divider-svg)",
        },
    }
    path = os.path.join(out_dir, "SECTIONS.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path
