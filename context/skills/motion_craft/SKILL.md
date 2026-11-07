<!-- capability
name: motion_craft
summary: Animation tokens + restraint (emilkowalski): 120-450ms, expo-out, transform-only, choreography, reduced-motion, where motion is banned.
triggers: animation, animate, motion, transition, polish, feel, demo, website, scroll
applies_to: build_website, build_demo, fix_site
-->

# SKILL: Motion Craft — animation restraint (emilkowalski distillation)

Source: `.skill-vendor/skills-emilkowalski/` (animate, choreograph,
animation-performance, ui-cheatsheet) — the best public motion craft on the internet.
Ghost sites use motion to signal change, never to decorate.

## Tokens (copy into every build)
- Durations: hover 120–180ms · enter 200–300ms · exit 150–200ms · page 300–450ms.
- Easing: enter `cubic-bezier(0.16, 1, 0.3, 1)` (expo-out) · exit `ease-in` ·
  micro `ease-out`. NEVER `linear` for UI motion, never bounce on content.
- Distance: 8–24px translate. Anything flying 200px is a circus.

## Choreography
- Stagger children 30–60ms, total sequence under 500ms. The eye reads one
  composition, not N animations.
- Animate ONCE on first entry (IntersectionObserver, `once: true`). Scroll-triggered
  re-runs read as desperation.
- transform + opacity ONLY. Animating `top/left/width/height` = jank on a ₹25k site.
- `prefers-reduced-motion: reduce` → kill every non-essential animation. Ship it
  in the same stylesheet, not as a promise.

## Where motion EARNED its place on Ghost sites
- Nav capsule: backdrop-blur + border darkens after 80px (one transition).
- Hero: 1 stagger-in on load (name → descriptor → CTAs).
- Gallery: crossfade or scale-1.03 on hover, 200ms.
- Dark-mode toggle: 200ms color transition.
- Menu/booking CTA: 120ms press feedback.

## Where motion is BANNED
- Autoplay carousels, parallax on text, animated counters, typing effects,
  spinning badges, pre-loaders over 400ms, anything that moves while the user
  is reading.

## Test
Turn the animations off — if the site gets worse to USE (not just less flashy),
the animations were wrong.
Deep dives: `.skill-vendor/skills-emilkowalski/skills/{animate,choreograph,animation-performance}`,
`.skill-vendor/skills-emilkowalski/ui-cheatsheet.md`.
