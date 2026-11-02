# WEBSITE BUILD GUIDE

Follow this whenever you are asked to build a website or demo. Do the steps in order. Do not start a dev server or open localhost at any point. Verify through the build, lint, and type checks only.

## 1. Define before building
Decide these in one pass, using the user's words where given, otherwise sensible assumptions:
- What the site is, who it is for, and the ONE action a visitor should take.
- Brand name, tone of voice, and one accent color.
- Page list and section order.
State your assumptions in one line, then proceed.

## 2. Research (web_search, 2-5 queries)
- Find how strong sites in this niche structure their pages and what proof points they lead with.
- Check current stable versions of the framework and libraries you will use.
- Collect good photos and use them in the site where possible.
- Take patterns and ideas only. Never copy text, layouts, logos, or images from another site.

## 3. Write the content first
Write all real copy before any code: headlines, subheads, body, button labels, FAQ, footer. No lorem ipsum, no placeholders. For real businesses, use only facts you were given or that are publicly listed. Never invent testimonials, reviews, prices, or customer counts. Use clearly marked placeholders like "[Add client quote]" for anything unverified.

## 4. Stack
Next.js (App Router) + TypeScript strict + Tailwind CSS. Motion for component animation. Add GSAP ScrollTrigger and Lenis only if the site needs scroll-driven scenes. Use shadcn/ui for accessible primitives. Pin versions from step 2.

## 5. Design system (create tokens first, use only tokens after)
- Color 60/30/10: neutral canvas, secondary surface, one accent for primary CTA and key highlights only. Define semantic tokens (bg, surface, border, text-primary/secondary/tertiary, accent) and use them everywhere. Provide light and dark themes with no flash on load.
- Type: one variable font via next/font, fluid clamp() scale, tight tracking on display text, line-height about 1.05 for display and 1.5-1.6 for body, max two weights per screen.
- Layout: 8pt spacing grid, 1200px max width, section padding clamp(80px, 12vw, 160px). Whitespace is the premium signal.
- Hierarchy: each screen has one focal point and one primary action. Reading order is headline, then proof, then action. Create hierarchy with size, weight, and contrast before color.
- Radius and shadow: a consistent radius scale and layered soft shadows.

## 6. Glass effects
- Build one reusable GlassPanel: backdrop blur 20-40px with saturate, translucent gradient fill, 1px gradient border, inner highlight, soft shadow.
- Use glass only on the nav, floating cards, and modals over a gradient or media background. Never on text-heavy blocks.
- Provide a plain blur fallback via @supports. Optional: a cursor-following highlight, disabled on touch.

## 7. Motion
- Hero headline reveals by line with a stagger. Section reveals are fade plus 24px rise, once, at about 20% in view.
- Add one or two pinned scroll scenes only if they serve the story.
- Animate transform and opacity only.
- Respect prefers-reduced-motion by switching to simple fades.

## 8. Pages and sections
Default home page: Nav, Hero, Social proof, Features (bento grid), How it works (3 steps), Testimonials (only real or clearly marked), Pricing (if relevant), FAQ, Final CTA, Footer. Add /about, /contact, /privacy, /terms, and a custom 404 as the scope needs.

## 9. Required functionality
- Working nav, anchors, and every link and button.
- Forms with Zod validation, inline errors, and loading/success/error states. Keep secrets in .env, never in client code, and include .env.example.
- SEO: per-page metadata, Open Graph image, sitemap, robots, JSON-LD, semantic HTML.
- Accessibility: WCAG AA contrast, keyboard navigation, visible focus, skip link, alt text, 44px touch targets.
- Performance: next/image, lazy loading below the fold, font preloading, dynamic imports for heavy animation code.
- Responsive from 360px to 1920px, mobile-first.
- Favicon set and manifest.

## 10. Build order
Tokens, then layout/nav/footer, then base components and GlassPanel, then hero, then scroll scenes, then remaining sections, then extra pages, then forms, then SEO/accessibility/performance.

## 11. Verification (no localhost)
- Run the type check, lint, and `npm run build`. Fix every error and warning.
- Read the build output for oversized bundles or failed static pages.
- Review the code for broken links, missing alt text, unlabeled inputs, hardcoded colors outside tokens, and layout that could break at 360px.
- Do not report done until the build passes cleanly.

## 12. Delivery
Report in 3-5 lines: what was built, the file structure, the commands to run it later (`npm install`, `npm run build`), the assumptions made, and one suggested next step. Include a DEPLOY.md with the steps to deploy on Vercel, the env vars to set, and a pre-launch checklist.

## Rules
- Pitch demos for a real business get a small removable "Concept design" footer note.
- Never imitate another organization's login, payment, or official portal pages.
- Never put keys or secrets in client code.
- Ask before any irreversible action such as deploying to a live domain or sending email.
- If the same error persists after two fixes, stop, diagnose from the output, and change approach instead of looping.
