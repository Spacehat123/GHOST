# SKILL: AEO — Answer Engine Optimization (rank on ChatGPT, Perplexity, Gemini, AI Overviews)

The operator's directive: "the website itself isn't the point — SEO optimization
(answer what ppl ask), ranking on ChatGPT, other AIs." For local businesses the
AI answer layer is where decisions happen now. This is how Ghost-built sites
become the SOURCE the engines cite.

## The mechanics (what we actually control)
AI answer engines synthesize from: (1) crawlable, semantically clean HTML;
(2) structured data they can trust; (3) entity consistency across the web
(NAP/schema everywhere matching); (4) third-party corroboration (Yelp, BBB,
chamber directories — AI reads Yelp heavily per the local-SEO guide);
(5) question-shaped content that matches real queries.

## Build rules (every Ghost client site ships with these)
1. **Answer-first blocks**: each service/section opens with a 40–60 word direct
   answer to the question a customer would type ("Do you take walk-ins?",
   "How much does a 7-course tasting cost?"). The answer is a complete sentence
   containing the business name + city. Detail after, never before.
2. **FAQPage JSON-LD** for every genuinely-asked question (pricing, booking,
   parking, hours, dietary, warranty). Questions come from real sources: the
   business's review replies, "People also ask", the operator's phone log. Marked
   up with the same text visible on the page (engines penalize schema/content
   mismatch).
3. **LocalBusiness JSON-LD** (correct subtype: Restaurant/Dentist/…): NAP, hours,
   geo, priceRange, sameAs → GBP listing, Yelp, Instagram, chamber listing. The
   `sameAs` graph is how engines connect the entity.
4. **llms.txt** at the root: a short markdown index of what the business is,
   where it is, what it offers, and the canonical URLs — the machine-readable
   front door for LLM crawlers.
5. **Semantic HTML, zero JS-required content**: real `<h1>`–`<h3>` hierarchy,
   `<address>`, `<table>` for menus/prices, `<time>` for hours. If content only
   exists after JS executes, most AI crawlers never see it. Server-rendered or
   static, always.
6. **Entity consistency**: the NAP string on the website must byte-match the GBP
   profile and the Yelp/BBB/chamber listings (see local_seo_dominance). One
   wrong suite number splits the entity.
7. **Quotable specifics**: engines cite pages with concrete, verifiable facts —
   exact prices, exact hours, exact service lists, named staff. Vague marketing
   copy doesn't get cited. (Ghost's Source-Ledger rule already guarantees these
   are real facts.)
8. **Clean internal linking**: every service page reachable in ≤2 clicks;
   descriptive anchors ("tasting menu pricing", never "click here").

## Verification (ship-gate, in the SEO pack)
- Rich Results test passes for LocalBusiness + FAQPage.
- `curl` the page with JS disabled — all answers present in raw HTML.
- NAP byte-match check across site/GBP/Yelp/chamber.
- llms.txt present and accurate.
- The five questions a customer actually asks are answered in the first 100
  words of their section.
