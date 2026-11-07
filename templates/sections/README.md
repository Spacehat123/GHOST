# The Section Kit — how and where to put components

**Where:** `templates/sections/<name>/` — one directory per component. That's
the whole answer to "where do I put a new component". The composer discovers
every directory containing a `SECTION.md`; there is no registry to update and
no code to change.

**What they are:** pre-validated page sections (hero, gallery, booking, FAQ…)
that Ghost's demo/website builders COMPOSE into full pages. Sections are
chosen and filled by deterministic code; nobody — human or model — hand-writes
page structure anymore. This is the Webild lesson (compose, don't generate)
with Ghost's teeth: every component passes validation gates before it may
render, and every rendered page carries its component map in `SECTIONS.json`.

---

## Anatomy of a component

```
templates/sections/my_section/
  SECTION.md      # required — manifest with the <!-- section --> header
  section.html    # required — Jinja2 markup
  section.css     # optional — page styles
  section.js      # optional — almost never needed (behavior lives in _base)
```

### The manifest (SECTION.md)

```markdown
<!-- section
name: my_section            # MUST equal the directory name
category: hero              # hero | navigation | gallery | booking | faq | nap | ...
summary: One line — what it renders and from which slots.
slots: main_title, tagline  # PageContext keys this template references
optional_slots: hero_bg     # keys that may be absent (the template must handle it)
applies_to: all             # or a domain list: restaurants_cafes, dental_medical, ...
requires: always            # or a condition name the planner checks
-->
# my_section
Notes for the next author.
```

The header format is the same `<!-- capability -->` style used by
`context/skills/*/SKILL.md` — one format to learn.

### The template (section.html)

Jinja2, **autoescaped** — anything coming from the dossier (names, taglines,
quotes) cannot inject HTML. Reference values directly: `{{ main_title }}`,
`{{ copy.cta_primary }}`, `{{ arch.font_head }}`. For dynamic slot access use
the `context` dict: `{% if context.get('gallery_2') %}`.

Two kinds of values:

| Source | Trust | Escaping |
|---|---|---|
| Dossier strings (names, reviews, taglines) | untrusted | autoescaped — never mark `\| safe` |
| Archetype tokens (`arch.*`), copy (`copy.*`) | Ghost-owned | escaped too (harmless) — raw CSS for `arch` is rendered by the composer, not sections |

### The house rules (validation refuses, never warns)

1. **No external anything.** `url(`, `@import` in CSS; `fetch(`,
   `XMLHttpRequest`, `eval(`, `document.write`, `<script src="http…">`,
   inline `on*=` handlers — all banned. Sections make zero network requests;
   pages are CSP-safe and work offline.
2. **Behavior lives in `_base/reveal.js`.** Booking link updates, the lead
   form POST, theme toggle, entrance motion, the svg.js divider — all in the
   base layer. A section ships markup + styles; add `section.js` only for
   genuinely section-local presentation (and it passes the same bans).
3. **Photo doctrine.** A photo slot may be EMPTY (unfilled slots stay
   unfilled). Render `[Inquire for quotation: photography]` — never a stock
   image, never a placeholder cat.
4. **Missing optional slots must degrade.** The template renders complete and
   beautiful with every optional slot absent — with JS disabled too.
5. **Contracts are sacred.** `booking-service`, `booking-detail`,
   `booking-date`, `whatsapp-direct-btn`, `booking-btn-text` (the booking
   ids), `.reveal-el` / `.spring-card` (motion targets), and the archetype
   CSS-variable tokens. Renaming any of these breaks QA and the quality gates.
6. **One decor element per page, site-wide.** The hand-drawn divider is the
   single allowed svg.js decoration — the composer places it. Sections don't
   add their own.

### The PageContext (what a template may reference)

`slug, business_name, main_title, sub_title, tagline, location, city, rating,
rating_badge, phone, clean_phone_digits, has_phone, is_uk_landline,
initial_booking_href, initial_btn_text, currency_symbol, services,
has_services, is_sample_services, show_sample, reviews, hours, faqs, steps,
booking_fields, photos_policy, hero_bg, bento_img1, bento_img2, gallery_1,
gallery_2, gallery_3, copy, arch, present_ids, form_path, philosophy_body_1`

Anything a template references outside this set (or its own declared slots)
fails validation with the exact variable named.

---

## How sections get chosen (the planner)

`section_kit.plan_sections(ctx)` is deterministic — every include/omit carries
a written reason, the same culture as need/pay scoring:

- `services_bento` → verified services, or the labelled sample layout, or
  **omitted** (never invented)
- `proof_reviews` → only when scraped reviews exist
- `gallery_strip` → only when ≥1 real photo is staged
- `faq_answers` → when `copy.faqs` exist (answer-first, AEO)
- `lead_capture` → always — every Ghost site captures leads
- `hours_location` → NAP block, byte-consistent with LocalBusiness schema

Order is fixed: nav → hero → banner → story → services → proof → gallery →
journey → booking → faq → hours → lead capture → footer (the svg.js divider
sits between journey and booking).

## Worked example: adding a component

```bash
mkdir templates/sections/testimonials_wall
```

`SECTION.md`:
```markdown
<!-- section
name: testimonials_wall
category: social_proof
summary: Masonry wall of real scraped reviews with star ratings.
slots: reviews
optional_slots: reviews
applies_to: all
requires: reviews
-->
# testimonials_wall
Only real reviews. Marquee variant for 3+, grid otherwise.
```

`section.html`: Jinja over `{% for rev in reviews %}` — autoescaped, glass
cards, `var(--…)` tokens, no scripts. Drop the directory. Next build picks it
up; `test_section_kit.py` validates the whole kit on every suite run, so a
broken component fails CI, not a client demo.

## Where the composer lives

`src/ghost/workers/section_kit.py` — `SectionLibrary` (discover/validate),
`plan_sections` (selection + reasons), `compose_page` (assembly, pure), `write_section_map` (`SECTIONS.json` —
per-demo component map; the address space the future edit lane will patch).
