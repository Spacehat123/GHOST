<!-- capability
name: premium_motion_site
summary: High-polish non-3D flagship: restrained scroll storytelling, bespoke typography, one high-craft signature interaction — top 3-5 visual-sector leads.
triggers: motion, flagship, polish, scroll, signature, motion site, flagship experience
applies_to: build_demo, build_website
-->

# Premium Motion Flagship Website Skill

Builds high-polish, non-3D flagship websites with restrained scroll storytelling, bespoke typography, and exactly one high-craft signature interaction.

## When to Use
- When `choose_flow` routes a high-scoring visual lead to `motion`.
- When an operator requests a flagship experience for a client without a 3D model.
- Top 3-5 leads per batch in visual/experience-led sectors (detailing, dental, beauty, gym, hospitality, dining, photography).

## Core Philosophy
1. **One Signature Interaction:** Never clutter with ten competing effects. Pick one signature interaction tailored to the client's craft (e.g. before/after reveal slider for detailing, interactive class timetable for fitness, floating lookbook for fashion).
2. **Honesty First:** Never fabricate before/after results, customer reviews, or work samples. If real consented photography is not provided in the ledger, present clearly styled concept placeholders (`"Your before photo here"`).
3. **Accessibility & Restraint:** Full `prefers-reduced-motion` compliance. Zero scroll hijacking beyond short pinned sequences. The page must remain 100% operable via keyboard and touch at 390x844.

## Signature Interaction Catalog
- **Automotive Detailing:** Split-curtain before/after paint correction comparison slider with dual magnification.
- **Dental & Aesthetics:** Trust-first care journey with sequential stage reveals.
- **Gyms & Athletic Clubs:** Live dynamic class schedule with timetable filters and next-session countdown.
- **Fine Dining & Cafés:** Ambient editorial menu with cursor-following dish previews.
- **Boutique & Tailoring:** Horizontal lookbook strip with texture zoom.
- **Luxury Hospitality:** Parallax suite narrative and architectural tour.

## Build Tasks (10 Tasks - Part 8 Mission Compatible)
1. **Direction & Signature Selection:** Extract brand cues, domain type, and select signature interaction. Write creative brief for operator authorization.
2. **Asset Ledger & Image Optimization:** Audit all photos, verify licenses/permissions in `SOURCES.json`, convert images to WebP/AVIF within 200KB hero budget.
3. **Scaffold & Build Setup:** Initialize template with tokens, typography, and clean bundle output.
4. **Design Tokens & Responsive Layout:** Configure serif/sans typographic scale, muted luxury palettes, and 390px/1440px fluid grid.
5. **Hero & Scroll Narrative:** Implement editorial opening headline, value proposition, and narrative pacing.
6. **Signature Interaction Assembly:** Code the primary interactive module with full pointer, touch, and ARIA keyboard support.
7. **Supporting Motion Pass:** Add restrained typography fade/staggers, soft border highlights, and optional smooth scroll.
8. **Direct Action & Booking Module:** Wire real telephone and direct WhatsApp actions from the CRM ledger with no invented contacts.
9. **Accessibility & Reduced Motion:** Implement `@media (prefers-reduced-motion: reduce)` overrides to disable pinned transforms and smooth scrolling.
10. **QA Audit & Evidence Verification:** Verify zero console errors, audit `SOURCES.json`, verify viewport screenshots, and compile `reports/perf.json`.
