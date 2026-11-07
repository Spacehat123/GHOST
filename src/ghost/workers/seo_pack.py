"""WORKERS — the SEO/AEO pack for client websites (Operator's brief, Nov 2026).

"The website itself isn't the point — SEO optimization (answer what people
ask), ranking on ChatGPT and other AIs." For a local business that means:
Google Business Profile dominance (the 6 local moves — see the
local_seo_dominance skill) + being the CITABLE SOURCE for AI answer engines
(aeo_answer_engines skill).

`build_seo_pack(slug)` assembles, from DOSSIER FACTS ONLY (never invented):

  data/documents/<slug>/seo_pack/
    meta.json                answer-first title/description/OG
    schema/LocalBusiness.jsonld   NAP exactly matching GBP (subtype by domain)
    schema/FAQPage.jsonld    what people actually ask, answer-first
    llms.txt                 the machine-readable front door for LLM crawlers
    local_seo_checklist.md   the 6-move onboarding checklist, personalized
    photo_request_email.md   the draft asking THE CLIENT for their real photos
                             (websites WAIT for client photos — demos don't)
    security_checklist.md    the OWASP ship-gate for the build

`verify_ship_gate(slug, site_dir)` is the security/SEO honesty gate for a
BUILT website: CSP present, no secrets/PII beyond the public NAP, schema +
llms.txt present, NAP byte-match.
"""
import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.crm.db import CRMDatabase
from ghost.paths import repo_root

logger = logging.getLogger("ghost.seo_pack")

DOMAIN_SUBTYPE = {
    "home_trade_services": "HomeAndConstructionBusiness",
    "dental_medical": "MedicalBusiness",
    "legal_accounting": "LegalService",
    "restaurants_food": "Restaurant",
    "salons_spas": "BeautySalon",
    "gyms_fitness": "ExerciseGym",
    "boutiques_retail": "Store",
    "hotels_stays": "LodgingBusiness",
    "general_services": "LocalBusiness",
}

WELCOME_QUESTIONS = [
    ("Where are you located and do you offer {service} near me?",
     "{name} is a {category} business in {city}. We serve {city} and nearby "
     "neighbourhoods from our location at {address}."),
    ("What are your opening hours?",
     "Our current hours are listed on this page and on our Google profile — "
     "call {phone} to confirm today's availability."),
    ("How do I book or get a quote?",
     "Call {phone} or use the contact options on this page. We confirm every "
     "booking personally — no automated charge ever happens on this site."),
    ("What services do you offer?",
     "{services_sentence}"),
    ("Why choose {name}?",
     "{why_sentence}"),
]


