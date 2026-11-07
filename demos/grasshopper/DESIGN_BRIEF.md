# BESPOKE DESIGN BRIEF & ARCHITECTURAL BLUEPRINT
**Establishment:** Grasshopper  
**Domain:** restaurants_cafes (Service)  
**Location / Market:** Grasshopper Area  
**Assigned Archetype:** Archetype 1 — Michelin & Fine Dining Editorial  
**Classification Confidence:** 1.00 (Needs confirmation: False)

---

## 1. Design System & Semantic Color Tokens
```css
:root {
  /* 60/30/10 Rule Hierarchy */
  --canvas: #fcfbf9;
  --surface: #f5f2eb;
  --raised: #ffffff;
  --ink: #1a1816;
  --ink-2: #68645e;
  --accent: #997328;
  --accent-secondary: #b88e3d;
  --line: #e5ded3;
}

:root[data-theme="dark"] {
  --canvas: #0c0c0e;
  --surface: #16161a;
  --raised: #2A2A2A;
  --ink: #eae6df;
  --ink-2: #8e8a82;
  --accent: #d4af37;
  --accent-secondary: #c5a059;
  --line: #272521;
}
```

## 2. Typography Hierarchy
- **Headline Display:** `'Playfair Display', serif`
- **Body & Technical Specs:** `'Plus Jakarta Sans', sans-serif`
- **Monospace Accents:** `'JetBrains Mono', 'Fira Code', monospace`

## 3. Section Flow
1. Floating Pill Navigation with Light/Dark Switcher
2. Hero Viewport with Clamped Mobile Headline
3. Operational Value Banner
4. Craft & Standards Philosophy
5. Bento Grid Offerings (Source-backed)
6. Authentic Reviews Section (Omitted if 0 reviews)
7. Space & Facility Gallery
8. How It Works (Domain-tailored 3 steps)
9. Active Interactive Booking Module (Live JS WhatsApp & Call sync)
10. Conversion Footer with Removable Concept Attribution


---

## HOUSE STYLE OVERRIDE — binding editorial standard

The section builder MUST apply the standard below over any archetype default it conflicts with. Photography rule: use ONLY the staged local paths (`assets['hero_bg']`, `bento_img1/2`, `gallery_1..3` when they point at `assets/photos/`) — they are THE BUSINESS'S OWN photos. Never hotlink stock — stock images are banned entirely. A photo slot WITHOUT a staged local path renders as `[Inquire for quotation: photography]`, never a generic image.


### PHOTOGRAPHY MODE: NONE AVAILABLE (binding)
No real photos were staged. Build the PHOTOGRAPHY-FREE editorial variant: typography-led (giant ghosted wordmark, serif hierarchy, hairline rules, micro-caps meta), generous whitespace, and where a photo would go render a bordered slot reading `[Inquire for quotation: photography]`. STOCK IMAGES PRESENTED AS THIS BUSINESS ARE FORBIDDEN — a generic photo carrying a specific menu/product claim is a fabricated fact and fails QA claims lint.

```markdown
<!-- capability
name: editorial_web_design
summary: The house look for every demo/client site: warm paper, serif voice, one earth accent, ghosted wordmark, real photos; hard bans (Tailwind blue, 3-card rows, AI images).
triggers: website, web site, build a site, landing page, demo, design, look, style, redesign, hero, template
applies_to: build_website, build_demo, fix_site, design_review
-->

# SKILL: Editorial Web Design — the Ghost house style

Source standard: the Grasshopper reference (operator-approved Nov 2026) + distillations
from `.skill-vendor/` (jakubkrehel better-typography/colors/layout/interface, MengTo
web-design, tastemaker, garden-skills web-design-engineer). This is the DEFAULT look
for every demo and client website Ghost builds. "Old school" templates are banned.

## The one-line rule
An editorial magazine page, not a SaaS template: warm paper, one serif voice, one
earth accent, photography doing the talking, and enough whitespace to be expensive.

## Palette system
- Paper: `#faf8f2` (warm cream) or `#fcfbf9`; never pure white, never cool gray.
- Ink: `#1a1a18` for display, `#3c3a35` for body. Never `#000`.
- ONE earthy accent per business (from their actual brand/interior photos: olive,
  ochre, terracotta, oxblood). Accent is for buttons + tiny meta only — never large
  fills, never gradients.
- Ghosted wordmark: business name set enormous (18–30vw), 3–5% opacity, behind the
  hero. One per page.
- Dark mode: full toggle (functional, class-based, `localStorage`), ink/paper
  inverted to `#141310` / `#f1ede4`. NOT an afterthought — ship it.

## Typography
- Display: a high-contrast serif (Playfair Display / Fraunces / Libre Caslon) for
  the business name + section headlines. Tight leading (0.95–1.05), real italics
  for the descriptor line ("Slow Food • Artisanal Seclusion • Unhurried Discipline").
- Body: humanist sans (Inter / Söhne / system) 16–18px, 1.6 line-height, max 62ch.
- Micro-caps meta rows: 10–11px, +0.18em tracking, uppercase, separated by thin
  dots — address/category/tagline rows, footer legal lines.
- Type scale (desktop): 96/64/40/24/18/16. Mobile: 56/40/28/20/17/16.
- NEVER: Inter-for-headlines, centered everything, 5 font weights, letter-spaced
  body text.

## Layout grammar
- Single-column editorial flow, generous section padding (120–160px desktop,
  72–96px mobile).
- Floating pill nav: cream capsule, hairline border, logo left, 4–6 links center,
  one accent CTA right. Sticky with a subtle backdrop-blur after 80px scroll.
- Hero: meta-caps row → giant serif name → italic descriptor → 2-line mission →
  primary CTA + secondary ghost CTA → hairline rule → micro-caps trust row.
