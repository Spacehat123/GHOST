# BESPOKE DESIGN BRIEF & ARCHITECTURAL BLUEPRINT
**Establishment:** Topaz Detailing London  
**Domain:** home_trade_services (Service)  
**Location / Market:** London, UK  
**Assigned Archetype:** Archetype 5 — Clean Service & Modern Precision  
**Classification Confidence:** 0.85 (Needs confirmation: False)

---

## 1. Design System & Semantic Color Tokens
```css
:root {
  /* 60/30/10 Rule Hierarchy */
  --canvas: #f8f9fa;
  --surface: #ffffff;
  --raised: #ffffff;
  --ink: #0f172a;
  --ink-2: #475569;
  --accent: #0f766e;
  --accent-secondary: #0284c7;
  --line: #e2e8f0;
}

:root[data-theme="dark"] {
  --canvas: #0b1120;
  --surface: #1e293b;
  --raised: #2A2A2A;
  --ink: #f8fafc;
  --ink-2: #94a3b8;
  --accent: #14b8a6;
  --accent-secondary: #38bdf8;
  --line: #334155;
}
```

## 2. Typography Hierarchy
- **Headline Display:** `'Plus Jakarta Sans', sans-serif`
- **Body & Technical Specs:** `'Inter', sans-serif`
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