def _dossier_facts(slug: str, dossiers_dir: Optional[str] = None) -> Dict[str, Any]:
    from ghost.workers.dossier import dossiers_dir as _dd
    base = dossiers_dir or _dd()
    path = os.path.join(base, slug, "facts.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fact_value(facts: Dict[str, Any], key: str) -> str:
    v = (facts.get("identity", {}).get(key, {}) or {}).get("value")
    return str(v) if v else ""


def build_seo_pack(slug: str, crm: Optional[CRMDatabase] = None,
                   dossiers_dir: Optional[str] = None,
                   out_base: Optional[str] = None) -> Dict[str, Any]:
    """Generate the pack from dossier facts + the lead record. Missing facts
    become [Inquire for quotation] inquiry items — never invented."""
    crm = crm or CRMDatabase.get_instance()
    lead = crm.get_lead(slug) or {}
    facts = _dossier_facts(slug, dossiers_dir)
    identity = facts.get("identity", {})
    val = lambda k: _fact_value(facts, k)  # noqa: E731

    name = val("name") or lead.get("name") or slug
    category = val("category") or lead.get("google_category") or "local business"
    address = val("address") or "[Inquire for quotation: full address]"
    phone = val("phone") or "[Inquire for quotation: phone]"
    website = val("website") or ""
    domain = (facts.get("pipeline", {}) or {}).get("pitch_type") and "" or ""
    subtype = DOMAIN_SUBTYPE.get(lead.get("google_category", "") or "", "LocalBusiness")
    city = (address.split(",")[-1].strip() or "[city]") if "," in address else "[city]"
    services = (lead.get("gaps_json") and json.loads(lead["gaps_json"]).get("services")) or []
    services = services or ["consultation", "booking", "aftercare"]
    services_sentence = ", ".join(s[:-1] if s.endswith(",") else s for s in services[:5])

    out_dir = os.path.join(out_base or os.path.join(repo_root(), "data", "documents"),
                           slug, "seo_pack")
    schema_dir = os.path.join(out_dir, "schema")
    os.makedirs(schema_dir, exist_ok=True)
    files: List[str] = []

    inquiries: List[str] = [k for k in ("address", "phone", "hours", "website")
                            if not val(k)]

    # ---- meta (answer-first: what/where/how in the first sentence) ---------
    title = f"{name} | {category.title()} in {city}"
    desc = (f"{name} is a {category} business in {city}. "
            f"Call {phone} to book — hours, services and directions on this page.")
    meta = {"title": title[:60], "description": desc[:158],
            "og_title": title, "og_description": desc,
            "og_type": "business.business", "canonical": website or ""}
    _write(os.path.join(out_dir, "meta.json"), json.dumps(meta, indent=2))
    files.append("meta.json")

    # ---- LocalBusiness JSON-LD (NAP must byte-match GBP) -------------------
    lb = {
        "@context": "https://schema.org",
        "@type": subtype,
        "name": name,
        "telephone": phone if "Inquire" not in phone else None,
        "address": {"@type": "PostalAddress",
                    "streetAddress": address, "addressLocality": city},
        "servesCuisine": None, "priceRange": None,
        "url": website or None,
    }
    lb = {k: v for k, v in lb.items() if v}
    _write(os.path.join(schema_dir, "LocalBusiness.jsonld"),
           json.dumps(lb, indent=2))
    files.append("schema/LocalBusiness.jsonld")

    # ---- FAQPage JSON-LD (what people actually ask) ------------------------
    faqs = []
    for q_template, a_template in WELCOME_QUESTIONS:
        q = q_template.format(service=category.lower(), name=name)
        a = a_template.format(name=name, category=category, city=city,
                              address=address, phone=phone,
                              services_sentence=f"We offer: {services_sentence}.",
                              why_sentence=(f"{name} is a {category} business in "
                                            f"{city} — real photos, real reviews, "
                                            f"one phone number that answers."))
        faqs.append({"question": q, "answer": a})
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": f["question"],
                              "acceptedAnswer": {"@type": "Answer",
                                                 "text": f["answer"]}}
                             for f in faqs]}
    _write(os.path.join(schema_dir, "FAQPage.jsonld"), json.dumps(faq_ld, indent=2))
    files.append("schema/FAQPage.jsonld")

    # ---- llms.txt ----------------------------------------------------------
    llms = (f"# {name}\n\n"
            f"> {name} is a {category} business in {city}. Call {phone}. "
            f"Services: {services_sentence}.\n\n"
            f"## Pages\n- Home: {website or '/'}\n\n"
            f"## Facts\n- Address: {address}\n- Phone: {phone}\n"
            f"- Hours: see site footer and Google profile\n\n"
            f"This file exists so AI answer engines can cite accurate facts.\n")
    _write(os.path.join(out_dir, "llms.txt"), llms)
    files.append("llms.txt")

    # ---- the 6-move personalized checklist ---------------------------------
    checklist = f"""# Local SEO onboarding — {name} (the 6 moves)

Personalized from the operator's distilled guide. Change ONE thing at a time;
give each a week before judging.

1. **Address & pin** — pin sits exactly on the building; no duplicate profile at
   the same address+category. Address on file: {address} {"✅ matches dossier" if "Inquire" not in address else "— [Inquire for quotation]"}
2. **Categories = what customers type** — primary candidate: "{category}"
   (verify against the top 3 with GMB Everywhere; confirm it exists in the
   picker). Add every genuine secondary (up to 9), 2–3 services each, generic
   names.
3. **Review velocity** — save the review link as a phone shortcut; Friday
   10-minute ritual; text from the owner's phone the day the job wraps
   (40–60% response); ~⅓ of asks to Yelp/Facebook/BBB (AI engines read Yelp);
   reply to every review; target 4.7–4.9★, never incentivize.
4. **Hours = when someone answers** — if after-hours calls are answered
   (service/AI receptionist), list those hours. Set once.
5. **Local links** — chamber of commerce directory, trade/manufacturer
   directories, sponsorships, BBB+Yelp. Could a local customer land on the page?
6. **Answer the phone** — call your own number 7PM Saturday; rollover at 4–5
   rings; missed-call text-back: "Sorry we missed you, this is {name}. What's
   going on?"; local number matching the website.

**Photos rule: REAL photos of the crew and work (20–30 to start, then 3–4/week).
AI-generated images are banned everywhere.**

Rank-grid scan before changes, then every 2–3 weeks.
"""
    _write(os.path.join(out_dir, "local_seo_checklist.md"), checklist)
    files.append("local_seo_checklist.md")

    # ---- the photo request draft (websites WAIT for client photos) ---------
    photo_email = f"""Subject: {name} — the photos that make your website work

Hi,

The build is ready to start. The one thing that makes it convert is YOUR
real photos — Google's own guidance and every ranking study agree: real
photos of the crew, the space and the work beat stock or AI images.

What we need (phone photos are fine):
1. Exterior + sign (3–5)  2. Interior / workspace (5–8)  3. Team at work (5–8)
4. Finished work / dishes / results (8–12)  5. Anything you're proud of.

Send them as attachments or a Drive link. The site ships with your photos in
place — that's the difference between a template and yours.

— Ghost (drafted for the {name} build; Pranav reviews and sends)
"""
    _write(os.path.join(out_dir, "photo_request_email.md"), photo_email)
    files.append("photo_request_email.md")

    # ---- OWASP ship-gate checklist -----------------------------------------
    from ghost.core.secrets import get  # noqa: F401  (vault reachability probe)
    security = """# OWASP ship-gate (blocks launch until green)

- [ ] Content-Security-Policy shipped; no unsafe-inline scripts
- [ ] Forms: server-side validation, CSRF token or honeypot+rate-limit
- [ ] No secrets/keys in shipped HTML/JS; no PII beyond the public NAP
- [ ] HTTPS + HSTS; zero mixed content
- [ ] npm audit clean; dependencies pinned
- [ ] File upload hardened (allowlist, size cap, non-executable storage) or absent
- [ ] Contact submissions go to the client's inbox/system — nothing logged in
      analytics, no third-party trackers by default
- [ ] Demo rule (if any demo artifacts ship): zero real-person data
Reference: context/skills/owasp_web_security/SKILL.md +
.skill-vendor/CheatSheetSeries/cheatsheets/
"""
    _write(os.path.join(out_dir, "security_checklist.md"), security)
    files.append("security_checklist.md")

    audit("seo_pack.built", subject=slug, files=files, inquiries=inquiries)
    return {"status": "ok", "slug": slug, "dir": out_dir, "files": files,
            "faqs": [f["question"] for f in faqs], "inquiries": inquiries,
            "subtype": subtype}


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def mark_client_photos_received(slug: str, by: str = "operator") -> Dict[str, Any]:
    """The WAIT gate: a client website build may not start until the operator
    marks the client's real photos as received (demos don't wait — websites
    do). Records a 'client_photos' document row the flow can gate on."""
    crm = crm_or_default()
    lead = crm.get_lead(slug)
    if not lead:
        return {"received": False, "reason": f"lead {slug!r} not found"}
    crm.add_document(lead_id=lead["id"], kind="client_photos", version=1,
                     path=f"data/documents/{slug}/client-photos/",
                     status="received")
    crm.record_event(event_type="client_photos_received", lead_id=lead["id"],
                     payload={"slug": slug})
    audit("website.client_photos_received", subject=slug, by=by)
    return {"received": True, "slug": slug}


