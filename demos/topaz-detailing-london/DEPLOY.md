# DEPLOYMENT GUIDE: Topaz Detailing at The Peninsula London

This document details deployment procedures, environment configuration, and pre-launch verification per Commander Pranav's Master Website Build Guide.

## 1. Hosting Architecture
- Target Platform: Vercel (Edge Network)
- Source Type: Static Single-Page Concept with Dynamic Headless Enhancements
- Primary Action: Direct Table / Salon Booking Concierge

## 2. Vercel Deployment Instructions
1. Install or authenticate Vercel CLI:
   ```bash
   npm i -g vercel
   vercel login
   ```
2. Navigate to project root or demo directory:
   ```bash
   cd /home/pranc/projects/ghost/demos/topaz-detailing-london
   ```
3. Deploy to preview:
   ```bash
   vercel
   ```
4. Deploy to production domain:
   ```bash
   vercel --prod
   ```

## 3. Environment Variables (.env.example)
```env
NEXT_PUBLIC_SITE_URL=https://topaz-detailing-london.vercel.app
NEXT_PUBLIC_CONCIERGE_PHONE="+44 20 8961 9951"
NEXT_PUBLIC_CONCIERGE_EMAIL="concierge@topaz-detailing-london.com"
```

## 4. Pre-Launch Verification Checklist
- [x] Strict 60/30/10 design system applied with semantic tokens.
- [x] Light Mode default verified with functional Dark Mode toggle and no flash on load.
- [x] Responsive layout verified from 360px mobile to 1920px desktop.
- [x] Zero layout overflow / 0 horizontal scroll.
- [x] Reusable GlassPanel with backdrop blur fallback (@supports).
- [x] Sectional flow: Nav -> Hero -> Bento Grid -> Proof Marquee -> How It Works -> FAQ -> Conversion Footer.
- [x] Removable "Concept design" footer attribution included.
- [x] Zero dev server dependency (verified headlessly).
