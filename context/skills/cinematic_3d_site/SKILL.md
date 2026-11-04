# SKILL: Cinematic 3D Site (Three.js scroll-driven hero)

Install at `context/skills/cinematic_3d_site/SKILL.md`. Ghost loads it with the `get_skill("cinematic_3d_site")` tool ONLY after the gate in `07_3D_SITE_PLAN.md` passes. Do not paste it into the base system prompt.

## What this skill produces
A single-page, scroll-driven 3D experience: one hero 3D subject (building, vehicle, product, interior) rendered live with Three.js, five scripted camera stages, cursor parallax, a hover "scan/wireframe" effect, an editorial UI, optional sound (off by default), and two follow-on content sections plus footer. It is a premium upsell for clients who justify it, never the default.

## Inputs Ghost must have BEFORE building (else the task is `blocked`, not guessed)
| Input | Source | If missing |
|---|---|---|
| `BUSINESS_NAME`, `SUBJECT` (what the 3D object is) | lead record / operator | ask the operator |
| GLB file (+ license row) | project folder, or free library (see 09) | block; never generate a fake model |
| Three services/offerings | source ledger | omit the section or mark placeholders |
| Contact details | source ledger | show nothing; no invented contacts or credentials |
| Mood/palette/season | operator, or derived from the brand and subject | propose one, ask for approval |
| Copy (headlines, descriptions) | written by Ghost from ledger facts only | no claims, no invented clients, no awards |

## Build tasks (this exact order; one at a time; each needs evidence)
1. **Inspect project folder + GLB.** Evidence: file list, GLB size, license ledger row.
2. **Analyse model.** Run `glb_inspect` (gltf-transform inspect): triangle count, materials, textures (sizes), bounding box, scale, animations, draco/meshopt use. Evidence: JSON report saved to `demos/<slug>/reports/glb.json`. Decide: optimise needed? (target: <= 300k triangles, <= 2048px textures, <= 8 MB over the wire).
3. **Set up tooling.** Vite + three (pinned versions), `GLTFLoader` + `DRACOLoader`/`MeshoptDecoder` as needed, postprocessing (bloom only if it holds 50+ fps on target), local fonts. Evidence: `npm run build` exit 0.
4. **Build the scene.** Environment fitting the subject and palette (e.g. water + reflections for a waterfront building, polished floor for a car, studio sweep for a product), atmospheric depth, lighting, subtle particles (snow/dust/embers), restrained emissive accents, soft bloom. Preserve the model's own materials. Avoid washed-out haze, heavy blur, visible cloud sprites, excessive darkness. Evidence: screenshot of the static scene.
5. **Build camera stages and scroll/touch control.** Five complete stages. One deliberate scroll gesture = one 1.2-1.6 s eased move, then wait (lock input during the move, ignore trackpad momentum, accept again after a short cooldown); reverse scrolling reverses; touch swipe and keyboard (arrows/PageUp/PageDown/space) do the same. Vary elevation, distance and focus; keep >= half the subject visible; in stage 5 the subject sits in the right ~60% of a desktop viewport, text on the left. Evidence: screenshot per stage (desktop 1440x900 and mobile 390x844) + a log proving one move per gesture.
6. **UI and copy.** Editorial typography (expressive display face, selective italic/colored words), fine rules, generous spacing, wordmark, nav hover states, a transparent "Views" panel (all five stages), a compact "Explore the structure/object" info area with a slowly looping label strip, custom inspection cursor, polished loading state. Each stage gets a short headline + description that reveal within 0.25-0.4 s alongside the camera. On headline hover only: a 1.2-1.6 s luminous scan across the letterforms (keep text readable, no restart flicker between letters). Evidence: screenshots of each stage with UI.
7. **Hover wireframe scan effect.** On subject hover, a localized wireframe revealed through a feathered irregular mask with a subtle cool-accent glow; a few animated guides/brackets/anchor points aligned to hovered features; fade out on pointer exit. Evidence: short capture or sequence of screenshots.
8. **Extension sections** (after stage 5, natural scroll continuation, reversible, stage interaction restored when scrolling back up):
   - *Structure to Atmosphere:* split composition; as the user scrolls the subject is revealed top to bottom (outline -> materials -> lighting -> finished render) with an aligned scan boundary and layered masks; the outline and finished layers must share identical framing. Three service columns from the ledger.
   - *Inside the Design:* an ORIGINAL plan/axonometric/diagram assembled from individually animated SVG paths (outer boundary -> structure -> partitions -> openings -> details -> annotations), internally coherent, labelled as a conceptual study; short process notes (Concept, Material, Light, Experience) aligned to the drawing layers; interactive markers revealing short notes on hover and keyboard focus. Optional brief pinned sequence that releases naturally.
   For non-architecture subjects replace the drawing with an exploded view, spec diagram, or material breakdown, built the same way.
9. **Footer and integration.** Contact invitation, wordmark, working section navigation, available contact details only, copyright, sound control, back-to-top.
10. **Sound (optional, off by default).** Visible global sound toggle; audio starts only after the visitor enables it. Ambient bed, camera whoosh once per move, tonal inspection cue. No overlapping hover sounds; never fire during continuous scroll. Use procedural Web Audio or licensed CC0 samples (see 09). If assets can't be produced, ship the controls and report exactly which assets are missing.
11. **Inspect in browser and fix.** Open every stage on desktop and mobile; fix framing, loading, cursor, and interaction issues. Evidence: final screenshot set + checks below.

## Hard requirements
- **Performance:** adaptive quality tiers (pixel ratio, shadow/bloom off on weak GPUs), pause rendering when the tab is hidden, dispose geometries/textures, assets lazy-loaded, 55+ fps on a mid laptop at desktop, 30+ on a mid phone, first meaningful paint under 3 s on a throttled connection (report real measurements, never guesses).
- **Accessibility:** `prefers-reduced-motion` shows static stage changes (no long camera moves, no scan effect), full keyboard control, visible focus, text never on top of bright areas without contrast, audio never auto-plays, a "skip to content" path.
- **Fallback:** if WebGL fails or the device is very weak, show a high-quality still poster of the scene (generated from the Three.js scene via screenshot) with the same copy and contact info.
- **Honesty:** no invented contact info, clients, credentials, awards or statistics. Everything claimed on the page is in the source ledger. The page carries the standard "concept design" note until the client signs.
- **Licensing:** every asset (GLB, textures, audio, fonts, images) has a row in `SOURCES.json` with source URL, license and attribution requirement; attribution rendered in the footer when required. NC/ND licenses are not allowed.
- **Limits:** at most ONE generated video, <= 5 s, seamless loop, and only if it clearly beats real-time animation. Default: none.
- **Scope:** do not rebuild finished stages when extending; preserve existing hero and its five stages.

## Acceptance checklist (the final audit task runs this; each line needs evidence)
- [ ] Build passes; no console errors on load or during all five stages.
- [ ] One gesture = one stage move; trackpad momentum doesn't skip stages; reverse works.
- [ ] Subject >= 50% visible in every stage; stage 5 composition in the right 60%.
- [ ] Hover wireframe + headline scan work and clean up.
- [ ] Reduced-motion and keyboard paths work; mobile 390x844 works.
- [ ] Sound off by default; toggle works; no overlapping sounds.
- [ ] Both extension sections reveal and reverse; footer links work.
- [ ] Measured fps and load time recorded in `reports/perf.json`.
- [ ] `SOURCES.json` complete; no banned terms; claims lint passes.
