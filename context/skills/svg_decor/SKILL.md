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
