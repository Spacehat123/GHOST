# Skill: Luxury Demo Builder & Editorial Web Design
## SOP & Quality Protocols for Ghost Agency Website Builds

### Mandatory Demo Protocols & Hard Rules:
1. **Rule 1 (Zero-Food Bias)**: Never assign a food/drink template unless Google category or verified signals explicitly indicate food/drink. Map prospect domain accurately across the 14 catalog industries via classify_business.
2. **Rule 2 (Fail Closed)**: If the business category is unknown or ambiguous, fail closed to 'general_services' neutral template and request confirmation. Never guess or default to fine_dining.
3. **Rule 3 (Strict Source Ledger)**: Every fact on the page (services, prices, hours, phone, address, rating, reviews) MUST originate from scraped data and be recorded in SOURCES.json via SourceLedger. Missing facts remain null and render marked placeholders ('[Inquire for quotation]') or are omitted entirely. Never invent plausible defaults or placeholder menus.
4. **Rule 4 (Zero Fabricated Metrics)**: No artificial benchmark scores ('99/100', '0.38s'), speed claims, or unverified 'Verified' stamps are ever placed on client demos.
5. **Rule 5 (Persona Firewall on Outbound Pitch)**: All client-facing outbound copy (email, DM, WhatsApp, PITCH_KIT) is authored and signed personally as Pranav. Zero internal persona words (Ghost, Task Force 141, Chief of Staff, Deep-Strike, Commander, sir/mate/boss), zero local file:// links, and zero local laptop paths.
6. **Rule 6 (Hosted Delivery Prerequisite)**: No outreach pitch is produced without a real hosted https URL deployed via Vercel ({slug}-concept). If unhosted, halt at status 'needs_deploy'.

### 8 to 12 Contextual Editorial Sections:
1. **Contextual Navigation**: Dynamically select navigation style (Minimalist Solid, Floating Pill, Classic Heritage, Liquid Glass).
2. **Hero Section**: Real high-res photography backdrop, gap callout badge, staggered reveals, dual CTAs.
3. **Operational Gap & Value Prop Banner**: Highlights the resolved friction (e.g. 'Instant 1-Tap Table Concierge').
4. **Story & Craft Philosophy**: Brand ethos and culinary/hospitality narrative extracted from real bio/listing.
5. **Bento Grid Offerings**: Signature highlight dish/service, secondary highlights, exact pricing, filterable categories.
6. **Quality Proof & Accreditations**: ONLY rendered if verified metrics or awards exist; if unverified, OMIT entirely.
7. **Ambiance & Space Gallery**: Multi-photo mosaic of real interior, dining room, and atmosphere.
8. **Verified Social Proof**: ONLY rendered if real Google Reviews exist: infinite marquee with actual star ratings and real quotes.
9. **Performance & Conversion Delta Card**: Side-by-side audit benchmark (their 8s page vs our sub-second demo).
10. **Curated Journey / How It Works**: 3 clear steps from reservation to table.
11. **Interactive VIP WhatsApp Booking Card**: Party size & date quick-selector -> prefilled WhatsApp chat.
12. **Location, Hours, Contact & Conversion Footer**: Interactive hours/address card, oversized brand typography, 'Concept design presented by Ghost' credit.

### Restrained Luxury Design & Color Hierarchy:
- **Depth Layers**: Light Mode uses Canvas #F2F2F7, Surface #FFFFFF, Raised #FFFFFF. Dark Mode uses Canvas #121212, Surface #1E1E1E, Raised #2A2A2A.
- **Elevation & Shadows**: In light mode, let soft layered shadows do the lifting (resting 0 1px 2px rgba(0,0,0,.06), hover 0 8px 24px rgba(0,0,0,.10)). In dark mode, step up surface lightness; add 1px border at 8% white if edges blur.
- **Text / Ink**: Soften black; pure #000 on white vibrates. Use Primary Ink #1D1B20 and Secondary Ink #49454F. Dark mode uses Primary #E6E1E5, Secondary #CAC4D0.
- **The Accent (10% Rule)**: Exactly ONE accent pair. 60% canvas, 30% surface/text, 10% accent.
- **Semantic Tokens Only**: All elements must reference CSS custom properties (--canvas, --surface, --raised, --ink, --ink-2, --accent, --line, --shadow-resting, --shadow-hover).

### Mobile-First Verification & Pitch Kit:
- Headless Brave CDP smoke test at 390x844 mobile first, then 1440x900 desktop (0 layout overflow, 0 console errors).
- Complete Pitch Kit containing: hosted preview link, verified screenshots, 3-part outreach copy.
