# Build brief: a local-business website that survives the real world

You are building ONE website for ONE real local business. It must look specific to that business (not generic), load fast on a cheap Android phone, and keep working for months with almost no maintenance.

## 1. Architecture rules (non-negotiable)
- **Static site. No custom backend, no database, no login, no server code.** Every moving part is an off-the-shelf service.
- Host: Vercel, Cloudflare Pages or Netlify (free tier). Source in a git repo (this is the backup).
- Contact paths, in this order of importance:
  1. `tel:` call button
  2. WhatsApp link: `https://wa.me/<number>?text=<prefilled message>`
  3. Enquiry form through a form service (Formspree / Web3Forms), delivering to the owner's email
  4. Booking: an external booking link (Google appointment page, Cal.com) if the business takes bookings
- Fonts self-hosted. No dependency on a CDN for anything the page needs to render.
- Content lives in ONE easy-to-edit file (e.g. `content.json`) so hours, prices and phone numbers can be changed in one place.

## 2. Content rules: never invent anything
Before writing any copy, collect and confirm with the owner (or from their own public pages):
- exact business name spelling, address, phone, WhatsApp number, hours
- services and real prices (or "from ₹X" only if confirmed)
- their own photos (ask for them; do not scrape or hotlink)
- Google review link, Instagram and other social links

Do NOT make up: prices, awards, "since 19xx", years of experience, testimonials, reviews, team names, certifications. If a fact is missing, leave it out or mark it `TODO` and tell me. Every fact on the page must be traceable to a source I can name.

## 3. Anti-"AI slop" design rules
- Write specific copy about THIS business: what they do, where, for whom. No filler like "elevate your experience", "seamless", "world-class".
- One accent colour, two fonts at most, a clear type hierarchy, generous spacing.
- No generic gradient blobs, no stock-photo heroes, no lorem ipsum, no emoji as icons.
- One page, short, ordered by what a customer needs: what you do → proof (real photos) → prices/menu → hours + map → call / WhatsApp.
- The primary action (call or WhatsApp) is visible without scrolling on a phone.
- No heavy 3D, video backgrounds or animation libraries unless the business truly needs them.

## 4. Technical acceptance criteria (each needs evidence)
- Lighthouse mobile: Performance ≥ 90, Accessibility ≥ 90, SEO ≥ 95, Best Practices ≥ 90.
- Page weight under 1 MB. Images: WebP, ≤ 200 KB each, `width`/`height` set, below-the-fold lazy-loaded.
- Looks right at 360, 390, 768 and 1440 px wide. Tap targets ≥ 44 px. No horizontal scroll.
- Works on slow 3G (throttled test). No console errors. No broken links (run a link checker).
- Title, meta description, favicon, canonical URL, `robots.txt`, `sitemap.xml`.
- **Open Graph title, description and image**, so the link previews properly when shared on WhatsApp (this is how most owners will share it).
- `LocalBusiness` JSON-LD with name, address, phone, hours (must match the Google Business Profile exactly).
- Custom 404 page. HTTPS only. Apex and `www` redirect to one canonical address.
- Form: honeypot spam protection, clear success and error states, a note on how the data is used. **Submit a real test form and confirm it arrives in the owner's inbox, including checking the spam folder.**

## 5. Real-world failures to design against
- **Domain:** register it in the CLIENT's name; set auto-renew; record the registrar login in a password manager; check apex vs `www` DNS; if the owner has business email, don't break its MX records.
- **Form fails silently:** form services have free-tier limits and emails can land in spam. Test monthly; provide WhatsApp as a backup path on every page.
- **Wrong details:** a wrong phone number or hours is the most common real failure. Read them back to the owner before launch. Match them to the Google listing.
- **Client wants changes:** decide up front how requests arrive (a single WhatsApp number) and how fast they're done.
- **Heavy images:** phone-camera photos are 4–8 MB. Compress everything.
- **Site not found:** verify in Google Search Console, submit the sitemap, and make sure the Google Business Profile links to the site.
- **Free-tier limits and lock-in:** note them, and keep the repo so the site can move host in an hour.
- **Credentials:** who owns what login is written down; nothing stays only on one person's laptop.
- **Uptime:** set a free uptime monitor (e.g. UptimeRobot) that alerts the developer.
- **Privacy:** if the site collects names or numbers, add a short privacy note. Avoid analytics that need cookie banners (use a privacy-friendly one).

## 6. How you must work
1. **Plan first, no code.** Give me the page structure, the content inventory with every `TODO`, and a **pre-mortem**: list 10 ways this site could fail for THIS business and how each is handled. Wait for my confirmation.
2. Build the page.
3. Run every check in §4 and show the **evidence**: Lighthouse JSON/screenshot, screenshots at the 4 widths, link-checker output, the test form submission.
4. Report honestly: what passed, what failed, what is still `TODO`. Never say "done" without evidence.
5. Handover file: where the site lives, domain and DNS details, how to edit `content.json`, how to redeploy, who holds which login.

## 7. My own 5-minute check (human, on my real phone)
- Open the live link on my phone over mobile data.
- Tap call and tap WhatsApp (they must work and use the right number).
- Share the link in a WhatsApp chat to myself and look at the preview.
- Submit the form and watch it arrive.
- Read every phone number, address, price and hour out loud against the owner's own listing.
