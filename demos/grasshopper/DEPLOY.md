# DEPLOYMENT GUIDE: Grasshopper

This document details deployment procedures for the static single-page concept for Grasshopper.

## 1. Architecture
- Source Type: Clean static HTML/Tailwind
- External Dependencies: None (Zero build step required)
- Edge CDN Target: Vercel / Cloudflare Pages / Netlify

## 2. Deployment Steps
### Deploy via Vercel CLI:
```bash
npm i -g vercel
cd demos/grasshopper
vercel --prod
```

### Deploy via Netlify Drop:
Drag and drop the `demos/grasshopper` folder directly into Netlify Drop.