- Photography: full-bleed or asymmetric editorial crops (NOT uniform card grids).
  REAL photos of the business (see photos policy) — never generic stock for a
  business we photographed.
- Menu/services: numbered list with hairline rules and dotted-price alignment
  (hospitality) or two-column definition list (services).
- Footer: micro-caps contact block + hours + map link + legal. Boring on purpose.

## Section map (local business default)
1. Hero (above) → 2. Philosophy/About (photo + 2 paragraphs) → 3. The Offer
(menu/services/pricing proof) → 4. Atmosphere gallery (5–9 real photos, editorial
crop) → 5. Social proof (reviews, ratings, press) → 6. Visit (hours, map, parking,
contact) → 7. FAQ (feeds AEO — see aeo_answer_engines skill) → 8. Footer.

## Hard bans
Tailwind-blue, glassmorphism cards, uniform 3-card rows, emoji icons, drop shadows
over 8px blur, `border-radius > 16px` on content blocks, AI-generated people/food
images, stock hands-shaking photos, lorem ipsum, dead links, "Welcome to our
website" copy.

## Quality bar (acceptance)
Lighthouse ≥ 90 perf / ≥ 95 a11y / ≥ 100 SEO; passes the 5-second test ("what does
this business do, where is it, how do I book"); looks hand-made, not generated.
Deep dives: `.skill-vendor/skills-jakubkrehel/skills/better-*`,
`.skill-vendor/Skills/agent-skills/web-design`, `.skill-vendor/tastemaker`.

```


## SVG DECOR — available and bounded

`@svgdotjs/svg.js` v3.2.4 is vendored at `assets/vendor/svg.min.js` (load it with a normal `<script src>` if the design uses it). Allowed: ONE self-drawing line-art element, hand-drawn section dividers, CSS underlines. Follow the rules verbatim:

```markdown
<!-- capability
name: svg_decor
summary: svg.js decorative craft (vendored at assets/vendor/svg.min.js): self-drawing line-art, hand-drawn dividers, animated underlines — max 2 per page, draw-once, reduced-motion aware.
triggers: svg, animation, animate, draw, divider, underline, logo, line-art, decor, cool, polish, demo, website
applies_to: build_website, build_demo
-->

# SKILL: SVG Decor — self-drawing details (svg.js, the Grasshopper touch)

Operator reference: Grasshopper-class sites feel *hand-made* — a line-drawing that
draws itself on load, an underline that sweeps on hover, a slightly wobbly
hand-drawn divider between sections. Ghost ships `@svgdotjs/svg.js` v3.2.4 (MIT)
locally at `assets/vendor/svg.min.js` — no CDN, CSP-safe.

## The three patterns (and only these)

### 1. Self-drawing line art (hero/section)
A single-path line drawing (the logo mark, a dish outline, a tool, a skyline)
that strokes itself in once on entry. The whole trick:
```js
const draw = SVG().addTo('#hero-art').size('100%', 220)
const path = draw.path(dAttribute).fill('none')
  .stroke({ color: 'var(--accent)', width: 1.5, linecap: 'round' })
const len = path.length()
path.attr('stroke-dasharray', `${len} ${len}`)
    .attr('stroke-dashoffset', len)
    .animate(1400, 0, 'now').ease('-').attr('stroke-dashoffset', 0)
```
- ONE path (or a few, staggered 120ms), stroke-only, `fill: none`, width 1–2.
- Draw ONCE on first entry (IntersectionObserver, once:true). Never loop.
- The path data must be a REAL drawing of the business's subject — traced from
  their actual logo/subject, not abstract squiggles.

### 2. Animated underline (nav links, key phrases)
```js
link.each(function () {
  const line = this.remember('u') // an SVG <line> or path under the text
})
// CSS-only alternative (preferred — zero JS):
// .u::after { transform: scaleX(0); transform-origin: left; transition: transform 180ms }
// .u:hover::after { transform: scaleX(1) }
```
Prefer the pure-CSS `::after` scaleX underline (transform-only, 180ms, expo-out)
per the motion_craft skill. Reach for svg.js only when the underline must be
drawn (a brush/ink stroke path), and then: 250ms, once per hover-in, staggered
draw-out none.

### 3. Hand-drawn divider between sections
A full-width path with 1–3px of gentle hand wobble (not a sine wave — real
hand-wobble has irregular amplitude), stroke 1px, ink at 35% opacity, drawn
once when scrolled into view (500ms). Between major sections only — never
between every block.

## The rules (binding)
- Decorative SVG is `aria-hidden="true"` and carries NO information the page
  needs. If it's load-bearing, it's not decoration.
- Max TWO drawn elements per page (one hero line-art + one divider) plus the
  CSS underlines. More is noise.
- Colors: stroke only, from the page's own palette (`var(--accent)` / ink at
  reduced opacity). No gradients, no glows, no fills on line art.
- `prefers-reduced-motion: reduce` → skip the draw-in entirely (render the
  finished stroke). Implement with a media check, not a promise.
- Draw-in duration 800–1400ms, expo-out ease, once. Everything transform/
  stroke-dashoffset — no layout reads per frame.
- The page must be COMPLETE and beautiful with JS disabled: the SVG decor is
  the garnish, never the meal. Ship the static stroke as the no-JS default
  (dashoffset 0), and only animate when JS + motion are allowed.
- Mobile: the hero line-art scales down or drops; dividers stay.

## Anti-ban list
Looping ambient animation · draw-in on every scroll re-entry · animated logos
that never finish · confetti particles · blob morphs · cursor trails ·
anything that moves while the user reads.

Deep dive: `.skill-vendor/svg.js/` (src + spec) — but the three patterns above
cover everything a Ghost site needs.

```
