<!-- capability
name: local_seo_dominance
summary: The 6 local moves: address/pin, categories = what customers type, review velocity (4.7-4.9, 1/3 to Yelp), hours = answered, local links, ANSWER THE PHONE + real photos.
triggers: seo, local, google, ranking, rank, gbp, business profile, reviews, maps, website, client
applies_to: build_website, onboard_client, seo_audit
-->

# SKILL: Local SEO Dominance — the 6 moves (operator's guide, distilled)

Source: "The Full List: 6 Local SEO Moves (1,000 Hours, Distilled)" by Hunter Lapeyre
(operator-provided, `Website_Guides/SEO_Guide/`). Most Ghost clients are LOCAL
businesses — they rank in a circle around their verified address, not globally.
Every client website Ghost builds ships with this operationalized, and every client
onboarding carries the checklist.

## The physics
Proximity to the VERIFIED address explains ~60–65% of map rank. Listing 40 cities
stretches nothing. A hidden (service-area) address costs 30–50% more effort.

## Move 1 — Address & pin
- Pin must sit exactly on the building (Google Maps check; fix via Google support).
- No second profile at the same address with the same category.
- Winning a city 20 miles away needs a real staffed location there. Virtual
  offices/UPS boxes get suspended.
- NEVER change the address on a profile that already ranks without a verification
  video ready (street, number, unlocking the door; ~90s, no faces).

## Move 2 — Categories = what customers TYPE
- Primary category is the strongest controllable ranking factor. "AC repair", not
  "HVAC contractor". Verify: search "[category] near me" — if the top 3 aren't
  businesses like yours, it's the wrong category. Check the top 3's actual
  categories (GMB Everywhere extension). Confirm the category exists in the picker —
  AI tools invent categories.
- Every genuine secondary category (up to 9), each with 2–3 described services.
  Generic searchable service names ("roof leak repair", never "New Orleans roof
  leak repair").
- BANNED: categories for work they don't do (suspension risk); changing a primary
  that already ranks.

## Move 3 — Reviews: velocity beats count
- First 10 build trust; after that, NEW REVIEWS PER MONTH vs competitors decides.
  Sweet spot: **4.7–4.9 stars** (perfect 5.0s rank LOWER). Total in the same
  ballpark as the top 3.
- The ask: text from the OWNER'S phone (saved contact) the day the job wraps —
  40–60% response vs 5–20% for software. Steady trickle, never spikes. Friday
  10-minute ritual.
- Reply to EVERY review in your own words (people sort by lowest first — the reply
  is for the NEXT customer; never reveal what they didn't post).
- Send ~⅓ of asks to Yelp/Facebook/BBB — AI answer engines read Yelp heavily.
- BANNED: incentives, "leave us 5 stars", keyword-primed asks, bought/traded
  reviews (FTC fines; Google wipes real ones with fakes).

## Move 4 — Hours = when someone answers
"Open" means a human picks up. Google hides closed businesses when an open one
exists. If an on-call tech/answering service/AI receptionist can answer, list those
hours. Only 24h if genuinely 24h. Set once, correctly — hours churn is a
re-verification trigger.

## Move 5 — Local links: chamber > DR score
First 10 real local links are worth more than the next 100. Best first: chamber of
commerce directory (~$100–200/yr) → manufacturer "find a contractor" pages (free if
certified) → trade association/licensing directories → youth sports/school
sponsorships (link on sponsor page) → local events/charity → BBB + Yelp (free,
trust + AI engines read them). Judge a link by "could a real local customer land on
this page?" BANNED: link packages, PBNs, random guest posts.

## Move 6 — THE BIG ONE: answer the phone
Goal completion is visible to Google (click/engagement data). Missed calls show up
as ranking drops within days; ~70% of callers won't leave voicemail. Do: call your
own number at 7PM Saturday and count rings; rollover to a live answerer at 4–5
rings; text back every missed call within minutes ("Sorry we missed you, this is
[name] from [company]. What's going on?"); profile number = local number that rings
a person and matches the website; real call tracking (the profile's call counts lie).
**Real photos of crew and jobs (20–30 to start, then 3–4/week) directly affect
calls. AI-generated images: banned.**

## What Ghost BUILDS from this (see workers/seo_pack.py)
1. Website JSON-LD `LocalBusiness` (+ type) with NAP EXACTLY matching the GBP —
   name, address, phone, hours (Move 1/4 consistency).
2. Services rendered as crawlable content matching the chosen categories' wording
   (Move 2), one section per service.
3. FAQPage JSON-LD answering what people actually ask (feeds AEO — see
   aeo_answer_engines skill).
4. Review velocity playbook: template ask text, Friday ritual card, ⅓-to-Yelp rule,
   reply-templates for 1★ and 5★ (Move 3).
5. Hours block answering "can someone pick up?" honestly (Move 4).
6. Local-links opportunity list (chamber, associations, sponsorships) with live-URL
   research prompts (Move 5).
7. Missed-call rollover + text-back setup in onboarding (Move 6).
8. Photo policy: real photos only; the site ships with slots for the client's own
   20–30 photos (see the client-photos gate in the website flow).

## Measurement
Rank-grid scan for the primary category before changes, then every 2–3 weeks.
Change ONE thing at a time; give it a week before judging.
