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
