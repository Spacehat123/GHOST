# LUXURY DESIGN BRIEF & ARCHITECTURAL BLUEPRINT
**Establishment:** Toit Brewpub  
**Location / Market:** Indiranagar, 100ft Rd, Bengaluru, India  
**Assigned Archetype:** Archetype 4 — Modern High-Energy Bistro & Cocktail Lounge  
**Generated At:** Direct Ghost Autonomous Swarm  

---

## 1. Visual & Token Palette (Color Hierarchy Guide Reference)
- **Mode Directive:** **LIGHT MODE BY DEFAULT** with functional Dark Mode toggle (`document.documentElement.classList.toggle('dark')`).
- **Layers (Depth Principle):**
  - Light Canvas: `#f9f9fb` (Never flat white; ensures surface card elevation)
  - Light Surface: `#ffffff` (Cards, panels)
  - Light Raised: `#ffffff` (With soft layered shadows)
  - Dark Canvas: `#0f0f11` / Dark Surface: `#18181b` / Dark Raised: `#2A2A2A`
- **Softened Text Ink (No Vibrating #000):**
  - Primary Ink: `#1D1B20` (Light) / `#E6E1E5` (Dark)
  - Secondary Ink: `#49454F` (Light) / `#CAC4D0` (Dark)
- **Accent Pair (60/30/10 Rule):**
  - Light Accent: `#d97706` (Deepened for >=4.5:1 contrast on light canvas)
  - Dark Accent: `#f59e0b` (Lightened for >=7:1 contrast on dark canvas)
- **Elevation & Shadows:**
  - Resting: `0 1px 2px rgba(0,0,0,.06)` / Hover: `0 8px 24px rgba(0,0,0,.10)`
  - Hairline perimeter line: `rgba(29, 27, 32, .08)` (Light) / `rgba(255, 255, 255, .08)` (Dark)

## 2. Typography Pairings
- **Display / Editorial Headers:** `'Space Grotesk', sans-serif`
- **Body & Micro-Labeling:** `'Inter', sans-serif`
- **Google Fonts Import:** `family=Space+Grotesk:wght@500;600;700&family=Inter:wght@300;400;500;600`

## 3. Curated Photography Assets (Step 2 Research Directive)
- **Asset Rule:** "Collect good photos and use them in the site where possible."
- **Atmospheric Hero Backdrop:** `https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?auto=format&fit=crop&w=1600&q=85`
- **Signature Dish Bento Feature:** `https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?auto=format&fit=crop&w=800&q=80`
- **Artisanal Process & Cellar Metric:** `https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=800&q=80`

## 4. Integrated Component Specifications (`resources.py`)
1. **KokonutUI Liquid Glass:** Frosted backdrop-blur-xl cards with subtle 1px perimeter border and reflective highlight gradient.
2. **Magic UI Shimmer CTA:** Dual high-converting reservation buttons featuring dynamic 360° conical gradient sheen.
3. **React Bits Ambient Gradient:** Ambient background radial blur (`blur-[140px]`) pulsating with organic living depth.
4. **Anime.js Staggered Entry:** Staggered viewport entrance for typography lines (120ms stagger) and menu items (80ms stagger).
5. **Motion Spring Physics:** Weighted hover transitions `cubic-bezier(0.34, 1.56, 0.64, 1)` for cards and interactive pills.
6. **Bklit Data Metric:** Dark-mode Sommelier Grand Cru scoring badge (`98.5 Points • Cellar Reserve`).

## 5. Sectional Architecture Map (Strict 12-Step Website Build Guide Order)
- **Section 1: Nav & Ambient Base Shell** (Sticky glass header with theme toggle, noise texture `opacity-[0.03]`, radial gradient orbs).
- **Section 2: Hero** (Curated photography backdrop, micro status badge `Serving Indiranagar, 100ft Rd, Bengaluru, India • 4.9★`, editorial headline, staggered text reveal, dual CTAs).
- **Section 3: BentoFeatures Grid** (60/30/10 tokens, photography cards, asymmetric layout: 2-column signature dish highlight + sommelier pairing card).
- **Section 4: Social Proof & Reviews** (Infinite horizontal CSS marquee with 3 verified customer quotes).
- **Section 5: How It Works** (3-step structured guest journey in GlassPanel containers).
- **Section 6: FAQ** (Interactive accordion for reservation lead times, dietary notes, private buyouts).
- **Section 7: Final Conversion Footer** (Oversized architectural watermark, direct booking triggers, operating hours, removable concept design note).