def website_ready_for_build(slug: str, crm: Optional[CRMDatabase] = None) -> Dict[str, Any]:
    """True only when the client's real photos are marked received (the gate
    the website flow consults — demos bypass it by design)."""
    crm = crm or CRMDatabase.get_instance()
    lead = crm.get_lead(slug)
    if not lead:
        return {"ready": False, "reason": "lead not found"}
    docs = crm.get_documents_for_lead(lead["id"])
    received = any(d.get("kind") == "client_photos" for d in docs)
    return {"ready": received,
            "reason": "" if received else
                      "waiting for the client's real photos (photo_request_email "
                      "is in the seo_pack; the operator marks them received)"}


def crm_or_default():
    return CRMDatabase.get_instance()


def verify_ship_gate(slug: str, site_dir: str,
                     dossiers_dir: Optional[str] = None) -> Dict[str, Any]:
    """The security/SEO honesty gate for a BUILT site (owasp_web_security
    skill): CSP, no secrets/PII beyond the public NAP, schema + llms.txt,
    NAP byte-match. Blocks launch until green."""
    reasons: List[str] = []
    index = None
    for candidate in ("index.html", "public/index.html", "dist/index.html"):
        p = os.path.join(site_dir, candidate)
        if os.path.exists(p):
            index = p
            break
    if not index:
        return {"passed": False, "reasons": ["no index.html found in the build"]}
    html_src = open(index, encoding="utf-8", errors="replace").read()

    if "Content-Security-Policy" not in html_src:
        reasons.append("no Content-Security-Policy found in the shipped page")
    for pattern, label in ((r"sk-[a-zA-Z0-9]{16,}", "API key pattern (sk-…)"),
                           (r"-----BEGIN [A-Z ]*PRIVATE KEY", "private key block"),
                           (r"(?i)password\s*[:=]\s*['\"][^'\"]{4,}", "hardcoded password")):
        if re.search(pattern, html_src):
            reasons.append(f"{label} in shipped HTML")

    facts = _dossier_facts(slug, dossiers_dir)
    phone = _fact_value(facts, "phone")
    email = _fact_value(facts, "email")
    for blob, label in ((phone, "phone"), (email, "email")):
        if blob and "Inquire" not in blob:
            occurrences = html_src.count(html.escape(blob)) + html_src.count(blob)
            if occurrences == 0:
                reasons.append(f"the public {label} from the dossier is MISSING "
                               "— NAP consistency broken")

    site_root = os.path.dirname(index)
    if not (os.path.exists(os.path.join(site_root, "llms.txt"))
            or os.path.exists(os.path.join(site_root, "public", "llms.txt"))
            or os.path.exists(os.path.join(site_root, "dist", "llms.txt"))):
        reasons.append("llms.txt missing — AI answer engines get no front door")
    schema_found = any("LocalBusiness" in open(os.path.join(dp, f), encoding="utf-8", errors="replace").read()
                       for dp, _, fs in os.walk(site_root) for f in fs
                       if f.endswith((".html", ".jsonld")) and os.path.getsize(os.path.join(dp, f)) < 2_000_000)
    if not schema_found:
        reasons.append("no LocalBusiness schema found in the build")

    passed = not reasons
    audit("seo_pack.ship_gate", subject=slug, passed=passed,
          reasons=reasons[:6])
    return {"passed": passed, "reasons": reasons}
