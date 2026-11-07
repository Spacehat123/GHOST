"""Autonomous 5-Stage Demo Building State Machine for Ghost.

Orchestrates the client acquisition demo pipeline for ANY local business type:
- Phase 1: Recon & Brand Extraction (Scraper Node via web_search, Brave CDP & Source Ledger)
- Phase 2: Generate DESIGN_BRIEF.md (Design token blueprint, domain profile & section map)
- Phase 3: Sectional Component Building (Domain-specific copy, active booking JS, zero fake metrics)
- Phase 4: Visual Self-Healing & Verification (Brave CDP headless verification at 390x844 & 1440x900)
- Phase 5: Instant Preview & Handover (Vercel deployment check + clean personal outreach from user)
"""
import asyncio
import os
import re
import json
import base64
import logging
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple
import websockets

from ghost.workers.resources import PREMIUM_RESOURCES, get_demo_starter_kit
from ghost.workers.recon_scout import ReconScout, format_author_name, is_venue_photo
from ghost.workers.audit_engine import PerformanceAuditor
from ghost.workers.source_ledger import SourceLedger, infer_currency_symbol
from ghost.workers.deploy import VercelDeployer, DEMOS_DIR
from ghost.mcp.bridge import BraveCDPBridge, CDPAutomationEngine, WebSearchBridge, request_tactical_authorization
from ghost.crm.db import CRMDatabase

try:
    from context.user import userinfo
    USER_NAME = getattr(userinfo, "NAME", getattr(userinfo, "USER", "Pranav"))
except Exception:
    USER_NAME = "Pranav"

logger = logging.getLogger("ghost.workers.state_machine")

# Persona Firewall: Outbound copy must NEVER leak roleplay, persona, or local paths
BANNED_OUTBOUND_TERMS = [
    "ghost",
    "task force",
    "task force 141",
    "chief of staff",
    "deep-strike",
    "commander",
    "file://",
    "/home/",
    "\\home\\",
    "internal_preview_path",
    "local_demo_path",
    "sir",
    "mate",
    "boss",
    "$1,000"
]


def sanitize_outbound(text: str) -> str:
    """
    Validates that client-facing outbound copy (PITCH_KIT, email, WhatsApp, DM)
    contains zero internal persona terms, roleplay jargon, local file paths,
    or internal pricing references.
    Raises ValueError if any banned term is detected.
    """
    if not text:
        return ""
    text_lower = text.lower()
    for term in BANNED_OUTBOUND_TERMS:
        pattern = r'\b' + re.escape(term.lower()) + r'\b' if term.isalnum() else re.escape(term.lower())
        if re.search(pattern, text_lower):
            raise ValueError(f"Persona firewall violation: Found banned term '{term}' in client-facing outbound copy.")
    return text


def split_business_name(name: str) -> Tuple[str, Optional[str]]:
    """
    Splits multi-part business names (e.g. 'Topaz Detailing at The Peninsula London')
    into primary title ('Topaz Detailing') and subtitle badge ('at The Peninsula London').
    Prevents giant headlines from dominating mobile viewports.
    """
    cleaned = name.strip()
    split_patterns = [r"\s+(?:at|@)\s+", r"\s*[-–—|]\s*"]
    for pat in split_patterns:
        m = re.search(pat, cleaned, flags=re.IGNORECASE)
        if m:
            main_title = cleaned[:m.start()].strip()
            subtitle = cleaned[m.start():].strip().lstrip("-–—|@ ").strip()
            if len(main_title) >= 3 and len(subtitle) >= 2:
                return main_title, subtitle
    return cleaned, None


# =============================================================================
# Domain Classifier & Catalog Mapping
# =============================================================================

GOOGLE_CATEGORY_MAP = {
    # 10. Home & Trade Services (Detailing, Auto, Trades, Pets)
    "car detailing service": "home_trade_services",
    "auto detailing service": "home_trade_services",
    "car wash": "home_trade_services",
    "car repair and maintenance": "home_trade_services",
    "auto repair shop": "home_trade_services",
    "car repair": "home_trade_services",
    "auto repair": "home_trade_services",
    "autocare": "home_trade_services",
    "auto care": "home_trade_services",
    "mechanic": "home_trade_services",
    "car service": "home_trade_services",
    "auto restoration service": "home_trade_services",
    "auto tune up service": "home_trade_services",
    "car inspection station": "home_trade_services",
    "valet parking service": "home_trade_services",
    "plumber": "home_trade_services",
    "plumbing service": "home_trade_services",
    "electrician": "home_trade_services",
    "electrical installation service": "home_trade_services",
    "hvac contractor": "home_trade_services",
    "air conditioning repair service": "home_trade_services",
    "handyman": "home_trade_services",
    "roofing contractor": "home_trade_services",
    "locksmith": "home_trade_services",
    "carpenter": "home_trade_services",
    "joiner": "home_trade_services",
    "glazier": "home_trade_services",
    "painter": "home_trade_services",
    "house painter": "home_trade_services",
    "flooring contractor": "home_trade_services",
    "tile contractor": "home_trade_services",
    "landscaping": "home_trade_services",
    "tree service": "home_trade_services",
    "pest control service": "home_trade_services",
    "cleaning service": "home_trade_services",
    "window cleaning service": "home_trade_services",
    "carpet cleaning service": "home_trade_services",
    "pet groomer": "home_trade_services",
    "pet groomers": "home_trade_services",
    "pet grooming service": "home_trade_services",
    "dog groomer": "home_trade_services",
    "dog groomers": "home_trade_services",
    "cat groomer": "home_trade_services",
    "mobile pet groomer": "home_trade_services",
    "pet care service": "home_trade_services",
    "dog day care center": "home_trade_services",

    # 1. Restaurants & Cafés (Pubs, Bars, Eateries)
    "restaurant": "restaurants_cafes",
    "fine dining restaurant": "restaurants_cafes",
    "bistro": "restaurants_cafes",
    "cafe": "restaurants_cafes",
    "coffee shop": "restaurants_cafes",
    "espresso bar": "restaurants_cafes",
    "bar": "restaurants_cafes",
    "bars": "restaurants_cafes",
    "pub": "restaurants_cafes",
    "pubs": "restaurants_cafes",
    "public house": "restaurants_cafes",
    "cocktail bar": "restaurants_cafes",
    "wine bar": "restaurants_cafes",
    "sports bar": "restaurants_cafes",
    "irish pub": "restaurants_cafes",
    "traditional pub": "restaurants_cafes",
    "lounge bar": "restaurants_cafes",
    "tavern": "restaurants_cafes",
    "inn": "restaurants_cafes",
    "brewery": "restaurants_cafes",
    "brewpub": "restaurants_cafes",
    "taproom": "restaurants_cafes",
    "beer garden": "restaurants_cafes",
    "gastropub": "restaurants_cafes",
    "diner": "restaurants_cafes",
    "eatery": "restaurants_cafes",
    "italian restaurant": "restaurants_cafes",
    "french restaurant": "restaurants_cafes",
    "japanese restaurant": "restaurants_cafes",
    "sushi restaurant": "restaurants_cafes",
    "steak house": "restaurants_cafes",
    "seafood restaurant": "restaurants_cafes",
    "tapas restaurant": "restaurants_cafes",
    "brasserie": "restaurants_cafes",
    "noodle shop": "restaurants_cafes",

    # 2. Salons & Spas (Beauty, Hair, Wellness)
    "hair salon": "salons_spas",
    "hairdresser": "salons_spas",
    "barber shop": "salons_spas",
    "barbershop": "salons_spas",
    "barber": "salons_spas",
    "beauty salon": "salons_spas",
    "beauty parlour": "salons_spas",
    "beauty studio": "salons_spas",
    "beautician": "salons_spas",
    "spa": "salons_spas",
    "day spa": "salons_spas",
    "nail salon": "salons_spas",
    "manicurist": "salons_spas",
    "pedicurist": "salons_spas",
    "facial spa": "salons_spas",
    "massage therapist": "salons_spas",
    "massage spa": "salons_spas",
    "skin care clinic": "salons_spas",
    "wellness center": "salons_spas",
    "cosmetics store": "salons_spas",
    "aesthetic clinic": "salons_spas",
    "eyelash salon": "salons_spas",
    "eyebrow bar": "salons_spas",
    "waxing hair removal service": "salons_spas",
    "laser hair removal service": "salons_spas",
    "permanent make-up clinic": "salons_spas",
    "tanning salon": "salons_spas",
    "make-up artist": "salons_spas",
    "hair extension technician": "salons_spas",

    # 3. Gyms & Fitness
    "gym": "gyms_fitness",
    "fitness center": "gyms_fitness",
    "personal trainer": "gyms_fitness",
    "yoga studio": "gyms_fitness",
    "pilates studio": "gyms_fitness",
    "crossfit gym": "gyms_fitness",
    "boxing gym": "gyms_fitness",
    "martial arts club": "gyms_fitness",
    "fitness boot camp": "gyms_fitness",
    "gymnastics center": "gyms_fitness",

    # 4. Dental & Medical
    "dentist": "dental_medical",
    "dental clinic": "dental_medical",
    "cosmetic dentist": "dental_medical",
    "orthodontist": "dental_medical",
    "pediatric dentist": "dental_medical",
    "doctor": "dental_medical",
    "medical clinic": "dental_medical",
    "physiotherapist": "dental_medical",
    "physiotherapy clinic": "dental_medical",
    "chiropractor": "dental_medical",
    "osteopath": "dental_medical",
    "optician": "dental_medical",
    "optometrist": "dental_medical",
    "eye care center": "dental_medical",
    "podiatrist": "dental_medical",
    "dermatologist": "dental_medical",
    "acupuncture clinic": "dental_medical",

    # 5. Boutiques & Retail (Fashion, Tailors, Jewelers)
    "clothing store": "boutiques_retail",
    "boutique": "boutiques_retail",
    "fashion boutique": "boutiques_retail",
    "designer clothing store": "boutiques_retail",
    "tailor": "boutiques_retail",
    "custom tailor": "boutiques_retail",
    "men's tailor": "boutiques_retail",
    "dressmaker": "boutiques_retail",
    "clothing alteration service": "boutiques_retail",
    "sewing shop": "boutiques_retail",
    "haute couture fashion house": "boutiques_retail",
    "fashion designer": "boutiques_retail",
    "bridal shop": "boutiques_retail",
    "vintage clothing store": "boutiques_retail",
    "jewelry store": "boutiques_retail",
    "jeweler": "boutiques_retail",
    "shoe store": "boutiques_retail",
    "fashion accessories store": "boutiques_retail",
    "watch store": "boutiques_retail",
    "leather goods store": "boutiques_retail",

    # 6. Real Estate
    "real estate agency": "real_estate",
    "real estate agents": "real_estate",
    "estate agent": "real_estate",
    "estate agents": "real_estate",
    "letting agent": "real_estate",
    "letting agency": "real_estate",
    "real estate consultant": "real_estate",
    "commercial real estate agency": "real_estate",
    "property management company": "real_estate",

    # 7. Photographers
    "photographer": "photographers",
    "photography studio": "photographers",
    "wedding photographer": "photographers",
    "commercial photographer": "photographers",
    "portrait studio": "photographers",
    "video production service": "photographers",

    # 8. Hotels & Stays
    "hotel": "hotels_stays",
    "resort hotel": "hotels_stays",
    "boutique hotel": "hotels_stays",
    "bed & breakfast": "hotels_stays",
    "motel": "hotels_stays",
    "villa": "hotels_stays",
    "holiday home": "hotels_stays",
    "guest house": "hotels_stays",

    # 9. Bakeries
    "bakery": "bakeries",
    "pastry shop": "bakeries",
    "cake shop": "bakeries",
    "patisserie": "bakeries",
    "cupcake shop": "bakeries",
    "donut shop": "bakeries",

    # 11. Coaches & Consultants
    "business management consultant": "coaches",
    "consultant": "coaches",
    "life coach": "coaches",
    "executive search firm": "coaches",
    "career guidance service": "coaches",

    # 12. Legal & Accounting
    "law firm": "legal_accounting",
    "lawyer": "legal_accounting",
    "attorney": "legal_accounting",
    "solicitor": "legal_accounting",
    "solicitors": "legal_accounting",
    "barrister": "legal_accounting",
    "notary public": "legal_accounting",
    "accounting firm": "legal_accounting",
    "accountant": "legal_accounting",
    "accountants": "legal_accounting",
    "certified public accountant": "legal_accounting",
    "chartered accountant": "legal_accounting",
    "accounting service": "legal_accounting",
    "bookkeeping service": "legal_accounting",
    "tax consultant": "legal_accounting",

    # 13. Wedding & Florists
    "wedding planner": "wedding_florists",
    "event planner": "wedding_florists",
    "florist": "wedding_florists",
    "florists": "wedding_florists",
    "flower designer": "wedding_florists",
    "flower delivery": "wedding_florists",
    "wedding venue": "wedding_florists",

    # 14. Tutors & Academies
    "tutoring service": "tutors",
    "private tutor": "tutors",
    "test preparation service": "tutors",
    "learning center": "tutors",
    "music school": "tutors",
    "language school": "tutors",
    "driving school": "tutors",
    "driving instructor": "tutors",
}

REGEX_DOMAIN_RULES: List[Tuple[str, str]] = [
    ("home_trade_services", r"\b(detail(ing|er|ers)?|ceramic coating|paint protection|paint correction|ppf|valet(ing)?|autocare|auto care|car care|mechanic|auto repair|motor repair|body shop|car wash|garage|plumb(ing|er|ers)?|electric(ian|ians|al)?|hvac|handyman|roof(ing|er|ers)?|locksmith|carpenter|landscaping|pest control|pet groom(ing|er|ers)?|dog groom(ing|er|ers)?|pet care|groomer|groomers|grooming|carpet clean(ing)?|clean(ing|er|ers)?)\b"),
    ("salons_spas", r"\b(barber(shop)?|hairstylist|hair salon|hair studio|haircut|hairdresser|hair|salon|spa|beauty studio|beauty salon|beauty|beautician|cosmetic(s)?|aesthetic(s)?|aesthetic clinic|massage|facial|skincare|skin care|skin clinic|esthetician|nail salon|nails?|nail studio|nail artist|lashes?|eyelashes?|lash studio|lash artist|wellness sanctuary)\b"),
    ("dental_medical", r"\b(dental|dentist|orthodont(ic|ist)|teeth|smile makeover|implant|doctor|physio(therapy|therapist)?|physio|chiropract(ic|or)?|dermatolog(y|ist)|medical clinic|dental clinic|eye clinic|clinic|optician|optometrist|eyecare)\b"),
    ("gyms_fitness", r"\b(gym|crossfit|fitness|pilates|yoga|athletic club|workout|strength training|personal train(ing|er))\b"),
    ("bakeries", r"\b(bakery|patisserie|pastry|croissant|sourdough|cake studio|artisan bakery|bakeshop)\b"),
    ("restaurants_cafes", r"\b(pub|pubs|public house|tavern|inn|brewpub|taproom|gastropub|cocktail bar|wine bar|speakeasy|bar|bars|restaurant|bistro|cafe|coffee|espresso|roastery|diner|trattoria|brasserie|eatery|omakase|tasting menu|steak house|steakhouse|steak|pizzeria|pizza|grill)\b"),
    ("boutiques_retail", r"\b(boutique|couture|haute couture|bespoke suiting|tailor(ing|s)?|dressmaker|alterations?|jewel(ry|er)?|atelier|ready-to-wear|lookbook|apparel|menswear|womenswear)\b"),
    ("real_estate", r"\b(real estate|estate agent(s)?|realty|property|brokerage|penthouse|villas|condos|apartments|realtor)\b"),
    ("photographers", r"\b(photograph(y|er|ers)?|cinematograph(y|er)?|portrait studio|photo shoot|videograph(y|er)?)\b"),
    ("hotels_stays", r"\b(hotel|resort|boutique stay|homestay|luxury suite|bungalow|villa rental)\b"),
    ("coaches", r"\b(executive coach|life coach|mentorship|career mentor|keynote speaker|consulting)\b"),
    ("legal_accounting", r"\b(law|legal|law firm|lawyer|attorney|solicitor|solicitors|barrister|cpa|chartered accountant|accountant|accountants|accounting|tax advisor|tax advisory|litigation)\b"),
    ("wedding_florists", r"\b(wedding planner|bridal stylist|florist|florists|floristry|floral studio|botanical stylist)\b"),
    ("tutors", r"\b(tutor(ing|s)?|test prep|sat prep|academy|learning center|math coaching|driving school|driving instructor)\b"),
]

DOMAIN_SNIPPET_KEYWORDS: Dict[str, List[str]] = {
    "restaurants_cafes": ["menu", "dining", "reservations", "chef", "table", "tasting", "lunch", "dinner", "cocktails", "wine", "dishes", "cuisine", "pub", "beer", "coffee"],
    "salons_spas": ["haircut", "styling", "stylist", "facial", "massage", "skincare", "barber", "lashes", "manicure", "pedicure", "salon", "spa"],
    "dental_medical": ["dental", "dentist", "teeth", "clinic", "doctor", "patients", "consultation", "implants", "physio", "chiropractor", "optician"],
    "gyms_fitness": ["fitness", "gym", "workout", "training", "coaching", "membership", "pilates", "yoga", "classes", "athletic"],
    "home_trade_services": ["detailing", "plumbing", "electrician", "contractor", "repair", "handyman", "installation", "maintenance", "roofing", "grooming"],
    "boutiques_retail": ["boutique", "fashion", "collection", "tailoring", "alterations", "apparel", "designer", "couture", "jeweler"],
    "real_estate": ["real estate", "property", "broker", "listings", "apartments", "realty", "estate agent", "tenants"],
    "photographers": ["photography", "photoshoot", "portraits", "photographer", "studio", "wedding photography"],
    "hotels_stays": ["hotel", "resort", "rooms", "suites", "accommodation", "stay", "booking"],
    "bakeries": ["bakery", "pastries", "baked daily", "cakes", "croissants", "sourdough", "patisserie"],
    "coaches": ["executive coaching", "mentor", "life coaching", "consulting", "keynote"],
    "legal_accounting": ["attorney", "lawyer", "legal", "cpa", "accounting", "tax", "litigation", "solicitor"],
    "wedding_florists": ["florist", "bouquets", "floral", "wedding planner", "flowers"],
    "tutors": ["tutoring", "tutor", "test prep", "sat prep", "learning center", "classes", "driving school"]
}


def classify_business(category: Optional[str], name: str, snippets: str = "") -> Tuple[str, float, bool, List[Dict[str, Any]]]:
    """
    Classifies a business into one of the 14 catalog industries.
    Returns: (domain, confidence, needs_confirmation, candidates)

    Strict Priority Hierarchy:
    1. Google Category direct mapping or category regex (confidence 0.95 / 0.90)
    2. Business Name ALONE evaluated via word-boundary regex (confidence 0.85).
       CRITICAL: Snippets are NEVER mixed with business name here to prevent nearby
       business mentions from poisoning classification (e.g. 'Autocare' near a restaurant).
    3. Snippets ALONE as weak signal requiring >= 2 distinct keyword hits (confidence 0.60, needs_confirmation=True).
       Single casual mentions of 'coffee' or 'restaurant' in area snippets are strictly ignored.
    4. Fail closed: fallback to 'general_services' and mark needs_confirmation=True (confidence 0.20).
       NEVER default to fine_dining or food/drink!
    """
    candidates_dict: Dict[str, Dict[str, Any]] = {}

    name_clean = name.strip().lower().replace("’", "'") if name else ""
    snippets_clean = snippets.strip().lower() if snippets and isinstance(snippets, str) else ""

    matched_domain: Optional[str] = None
    matched_conf: float = 0.20
    needs_conf: bool = True

    # 1. Google Category
    if category and isinstance(category, str):
        cat_clean = category.strip().lower()
        if cat_clean in GOOGLE_CATEGORY_MAP:
            matched_domain = GOOGLE_CATEGORY_MAP[cat_clean]
            matched_conf = 0.95
            needs_conf = False
            candidates_dict[matched_domain] = {
                "domain": matched_domain,
                "score": 0.95,
                "reason": f"Google category '{category}' directly mapped to {matched_domain}"
            }
        else:
            for k, dom in GOOGLE_CATEGORY_MAP.items():
                if re.search(r'\b' + re.escape(k) + r'\b', cat_clean):
                    matched_domain = dom
                    matched_conf = 0.90
                    needs_conf = False
                    candidates_dict[dom] = {
                        "domain": dom,
                        "score": 0.90,
                        "reason": f"Google category '{category}' matched keyword '{k}'"
                    }
                    break
            if not matched_domain:
                for dom, pattern in REGEX_DOMAIN_RULES:
                    if re.search(pattern, cat_clean, flags=re.IGNORECASE):
                        matched_domain = dom
                        matched_conf = 0.90
                        needs_conf = False
                        candidates_dict[dom] = {
                            "domain": dom,
                            "score": 0.90,
                            "reason": f"Google category '{category}' matched rule for {dom}"
                        }
                        break

    # 2. Business Name ALONE (no snippet mixing)
    if not matched_domain and name_clean:
        for dom, pattern in REGEX_DOMAIN_RULES:
            m = re.search(pattern, name_clean, flags=re.IGNORECASE)
            if m:
                matched_domain = dom
                matched_conf = 0.85
                needs_conf = False
                candidates_dict[dom] = {
                    "domain": dom,
                    "score": 0.85,
                    "reason": f"Business name matched keyword '{m.group(0)}'"
                }
                break

    # 3. Snippets ALONE (Weak signal requiring >= 2 distinct keyword hits)
    snippet_candidates = []
    if snippets_clean:
        for dom, kw_list in DOMAIN_SNIPPET_KEYWORDS.items():
            hits = [kw for kw in kw_list if re.search(r'\b' + re.escape(kw) + r'\b', snippets_clean)]
            if len(hits) >= 2:
                snippet_candidates.append((dom, 0.60, f"Snippets matched keywords: {', '.join(hits[:3])}"))
            elif len(hits) == 1:
                snippet_candidates.append((dom, 0.35, f"Weak snippet signal (1 keyword: {hits[0]})"))

        snippet_candidates.sort(key=lambda x: x[1], reverse=True)

        for dom, sc, rsn in snippet_candidates:
            if dom not in candidates_dict:
                candidates_dict[dom] = {"domain": dom, "score": sc, "reason": rsn}

        if not matched_domain and snippet_candidates and snippet_candidates[0][1] >= 0.60:
            top_snippet = snippet_candidates[0]
            matched_domain = top_snippet[0]
            matched_conf = top_snippet[1]
            needs_conf = True

    # 4. Fail closed: fallback to general_services with needs_confirmation=True
    if not matched_domain:
        matched_domain = "general_services"
        matched_conf = 0.20
        needs_conf = True
        if "general_services" not in candidates_dict:
            candidates_dict["general_services"] = {
                "domain": "general_services",
                "score": 0.20,
                "reason": "Default neutral service template"
            }

    if "general_services" not in candidates_dict:
        candidates_dict["general_services"] = {
            "domain": "general_services",
            "score": 0.20,
            "reason": "Default neutral fallback"
        }

    # Construct top 3 candidates, ensuring matched_domain is first
    ordered_candidates = [candidates_dict[matched_domain]]
    remaining = [c for d, c in sorted(candidates_dict.items(), key=lambda item: item[1]["score"], reverse=True) if d != matched_domain]
    ordered_candidates.extend(remaining)
    candidates = ordered_candidates[:3]

    return matched_domain, matched_conf, needs_conf, candidates


def detect_domain_profile(business_name: str, location: str = "", text: str = "") -> str:
    """Compatibility shim for existing callers."""
    dom, _, _, _ = classify_business(None, business_name, text)
    return dom


def infer_location_and_phone(business_name: str, given_location: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    Extracts location cleanly.
    Never invents phone numbers. Returns None if phone is unknown.
    """
    raw_loc = given_location.strip() if given_location and given_location.strip() else ""
    if raw_loc:
        return raw_loc, None
    return f"{business_name.split()[0]} Area", None


# =============================================================================
# Curated Domain Assets & Content Systems
# =============================================================================

DOMAIN_CURATED_ASSETS = {
    "home_trade_services": {
        "services": [
            {"title": "Surface Restoration", "category": "Surface Restoration", "price": None, "desc": None},
            {"title": "Protection Systems", "category": "Protection Systems", "price": None, "desc": None},
            {"title": "Interior Refinement", "category": "Interior Refinement", "price": None, "desc": None},
            {"title": "Specialist Treatment", "category": "Specialist Treatment", "price": None, "desc": None}
        ]
    },
    "dental_medical": {
        "services": [
            {"title": "Clinical Diagnostics", "category": "Clinical Diagnostics", "price": None, "desc": None},
            {"title": "Treatment Solutions", "category": "Treatment Solutions", "price": None, "desc": None},
            {"title": "Preventive Care", "category": "Preventive Care", "price": None, "desc": None}
        ]
    },
    "salons_spas": {
        "services": [
            {"title": "Styling Suite", "category": "Styling Suite", "price": None, "desc": None},
            {"title": "Hair & Scalp Care", "category": "Hair & Scalp Care", "price": None, "desc": None},
            {"title": "Color Treatments", "category": "Color Treatments", "price": None, "desc": None}
        ]
    },
    "gyms_fitness": {
        "services": [
            {"title": "Performance Assessment", "category": "Performance Assessment", "price": None, "desc": None},
            {"title": "Coaching Programs", "category": "Coaching Programs", "price": None, "desc": None},
            {"title": "Group Conditioning", "category": "Group Conditioning", "price": None, "desc": None}
        ]
    },
    "general_services": {
        "services": [
            {"title": "Consultation", "category": "Consultation", "price": None, "desc": None},
            {"title": "Core Service Delivery", "category": "Core Service Delivery", "price": None, "desc": None},
            {"title": "Client Support", "category": "Client Support", "price": None, "desc": None}
        ]
    },
    "restaurants_cafes": {
        "services": [
            {"title": "Tasting Menus", "category": "Tasting Menus", "price": None, "desc": None},
            {"title": "Midday Menu", "category": "Midday Menu", "price": None, "desc": None},
            {"title": "Beverage Program", "category": "Beverage Program", "price": None, "desc": None}
        ]
    }
}

GENERAL_SERVICES_ASSETS = DOMAIN_CURATED_ASSETS["general_services"]

# Domain-specific narrative copy definitions
DOMAIN_COPY = {
    "home_trade_services": {
        "tagline_template": "Engineered Precision & Technical Detailing in {location}.",
        "offering_title": "Services & Packages",
        "offering_kicker": "Technical Standards",
        "cta_primary": "Book Service",
        "cta_secondary": "Explore Services",
        "banner_headline": "Direct Service Protocol • Zero Delay",
        "banner_sub": "Instant service request via WhatsApp concierge. Direct confirmation with technician.",
        "philosophy_kicker": "Discipline & Standards",
        "philosophy_title": "Meticulous Craftsmanship & Lasting Protection",
        "philosophy_body_1": "At {business_name}, every vehicle and project is treated with uncompromising technical rigor in {location}.",
        "philosophy_body_2": "We reject rushed work and substandard materials. Our technicians utilize precision multi-stage methodology to deliver lasting surface protection and aesthetic perfection.",
        "quote_attribution": "Senior Specialist",
        "quote_text": "True perfection lies in microscopic surface refinement and uncompromising patience.",
        "atmosphere_title": "Studio & Facility",
        "atmosphere_kicker": "Technical Facility",
        "gallery_1": "Main Detailing Bay",
        "gallery_2": "Curing & Inspection Suite",
        "gallery_3": "Client Consultation Area",
        "how_it_works_steps": [
            ("Step 01", "Select Service", "Choose your required detailing package, ceramic protection, or custom treatment."),
            ("Step 02", "Confirm Details", "Share your vehicle model and preferred date directly via WhatsApp or call."),
            ("Step 03", "Precision Execution", "Drop off your vehicle for dedicated multi-stage treatment by certified specialists.")
        ],
        "booking_title": "Book Your Service Appointment",
        "booking_sub": "Select your service scope and initiate direct confirmation with our technical team.",
        "booking_fields": "automotive",
        "faqs": [
            ("What is the typical turnaround time for treatments?", "Standard multi-stage treatments require 1 to 3 days depending on vehicle condition and curing requirements."),
            ("Do you provide documentation on protection packages?", "Yes. All professional coating applications include documented maintenance guidance and warranty records.")
        ]
    },
    "dental_medical": {
        "tagline_template": "Excellence in Clinical Care & Patient Wellbeing in {location}.",
        "offering_title": "Clinical Treatments",
        "offering_kicker": "Clinical Precision",
        "cta_primary": "Request Appointment",
        "cta_secondary": "View Treatments",
        "banner_headline": "Direct Appointment Access • Transparent Care",
        "banner_sub": "Connect directly with our clinical coordinators without call queues.",
        "philosophy_kicker": "Clinical Standards",
        "philosophy_title": "Modern Healthcare Guided by Integrity & Empathy",
        "philosophy_body_1": "At {business_name}, patient comfort, advanced diagnostics, and long-term wellness guide every treatment plan in {location}.",
        "philosophy_body_2": "We provide transparent guidance, state-of-the-art procedures, and dedicated follow-up care.",
        "quote_attribution": "Clinical Director",
        "quote_text": "Care calibrated to the individual, delivered with precision and genuine reassurance.",
        "atmosphere_title": "Clinic & Surgeries",
        "atmosphere_kicker": "Clinical Environment",
        "gallery_1": "Diagnostic Suite",
        "gallery_2": "Treatment Clinic",
        "gallery_3": "Patient Reception",
        "how_it_works_steps": [
            ("Step 01", "Select Treatment", "Select your consultation or treatment requirement."),
            ("Step 02", "Coordinate Time", "Confirm a convenient appointment slot directly on WhatsApp or telephone."),
            ("Step 03", "Personalized Care", "Receive unhurried, comprehensive clinical care in a relaxed environment.")
        ],
        "booking_title": "Request Your Consultation",
        "booking_sub": "Connect directly with our clinical reception to schedule your appointment.",
        "booking_fields": "medical",
        "faqs": [
            ("What happens during the initial consultation?", "We conduct a thorough evaluation, discuss your objectives, and provide clear options with transparent pricing."),
            ("How do you handle urgent appointments?", "We reserve emergency slots daily for patients requiring rapid consultation.")
        ]
    },
    "general_services": {
        "tagline_template": "Professional Standards & Bespoke Service in {location}.",
        "offering_title": "Capabilities & Offerings",
        "offering_kicker": "Disciplined Standards",
        "cta_primary": "Request Consultation",
        "cta_secondary": "Explore Capabilities",
        "banner_headline": "Direct Client Engagement • Transparent Communication",
        "banner_sub": "Connect directly with our team with zero intermediary delays.",
        "philosophy_kicker": "Standards & Philosophy",
        "philosophy_title": "Uncompromising Quality & Client Trust",
        "philosophy_body_1": "At {business_name}, every engagement is handled with personal dedication and rigor in {location}.",
        "philosophy_body_2": "We focus on measured outcomes, clear accountability, and enduring relationships.",
        "quote_attribution": "Principal & Client Director",
        "quote_text": "Excellence is never accidental; it is the result of disciplined intention and consistent execution.",
        "atmosphere_title": "Practice & Environment",
        "atmosphere_kicker": "Our Space",
        "gallery_1": "Client Consultation Room",
        "gallery_2": "Work & Collaboration Space",
        "gallery_3": "Executive Suite",
        "how_it_works_steps": [
            ("Step 01", "Select Service Scope", "Identify your specific requirements from our capabilities catalog."),
            ("Step 02", "Direct Coordination", "Connect directly via WhatsApp or telephone to establish timelines."),
            ("Step 03", "Tailored Delivery", "Receive dedicated attention and transparent execution from our team.")
        ],
        "booking_title": "Schedule a Consultation",
        "booking_sub": "Select your service requirements and connect directly with our team.",
        "booking_fields": "general",
        "faqs": [
            ("How do I receive an initial quotation?", "Share your project or service scope via WhatsApp or call for a transparent, bespoke proposal."),
            ("What are your standard scheduling lead times?", "We accommodate consultation requests within 24 to 48 hours of initial inquiry.")
        ]
    },
    "restaurants_cafes": {
        "tagline_template": "{name} in {location}.",
        "offering_title": "Curated Offerings",
        "offering_kicker": "Artisanal Precision",
        "cta_primary": "Reserve Table",
        "cta_secondary": "Explore Offerings",
        "banner_headline": "Direct Table Protocol • Zero Intermediary Friction",
        "banner_sub": "Instant booking confirmation via WhatsApp concierge with zero third-party commissions.",
        "philosophy_kicker": "Philosophy & Provenance",
        "philosophy_title": "Architectural Flavor & Uncompromising Standards",
        "philosophy_body_1": "At {business_name}, every detail is calibrated to provide a memorable dining experience in {location}.",
        "philosophy_body_2": "We reject automated mediocrity. Our team pairs artisanal heritage with warm hospitality.",
        "quote_attribution": "Head Concierge",
        "quote_text": "Hospitality crafted with discipline, warmth, and unhurried composure.",
        "atmosphere_title": "Atmosphere & Spaces",
        "atmosphere_kicker": "Visual Ambiance",
        "gallery_1": "The Main Dining Room",
        "gallery_2": "Bar & Lounge",
        "gallery_3": "Private Dining Suite",
        "how_it_works_steps": [
            ("Step 01", "Select Experience", "Choose your preferred seating and dining time."),
            ("Step 02", "Instant WhatsApp", "Connect directly with our team on WhatsApp with your date and party size pre-filled."),
            ("Step 03", "Hospitality", "Arrive to an unhurried, tailored dining experience.")
        ],
        "booking_title": "Reserve Your Table",
        "booking_sub": "Choose your seating and initiate an instant booking conversation directly on WhatsApp.",
        "booking_fields": "dining",
        "faqs": [
            ("What are the reservation lead times?", "Reservations open 14 days in advance. Direct WhatsApp inquiries receive priority confirmation."),
            ("Do you accommodate dietary preferences?", "Yes, please inform us in advance so our kitchen can tailor your experience.")
        ]
    }
}

# Archetype Catalog
ARCHETYPE_MAP = {
    1: {
        "id": 1,
        "name": "Michelin & Fine Dining Editorial",
        "bg": "#fcfbf9", "surface": "#f5f2eb", "accent": "#997328", "accent_secondary": "#b88e3d",
        "text": "#1a1816", "muted": "#68645e", "border": "#e5ded3",
        "dark_bg": "#0c0c0e", "dark_surface": "#16161a", "dark_accent": "#d4af37", "dark_accent_secondary": "#c5a059",
        "dark_text": "#eae6df", "dark_muted": "#8e8a82", "dark_border": "#272521",
        "font_head": "'Playfair Display', serif", "font_body": "'Plus Jakarta Sans', sans-serif",
        "google_fonts": "family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=Plus+Jakarta+Sans:wght@300;400;500;600"
    },
    2: {
        "id": 2,
        "name": "Boutique Resort & Coastal Minimalist",
        "bg": "#faf7f2", "surface": "#eee7dc", "accent": "#735c44", "accent_secondary": "#a86b3b",
        "text": "#22201e", "muted": "#5f5953", "border": "#d8cfbf",
        "dark_bg": "#12100e", "dark_surface": "#1c1815", "dark_accent": "#c2a688", "dark_accent_secondary": "#d99762",
        "dark_text": "#ede7e1", "dark_muted": "#9e9389", "dark_border": "#2d2620",
        "font_head": "'Fraunces', serif", "font_body": "'Inter', sans-serif",
        "google_fonts": "family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,400&family=Inter:wght@300;400;500;600"
    },
    3: {
        "id": 3,
        "name": "Heritage Luxury & Old Money Grand Hotel",
        "bg": "#faf8f5", "surface": "#eee6d8", "accent": "#1b3022", "accent_secondary": "#8a6d3b",
        "text": "#141414", "muted": "#4d4a45", "border": "#dcd1be",
        "dark_bg": "#0d130f", "dark_surface": "#151f19", "dark_accent": "#529368", "dark_accent_secondary": "#d4b373",
        "dark_text": "#f3eee5", "dark_muted": "#8d968f", "dark_border": "#213227",
        "font_head": "'Cinzel', serif", "font_body": "'Cormorant Garamond', serif",
        "google_fonts": "family=Cinzel:wght@500;600;700&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400"
    },
    4: {
        "id": 4,
        "name": "Modern High-Energy Bistro & Cocktail Lounge",
        "bg": "#f9f9fb", "surface": "#eeeff3", "accent": "#d97706", "accent_secondary": "#dc2626",
        "text": "#111827", "muted": "#4b5563", "border": "#d1d5db",
        "dark_bg": "#0f0f11", "dark_surface": "#18181b", "dark_accent": "#f59e0b", "dark_accent_secondary": "#ef4444",
        "dark_text": "#f3f4f6", "dark_muted": "#9ca3af", "dark_border": "#27272a",
        "font_head": "'Space Grotesk', sans-serif", "font_body": "'Inter', sans-serif",
        "google_fonts": "family=Space+Grotesk:wght@500;600;700&family=Inter:wght@300;400;500;600"
    },
    5: {
        "id": 5,
        "name": "Clean Service & Modern Precision",
        "bg": "#f8f9fa", "surface": "#ffffff", "accent": "#0f766e", "accent_secondary": "#0284c7",
        "text": "#0f172a", "muted": "#475569", "border": "#e2e8f0",
        "dark_bg": "#0b1120", "dark_surface": "#1e293b", "dark_accent": "#14b8a6", "dark_accent_secondary": "#38bdf8",
        "dark_text": "#f8fafc", "dark_muted": "#94a3b8", "dark_border": "#334155",
        "font_head": "'Plus Jakarta Sans', sans-serif", "font_body": "'Inter', sans-serif",
        "google_fonts": "family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@300;400;500;600"
    }
}


class AutonomousDemoStateMachine:
    """
    5-Stage pipeline state machine executing autonomous demo production
    for any business sector without domain hallucination or persona leaks.
    """

    def __init__(self, business_name: str, location: Optional[str] = None, slug: Optional[str] = None, use_owner_photos: bool = False):
        self.business_name = business_name.strip()
        inferred_loc, _ = infer_location_and_phone(self.business_name, location)
        self.location = inferred_loc
        self.slug = slug or self.slugify(self.business_name)
        self.out_dir = os.path.join(DEMOS_DIR, self.slug)
        os.makedirs(self.out_dir, exist_ok=True)
        self.html_path = os.path.join(self.out_dir, "index.html")
        self.brief_path = os.path.join(self.out_dir, "DESIGN_BRIEF.md")
        self.sources_path = os.path.join(self.out_dir, "SOURCES.json")

        # Source Ledger
        self.ledger = SourceLedger()

        # Runtime State
        self.domain: str = "general_services"
        self.confidence: float = 0.5
        self.needs_confirmation: bool = False
        self.candidates: List[Dict[str, Any]] = []
        self.show_sample_services: Optional[bool] = None
        self.use_owner_photos: bool = use_owner_photos
        self.brand_data: Dict[str, Any] = {}
        self.archetype_id: int = 5
        self.design_tokens: Dict[str, Any] = {}
        self.audit_results: Dict[str, Any] = {}
        self.deployed_url: Optional[str] = None


    def stage_real_photos(self, assets: Dict[str, Any], scout_data: Dict[str, Any]) -> int:
        """Operator photo policy (Nov 2026): DEMOS get the business's real
        photos from Google/Insta with ZERO waiting — staged locally (never
        hotlinked) with provenance rows in SOURCES.json. Real photos fill the
        standard slots; curated stock only backfills slots reality didn't.
        Sources, in order: the recon harvest, then the business's dossier
        photos (dossiers/<slug>/photos)."""
        from ghost.workers.dossier import stage_photos_into_demo
        # stock dies FIRST: unfilled slots stay unfilled ([Inquire] in the
        # brief), never silently backfilled with generic imagery
        for slot in ("hero_bg", "bento_img1", "bento_img2",
                     "gallery_1", "gallery_2", "gallery_3"):
            assets.pop(slot, None)
        photos: List[Any] = []
        photos.extend(scout_data.get("photos", []) or [])
        from ghost.workers.dossier import dossiers_dir as _dossiers_dir
        dossier_dir = os.path.join(_dossiers_dir(), self.slug, "photos")
        if os.path.isdir(dossier_dir):
            for fname in sorted(os.listdir(dossier_dir)):
                if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    photos.append({"path": os.path.join(dossier_dir, fname),
                                   "source_url": "dossier_photos"})
        try:
            staged = stage_photos_into_demo(photos, self.out_dir, self.slug)
        except Exception as e:
            logger.warning("photo staging failed for %s: %s", self.slug, e)
            return 0
        for slot, rel in staged["slots"].items():
            assets[slot] = rel   # local path — no hotlink
        if not staged["staged"]:
            # PHOTO DOCTRINE (the Grasshopper lesson): stock photos wearing
            # business claims are fabricated evidence — a cat labelled
            # 'chocolate ganache' is a lie. No real photos means NO photos:
            # the build goes typography-led editorial with [Inquire for
            # quotation: photography] placeholders.
            assets["photos_policy"] = "no_photos_inquire"
            from ghost.core.audit import audit
            audit("demo.photos_needed", subject=self.slug,
                  ping="(c) unverifiable: no real photos for this demo — drop "
                       "them into the Dossiers tab (drag & drop) and rebuild")
            logger.info("no real photos for '%s' — photography-free editorial "
                        "variant (stock fallback is banned)", self.slug)
        self.brand_data = self.brand_data or {}
        self.brand_data["photos_staged"] = staged["staged"]
        return staged["staged"]

    @classmethod
    def get_show_sample_services(cls) -> bool:
        """Returns True if sample services placeholder block should be rendered when no real services are found."""
        env_val = os.environ.get("GHOST_DEMO_SHOW_SAMPLE_SERVICES", "").lower()
        if env_val in ("1", "true", "yes"):
            return True
        config_file = os.path.join(os.path.dirname(__file__), "../../../configs/demo.json")
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return bool(cfg.get("show_sample_services", False))
            except Exception:
                pass
        return False

    @staticmethod
    def slugify(text: str) -> str:
        s = re.sub(r'[^a-zA-Z0-9\s-]', '', text.strip().lower())
        return re.sub(r'[\s-]+', '-', s)

    # =========================================================================
    # Phase 1: Recon & Brand Extraction (The Scraper Node)
    # =========================================================================
    async def run_phase1_recon(self) -> Dict[str, Any]:
        """
        Executes Google Maps / Places reconnaissance and records all findings
        in the strict SourceLedger. Missing data is strictly None.
        """
        logger.info(f"Phase 1: Scouting intelligence for '{self.business_name}' in '{self.location}'")
        
        # 1. Scout via ReconScout
        scout_data = await ReconScout.scout_business(self.business_name, self.location)

        # 2. Record facts into Source Ledger
        self.ledger.add("business_name", scout_data.get("business_name") or self.business_name, source_url="google_maps")
        self.ledger.add("category", scout_data.get("category"), source_url="google_maps")
        self.ledger.add("categories", scout_data.get("categories", []), source_url="google_maps")
        self.ledger.add("rating", scout_data.get("rating"), source_url="google_maps")
        self.ledger.add("review_count", scout_data.get("review_count"), source_url="google_maps")
        self.ledger.add("phone", scout_data.get("phone"), source_url="google_maps")
        self.ledger.add("address", scout_data.get("address") or self.location, source_url="google_maps")
        self.ledger.add("website", scout_data.get("website"), source_url="google_maps")
        self.ledger.add("hours", scout_data.get("hours"), source_url="google_maps")
        self.ledger.add("reviews", scout_data.get("reviews", []), source_url="google_maps")
        self.ledger.add("photos", scout_data.get("photos", []), source_url="google_maps")
        self.ledger.save(self.sources_path)

        # 3. Benchmark prospect's existing website headlessly (saved to private audit notes)
        prospect_url = scout_data.get("website")
        audit_data = await PerformanceAuditor.audit_url(prospect_url)
        self.audit_results = audit_data or {}
        if audit_data:
            audit_notes_path = os.path.join(self.out_dir, "AUDIT_NOTES.md")
            with open(audit_notes_path, "w", encoding="utf-8") as f:
                f.write(f"# Performance Audit Notes: {self.business_name}\n\n")
                f.write(f"- **URL:** {audit_data.get('url')}\n")
                f.write(f"- **Load Time:** {audit_data.get('load_time_sec')}s\n")
                f.write(f"- **Score:** {audit_data.get('perf_score')}/100\n")
                f.write(f"- **DOM Nodes:** {audit_data.get('dom_nodes')}\n")
                f.write(f"- **Timestamp:** {audit_data.get('scraped_at')}\n")
                f.write(f"- **Method:** {audit_data.get('method')}\n")

        # 4. Fallback search query (neutral across all industries)
        category_hint = scout_data.get("category") or ""
        query = f"{self.business_name} {self.location} {category_hint} reviews hours phone services".strip()
        search_res = await WebSearchBridge.search(query, max_results=6)
        results = search_res.get("results", []) if isinstance(search_res, dict) else []
        snippets_text = " ".join([r.get("snippet", "") + " " + r.get("title", "") for r in results])

        # 5. Classify business
        domain, conf, needs_conf, candidates = classify_business(category_hint, self.business_name, snippets_text)
        self.domain = domain
        self.confidence = conf
        self.needs_confirmation = needs_conf
        self.candidates = candidates
        logger.info(f"Classified '{self.business_name}' -> domain: '{domain}' (confidence: {conf:.2f}, needs_confirmation: {needs_conf})")

        # 6. Select assets and copy system
        assets = DOMAIN_CURATED_ASSETS.get(domain, GENERAL_SERVICES_ASSETS).copy()
        copy_set = DOMAIN_COPY.get(domain, DOMAIN_COPY["general_services"])

        # Operator photo policy: stage REAL photos locally (recon harvest +
        # dossier), never hotlinks; stock backfills only unfilled slots.
        self.stage_real_photos(assets, scout_data)

        # Select archetype based on domain
        if domain in ["home_trade_services", "dental_medical", "legal_accounting", "general_services"]:
            archetype_id = 5
        elif domain in ["gyms_fitness"]:
            archetype_id = 4
        elif domain in ["salons_spas", "boutiques_retail", "real_estate", "hotels_stays"]:
            archetype_id = 2
        else:
            archetype_id = 1
        self.archetype_id = archetype_id

        # Format reviews (zero synthetic reviews)
        scraped_reviews = scout_data.get("reviews", [])
        reviews = []
        for r in scraped_reviews[:4]:
            t = r.get("text", "").strip()
            if len(t) >= 15:
                auth = r.get("author") or "Google reviewer"
                reviews.append({
                    "quote": t,
                    "author": auth,
                    "source": f"Google Review • {r.get('stars', '5.0★')}"
                })

        city_part = self.location.split(',')[0].strip()
        tagline = copy_set["tagline_template"].format(location=city_part, name=self.business_name, business_name=self.business_name)

        # Services: verify if scraped, else record as unverified concept samples
        scraped_services = scout_data.get("services", [])
        if scraped_services:
            self.ledger.add("services", scraped_services, source_url="google_maps", verified=True)
            services_list = scraped_services
            services_verified = True
        else:
            self.ledger.add(
                "services",
                None,
                source_url="google_maps",
                verified=False,
                notes="Sample Concept Architecture • Not Scraped from Listing"
            )
            services_list = []
            services_verified = False
        self.ledger.save(self.sources_path)

        # Save recon artifacts
        try:
            with open(os.path.join(self.out_dir, "SCOUT.json"), "w", encoding="utf-8") as f:
                json.dump(scout_data, f, indent=2)
            if audit_data:
                with open(os.path.join(self.out_dir, "AUDIT.json"), "w", encoding="utf-8") as f:
                    json.dump(audit_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Note saving recon JSON artifacts: {e}")

        self.brand_data = {
            "business_name": self.ledger.get("business_name") or self.business_name,
            "location": self.ledger.get("address") or self.location,
            "slug": self.slug,
            "domain": domain,
            "category": category_hint,
            "assets": assets,
            "copy": copy_set,
            "archetype_id": archetype_id,
            "archetype_name": ARCHETYPE_MAP[archetype_id]["name"],
            "tagline": tagline,
            "services": services_list,
            "services_verified": services_verified,
            "reviews": reviews,
            "phone": self.ledger.get("phone"),
            "email": None,
            "hours": self.ledger.get("hours"),
            "rating": self.ledger.get("rating"),
            "review_count": self.ledger.get("review_count"),
            "scouted_photos": scout_data.get("photos", []),
            "audit_data": audit_data,
            "needs_confirmation": needs_conf
        }

        try:
            with open(os.path.join(self.out_dir, "BRAND.json"), "w", encoding="utf-8") as f:
                json.dump(self.brand_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Note saving BRAND.json: {e}")

        logger.info(f"Phase 1 Complete: Scraped '{self.brand_data['business_name']}' ({domain}).")
        return self.brand_data

    def confirm_domain(self, domain: str, archetype_id: Optional[int] = None) -> None:
        """
        Explicitly confirms or overrides domain classification by operator.
        Clears needs_confirmation and updates brand data and assets.
        """
        if domain not in DOMAIN_COPY:
            raise ValueError(f"Unknown domain '{domain}'. Valid domains: {list(DOMAIN_COPY.keys())}")
        self.domain = domain
        self.confidence = 1.0
        self.needs_confirmation = False

        if archetype_id is not None:
            self.archetype_id = archetype_id
        elif domain in ["home_trade_services", "dental_medical", "legal_accounting", "general_services"]:
            self.archetype_id = 5
        elif domain in ["gyms_fitness"]:
            self.archetype_id = 4
        elif domain in ["salons_spas", "boutiques_retail", "real_estate", "hotels_stays"]:
            self.archetype_id = 2
        else:
            self.archetype_id = 1

        assets = DOMAIN_CURATED_ASSETS.get(domain, GENERAL_SERVICES_ASSETS).copy()
        copy_set = DOMAIN_COPY.get(domain, DOMAIN_COPY["general_services"])

        if self.brand_data:
            self.brand_data["domain"] = domain
            self.brand_data["assets"] = assets
            self.brand_data["copy"] = copy_set
            self.brand_data["archetype_id"] = self.archetype_id
            self.brand_data["archetype_name"] = ARCHETYPE_MAP[self.archetype_id]["name"]
            city_part = self.location.split(',')[0].strip()
            self.brand_data["tagline"] = copy_set["tagline_template"].format(location=city_part, name=self.brand_data.get("business_name") or self.business_name, business_name=self.brand_data.get("business_name") or self.business_name)
            if not self.brand_data.get("services_verified"):
                self.brand_data["services"] = []
            self.brand_data["needs_confirmation"] = False

    # =========================================================================
    # Phase 2: Generate DESIGN_BRIEF.md (Context Layering)
    # =========================================================================
    def _house_style_section(self) -> str:
        """The operator's editorial house style (Nov 2026 reference), loaded
        from the committed skill, appended to every DESIGN_BRIEF as a BINDING
        override — the section builder obeys it over archetype defaults."""
        try:
            from ghost.paths import repo_root
            skill = os.path.join(repo_root(), "context", "skills",
                                 "editorial_web_design", "SKILL.md")
            with open(skill, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("house style skill unreadable: %s", e)
            content = (
                "- Warm paper palette, serif display type, ONE earthy accent, "
                "micro-caps meta rows, generous whitespace, real photos only.")
        photos_note = ""
        if not any(str(v).startswith("assets/photos/") for v in (self.brand_data or {}).get("assets", {}).values()):
            photos_note = (
                "\n\n### PHOTOGRAPHY MODE: NONE AVAILABLE (binding)\n"
                "No real photos were staged. Build the PHOTOGRAPHY-FREE "
                "editorial variant: typography-led (giant ghosted wordmark, "
                "serif hierarchy, hairline rules, micro-caps meta), generous "
                "whitespace, and where a photo would go render a bordered "
                "slot reading `[Inquire for quotation: photography]`. "
                "STOCK IMAGES PRESENTED AS THIS BUSINESS ARE FORBIDDEN — a "
                "generic photo carrying a specific menu/product claim is a "
                "fabricated fact and fails QA claims lint.\n")
        return (
            "\n\n---\n\n## HOUSE STYLE OVERRIDE — binding editorial standard\n\n"
            "The section builder MUST apply the standard below over any "
            "archetype default it conflicts with. Photography rule: use ONLY "
            "the staged local paths (`assets['hero_bg']`, `bento_img1/2`, "
            "`gallery_1..3` when they point at `assets/photos/`) — they are "
            "THE BUSINESS'S OWN photos. Never hotlink stock — stock images are "
            "banned entirely. A photo slot WITHOUT a staged local path renders "
            "as `[Inquire for quotation: photography]`, never a generic image.\n"
            + photos_note +
            "\n```markdown\n" + content + "\n```\n")

    def _svg_decor_section(self) -> str:
        """Vendor svg.js into the build and bind the decorative-SVG rules:
        self-drawing line art, animated underlines, hand-drawn dividers —
        once, tasteful, reduced-motion aware, aria-hidden."""
        import shutil
        try:
            from ghost.paths import repo_root
            vendor_src = os.path.join(repo_root(), "templates", "svg", "svg.min.js")
            vendor_dir = os.path.join(self.out_dir, "assets", "vendor")
            os.makedirs(vendor_dir, exist_ok=True)
            shutil.copy(vendor_src, os.path.join(vendor_dir, "svg.min.js"))
        except Exception as e:
            logger.warning("svg.js vendoring skipped: %s", e)
            return ""
        try:
            from ghost.paths import repo_root as _rr
            with open(os.path.join(_rr(), "context", "skills", "svg_decor",
                                   "SKILL.md"), "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("svg_decor skill unreadable: %s", e)
            content = "Max two drawn elements per page; stroke-only; draw once; reduced-motion off."
        return (
            "\n\n## SVG DECOR — available and bounded\n\n"
            "`@svgdotjs/svg.js` v3.2.4 is vendored at `assets/vendor/svg.min.js` "
            "(load it with a normal `<script src>` if the design uses it). "
            "Allowed: ONE self-drawing line-art element, hand-drawn section "
            "dividers, CSS underlines. Follow the rules verbatim:\n\n"
            "```markdown\n" + content + "\n```\n")

    async def run_phase2_design_brief(self) -> str:
        """
        Synthesizes exact design tokens, hex codes, typography pairings,
        and section architecture into `demos/<slug>/DESIGN_BRIEF.md`.
        """
        if self.needs_confirmation:
            raise RuntimeError(
                f"Execution halted: Cannot generate design brief for '{self.business_name}'. "
                f"Classification confidence {self.confidence:.2f} requires operator confirmation."
            )
        logger.info(f"Phase 2: Generating DESIGN_BRIEF.md for '{self.slug}'")
        arch = ARCHETYPE_MAP[self.archetype_id]
        self.design_tokens = arch
        b = self.brand_data

        brief_content = f"""# BESPOKE DESIGN BRIEF & ARCHITECTURAL BLUEPRINT
**Establishment:** {b['business_name']}  
**Domain:** {b['domain']} ({b.get('category') or 'Service'})  
**Location / Market:** {b['location']}  
**Assigned Archetype:** Archetype {self.archetype_id} — {arch['name']}  
**Classification Confidence:** {self.confidence:.2f} (Needs confirmation: {self.needs_confirmation})

---

## 1. Design System & Semantic Color Tokens
```css
:root {{
  /* 60/30/10 Rule Hierarchy */
  --canvas: {arch['bg']};
  --surface: {arch['surface']};
  --raised: #ffffff;
  --ink: {arch['text']};
  --ink-2: {arch['muted']};
  --accent: {arch['accent']};
  --accent-secondary: {arch['accent_secondary']};
  --line: {arch['border']};
}}

:root[data-theme="dark"] {{
  --canvas: {arch['dark_bg']};
  --surface: {arch['dark_surface']};
  --raised: #2A2A2A;
  --ink: {arch['dark_text']};
  --ink-2: {arch['dark_muted']};
  --accent: {arch['dark_accent']};
  --accent-secondary: {arch['dark_accent_secondary']};
  --line: {arch['dark_border']};
}}
```

## 2. Typography Hierarchy
- **Headline Display:** `{arch['font_head']}`
- **Body & Technical Specs:** `{arch['font_body']}`
- **Monospace Accents:** `'JetBrains Mono', 'Fira Code', monospace`

## 3. Section Flow
1. Floating Pill Navigation with Light/Dark Switcher
2. Hero Viewport with Clamped Mobile Headline
3. Operational Value Banner
4. Craft & Standards Philosophy
5. Bento Grid Offerings (Source-backed)
6. Authentic Reviews Section (Omitted if 0 reviews)
7. Space & Facility Gallery
8. How It Works (Domain-tailored 3 steps)
9. Active Interactive Booking Module (Live JS WhatsApp & Call sync)
10. Conversion Footer with Removable Concept Attribution
"""
        with open(self.brief_path, "w", encoding="utf-8") as f:
            brief_content += self._house_style_section()
            brief_content += self._svg_decor_section()
            f.write(brief_content)

        logger.info(f"Phase 2 Complete: DESIGN_BRIEF.md written to {self.brief_path}")
        return brief_content

    # =========================================================================
    # Phase 3: Sectional Component Building (Anti-Lazy Modular Assembly)
    # =========================================================================
    async def run_phase3_build_sections(self) -> str:
        """
        Builds the demo section-by-section without massive single-shot shortcuts.
        Produces complete responsive HTML with noise texture, smooth scroll,
        hero, bento grid, marquee reviews, and oversized typography footer.
        """
        logger.info(f"Phase 3: Assembling sectional components for '{self.slug}'")
        arch = self.design_tokens or ARCHETYPE_MAP[self.archetype_id]
        b = self.brand_data
        c = b["copy"]
        assets = b["assets"]

        main_title, sub_title = split_business_name(b["business_name"])
        curr = infer_currency_symbol(b["location"])

        # Services section state (verified vs labelled-sample vs omitted)
        services_list = b.get("services", [])
        is_sample_services = not b.get("services_verified", False)
        show_sample = getattr(self, "show_sample_services", None)
        if show_sample is None:
            show_sample = self.get_show_sample_services()
        has_services_section = bool(services_list) if not is_sample_services else bool(show_sample)

        # Phone status + direct-contact affordances
        raw_phone = b.get("phone")
        has_phone = bool(raw_phone and raw_phone.strip())
        clean_phone_digits = re.sub(r'[^0-9]', '', raw_phone) if has_phone else ""
        is_uk_landline = bool(has_phone and re.match(r'^\+?44\s*2|^\+?44\s*1|^02|^01', raw_phone.strip()))
        initial_booking_href = f"tel:{raw_phone}" if is_uk_landline else (f"https://wa.me/{clean_phone_digits}" if has_phone else "#contact")
        initial_btn_text = f"Call Directly: {raw_phone}" if is_uk_landline else ("Instant WhatsApp Direct" if has_phone else "[Inquire Directly]")

        philosophy_body_1 = c["philosophy_body_1"].format(
            business_name=main_title, location=self.location.split(',')[0])

        # The PageContext — the narrow data contract the kit composes from.
        ctx = {
            "slug": self.slug,
            "business_name": b["business_name"],
            "main_title": main_title,
            "sub_title": sub_title,
            "tagline": b.get("tagline", ""),
            "location": b["location"],
            "city": self.location.split(',')[0],
            "rating": b.get("rating"),
            "rating_badge": (f"Serving {self.location.split(',')[0]} • {b['rating']}★ Public Acclaim"
                             if b.get('rating') else
                             f"Serving {self.location.split(',')[0]} • Professional Excellence"),
            "phone": raw_phone,
            "clean_phone_digits": clean_phone_digits,
            "has_phone": has_phone,
            "is_uk_landline": is_uk_landline,
            "initial_booking_href": initial_booking_href,
            "initial_btn_text": initial_btn_text,
            "currency_symbol": curr,
            "services": services_list,
            "has_services": bool(services_list),
            "is_sample_services": is_sample_services,
            "show_sample": bool(show_sample),
            "reviews": b.get("reviews", []),
            "hours": b.get("hours"),
            "faqs": c.get("faqs", []),
            "steps": c.get("how_it_works_steps", []),
            "booking_fields": c.get("booking_fields", "general"),
            "photos_policy": assets.get("photos_policy", ""),
            "hero_bg": assets.get("hero_bg"),
            "bento_img1": assets.get("bento_img1"),
            "bento_img2": assets.get("bento_img2"),
            "gallery_1": assets.get("gallery_1"),
            "gallery_2": assets.get("gallery_2"),
            "gallery_3": assets.get("gallery_3"),
            "copy": c,
            "arch": arch,
            "form_path": f"/api/leads/{self.slug}/contact",
            "philosophy_body_1": philosophy_body_1,
        }

        # THE SECTION KIT — compose, never hand-weave (the Webild steal, done
        # the Ghost way: pre-validated components, deterministic planner with
        # written reasons, photo doctrine enforced structurally).
        from ghost.workers.section_kit import (compose_page, ensure_vendor,
                                               plan_sections, write_section_map)
        plan = plan_sections(ctx)
        omitted = [p["name"] for p in plan if not p.get("include")]
        if omitted:
            logger.info(f"Section planner omitted: {omitted}")
        full_html, section_records = compose_page(
            plan, ctx, arch, starter_kit=get_demo_starter_kit(self.archetype_id))
        ensure_vendor(self.out_dir)
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        write_section_map(self.out_dir, section_records, ctx)
        self.section_records = section_records
        self.section_plan = plan

        logger.info(f"Phase 3 Complete: composed {len(section_records)} kit sections "
                    f"at {self.html_path} ({len(full_html)} bytes)")
        return full_html

    # =========================================================================
    # Phase 4: Visual Self-Healing (Brave CDP Headless Verification)
    # =========================================================================
    async def run_phase4_visual_verification(self) -> Dict[str, Any]:
        """
        Connects to running Brave CDP instance on port 9222.
        Opens the generated HTML in a tab, tests 390x844 (mobile) and 1440x900 (desktop),
        waits for fonts, images, and animations to settle, and captures screenshots.
        """
        logger.info(f"Phase 4: Initiating Brave CDP Visual Verification on {self.html_path}")
        file_url = f"file://{os.path.abspath(self.html_path)}"

        audit: Dict[str, Any] = {
            "viewports_tested": ["390x844 (Mobile)", "1440x900 (Desktop)"],
            "console_errors": 0,
            "layout_overflow": False,
            "screenshots": {},
            "status": "passed"
        }

        try:
            tab = await BraveCDPBridge.new_tab(file_url)
            ws_url = tab.get("webSocketDebuggerUrl") or tab.get("wsUrl")

            if not ws_url:
                tabs = await BraveCDPBridge.list_tabs()
                for t in tabs:
                    if file_url in t.get("url", ""):
                        ws_url = t.get("wsUrl")
                        tab = t
                        break

            if ws_url:
                async with websockets.connect(ws_url, close_timeout=5) as ws:
                    await CDPAutomationEngine.send_command(ws, 4001, "Page.enable", {})
                    await CDPAutomationEngine.send_command(ws, 4002, "Runtime.enable", {})

                    # Wait for fonts, images, and force animation settlement
                    settle_script = """
                    (async function() {
                        if (document.fonts && document.fonts.ready) {
                            await document.fonts.ready;
                        }
                        const imgs = Array.from(document.querySelectorAll('img'));
                        await Promise.all(imgs.map(img => {
                            if (img.complete && img.naturalWidth > 0) return Promise.resolve();
                            return new Promise(resolve => {
                                img.onload = () => resolve();
                                img.onerror = () => resolve();
                                setTimeout(resolve, 2000);
                            });
                        }));
                        const reveals = document.querySelectorAll('.reveal-el, .spring-card');
                        reveals.forEach(el => {
                            el.style.opacity = '1';
                            el.style.transform = 'none';
                            el.style.visibility = 'visible';
                        });
                        return true;
                    })()
                    """
                    await CDPAutomationEngine.evaluate_js(ws, settle_script)
                    await asyncio.sleep(1.2)

                    # Check layout overflow
                    overflow_check = """
                    (function() {
                        return {
                            overflow: document.documentElement.scrollWidth > window.innerWidth,
                            scrollWidth: document.documentElement.scrollWidth,
                            innerWidth: window.innerWidth
                        };
                    })()
                    """
                    layout_res = await CDPAutomationEngine.evaluate_js(ws, overflow_check)
                    if isinstance(layout_res, dict) and layout_res.get("overflow"):
                        logger.warning(f"Layout overflow detected: {layout_res}")
                        audit["layout_overflow"] = True

                    # 1440x900 Desktop Emulation & Capture
                    await CDPAutomationEngine.send_command(ws, 4003, "Emulation.setDeviceMetricsOverride", {
                        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False
                    })
                    await asyncio.sleep(0.5)
                    snap_desktop = await CDPAutomationEngine.send_command(ws, 4004, "Page.captureScreenshot", {"format": "jpeg", "quality": 85})
                    if snap_desktop.get("result", {}).get("data"):
                        d_path = os.path.join(self.out_dir, "desktop_1440x900.jpg")
                        with open(d_path, "wb") as f:
                            f.write(base64.b64decode(snap_desktop["result"]["data"]))
                        audit["screenshots"]["desktop"] = d_path

                    # 390x844 Mobile Emulation & Capture
                    await CDPAutomationEngine.send_command(ws, 4005, "Emulation.setDeviceMetricsOverride", {
                        "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True
                    })
                    await asyncio.sleep(0.5)
                    snap_mobile = await CDPAutomationEngine.send_command(ws, 4006, "Page.captureScreenshot", {"format": "jpeg", "quality": 85})
                    if snap_mobile.get("result", {}).get("data"):
                        m_path = os.path.join(self.out_dir, "mobile_390x844.jpg")
                        with open(m_path, "wb") as f:
                            f.write(base64.b64decode(snap_mobile["result"]["data"]))
                        audit["screenshots"]["mobile"] = m_path

                if tab.get("id"):
                    await BraveCDPBridge.close_tab(tab["id"])
            else:
                logger.info("CDP WebSocket debugger unavailable for headless tab.")

        except Exception as e:
            logger.warning(f"Brave CDP runtime probe note: {e}")
            audit["note"] = f"Visual assertion completed with local fallback: {e}"

        self.audit_results = audit

        # reports/qa.json — the composed-page QA contract. Evidence or it
        # didn't happen: no real screenshots => passed=False, mode "none",
        # status inconclusive (the deploy gate refuses on exactly this).
        shots = [p for p in (audit.get("screenshots") or {}).values()
                 if p and os.path.isfile(p)]
        overflow = bool(audit.get("layout_overflow"))
        qa = {
            "passed": bool(shots) and not overflow,
            "mode": "cdp" if shots else "none",
            "status": ("passed" if (shots and not overflow)
                       else ("inconclusive" if not shots else "failed")),
            "screenshots_captured": len(shots),
            "screenshots": shots,
            "human_review": {"screenshots": shots},
            "checks": {
                "layout_overflow": overflow,
                "viewports_tested": audit.get("viewports_tested", []),
                "note": audit.get("note", ""),
            },
            "sections": [r.get("name") for r in getattr(self, "section_records", [])],
            "builder": "demo_state_machine/section_kit",
        }
        reports_dir = os.path.join(self.out_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        with open(os.path.join(reports_dir, "qa.json"), "w", encoding="utf-8") as f:
            json.dump(qa, f, indent=2)

        logger.info(f"Phase 4 Complete: Viewports verified. Screenshots: {audit.get('screenshots')}")
        return audit

    # =========================================================================
    # Phase 5: Instant Preview & Handover (Cloud Deploy & Personal Outreach)
    # =========================================================================
    async def run_phase5_preview_handover(self) -> Dict[str, Any]:
        """
        Prepares final package:
        1. Deploys demo to Vercel via VercelDeployer (project name: {slug}-concept).
        2. If deployed, generates personalized outreach authored by the user.
        3. Enforces Persona Firewall: zero leaked persona, zero local file links.
        4. If not deployed, marks status as 'needs_deploy' and does NOT emit outreach.
        """
        logger.info(f"Phase 5: Finalizing preview & handover for '{self.slug}'")
        b = self.brand_data
        main_title, _ = split_business_name(b["business_name"])

        # Attempt Vercel cloud deployment — control plane authorizes, effect executes
        from ghost.autonomy.outbound import issue_effect_grant
        deploy_grant = issue_effect_grant("deploy", self.slug)
        deploy_res = await VercelDeployer.deploy_demo(self.slug, project_name=f"{self.slug}-concept",
                                                      grant_token=deploy_grant)
        live_url = deploy_res.get("url")
        self.deployed_url = live_url

        # Register in SQLite CRM
        try:
            crm = CRMDatabase.get_instance()
            crm.add_or_update_client(
                slug=self.slug,
                business_name=b["business_name"],
                contact_name="Decision Maker",
                email=b.get("email"),
                phone=b.get("phone"),
                preferred_channel="email",
                status="demo_built",
                deal_value=None,
                notes=f"Archetype {self.archetype_id}: {b['archetype_name']}. Bespoke concept built."
            )
        except Exception as e:
            logger.warning(f"CRM sync note: {e}")

        # If no hosted URL, stop outreach production per Rule 6
        if not live_url:
            logger.warning(f"No hosted URL available for '{self.slug}'. Stopping outreach generation per Rule 6.")
            return {
                "status": "needs_deploy",
                "slug": self.slug,
                "business_name": b["business_name"],
                "html_path": self.html_path,
                "brief_path": self.brief_path,
                "deploy_result": deploy_res,
                "message": "Demo built successfully. Hosted deployment required before generating outbound pitch kit."
            }

        # Build category-specific gap sentence from ledger facts
        if not b.get("phone"):
            gap_sentence = "customers cannot easily book or inquire for your services online from mobile devices"
        else:
            gap_sentence = "your mobile visitors don't have an instant 1-tap booking flow connected directly to your team"

        # 3-Part Outreach Copy authored as USER (Pranav)
        whatsapp_pitch = (
            f"Hi {main_title} team, noticed {gap_sentence}.\n\n"
            f"Put together an interactive mobile concept tailored for your services: {live_url}\n\n"
            f"(Note: This is an independent concept created for you, not affiliated with {main_title}.)\n\n"
            f"Open to taking a look or having this handed over?\n"
            f"— {USER_NAME}"
        )

        instagram_dm = (
            f"Hi {main_title}! Quick observation: {gap_sentence}.\n\n"
            f"Crafted a bespoke mobile concept for your brand: {live_url}\n\n"
            f"Open to having this set up for you?\n"
            f"— {USER_NAME}"
        )

        pitch_subject = f"Digital Concept Preview for {main_title}"
        pitch_body = (
            f"Hi {main_title} Team,\n\n"
            f"I recently spent time reviewing your digital presence in {self.location.split(',')[0]} and noticed {gap_sentence}.\n\n"
            f"I put together a private, interactive web concept tailored specifically for your brand:\n"
            f"{live_url}\n\n"
            f"What this concept includes:\n"
            f"- Dedicated mobile-first service booking flow connected directly to your team\n"
            f"- High-performance responsive design across all devices\n"
            f"- Editorial aesthetic tailored to your brand standards\n\n"
            f"Please note this is an independent concept created to show what is possible. If this aligns with your standards, I would be glad to hand over the files or discuss setting it live on your custom domain.\n\n"
            f"Best regards,\n"
            f"{USER_NAME}"
        )

        # Enforce Persona Firewall on all outbound text
        sanitize_outbound(whatsapp_pitch)
        sanitize_outbound(instagram_dm)
        sanitize_outbound(pitch_subject)
        sanitize_outbound(pitch_body)

        # Generate PITCH_KIT.md
        pitch_kit_path = os.path.join(self.out_dir, "PITCH_KIT.md")
        pitch_kit_content = f"""# PITCH KIT: {main_title}

## 1. Concrete Gap Identified
- **Target:** {main_title} ({self.location})
- **Primary Operational Gap:** {gap_sentence.capitalize()}.
- **Hosted Concept URL:** {live_url}

## 2. Preview Assets
- **Live Preview URL:** {live_url}
- **Mobile Screenshot:** {self.audit_results.get('screenshots', {}).get('mobile', 'mobile_390x844.jpg')}
- **Desktop Screenshot:** {self.audit_results.get('screenshots', {}).get('desktop', 'desktop_1440x900.jpg')}

## 3. Direct Outreach Scripts
### WhatsApp:
```text
{whatsapp_pitch}
```

### Instagram DM:
```text
{instagram_dm}
```

## 4. Formal Email Pitch Draft
**Subject:** {pitch_subject}

```text
{pitch_body}
```
"""
        sanitize_outbound(pitch_kit_content)
        with open(pitch_kit_path, "w", encoding="utf-8") as f:
            f.write(pitch_kit_content)

        # Generate DEPLOY.md (Clean static deployment guide)
        deploy_md_path = os.path.join(self.out_dir, "DEPLOY.md")
        deploy_content = f"""# DEPLOYMENT GUIDE: {main_title}

This document details deployment procedures for the static single-page concept for {main_title}.

## 1. Architecture
- Source Type: Clean static HTML/Tailwind
- External Dependencies: None (Zero build step required)
- Edge CDN Target: Vercel / Cloudflare Pages / Netlify

## 2. Deployment Steps
### Deploy via Vercel CLI:
```bash
npm i -g vercel
cd demos/{self.slug}
vercel --prod
```

### Deploy via Netlify Drop:
Drag and drop the `demos/{self.slug}` folder directly into Netlify Drop.
"""
        with open(deploy_md_path, "w", encoding="utf-8") as f:
            f.write(deploy_content)

        handover = {
            "status": "success",
            "slug": self.slug,
            "business_name": b["business_name"],
            "live_url": live_url,
            "html_path": self.html_path,
            "brief_path": self.brief_path,
            "deploy_path": deploy_md_path,
            "pitch_kit_path": pitch_kit_path,
            "archetype": b["archetype_name"],
            "screenshots": self.audit_results.get("screenshots", {}),
            "pitch_draft": {
                "subject": pitch_subject,
                "body": pitch_body,
                "whatsapp_pitch": whatsapp_pitch,
                "instagram_dm": instagram_dm,
                "recipient": b.get("email"),
                "phone": b.get("phone")
            }
        }

        # Signal Watchdog
        try:
            from ghost.watchdog.service import AutonomousWatchdog
            watchdog = AutonomousWatchdog.get_instance()
            await watchdog.emit_signal(
                event_type="demo_state_machine_completed",
                source="demo_state_machine",
                summary=f"Autonomous Demo completed for '{b['business_name']}' ({b['archetype_name']}).",
                payload=handover,
                urgency="normal"
            )
        except Exception as e:
            logger.warning(f"Watchdog signal note: {e}")

        return handover

    # =========================================================================
    # Master Execution Loop
    # =========================================================================
    async def execute_all_phases(self) -> Dict[str, Any]:
        """Runs all 5 stages end-to-end autonomously with fail-closed gate."""
        await self.run_phase1_recon()

        # Fail-closed gate: Pause execution if classification needs confirmation
        if self.needs_confirmation:
            confirm_path = os.path.join(self.out_dir, "CONFIRM.json")
            confirm_data = {
                "slug": self.slug,
                "name": self.business_name,
                "google_category": self.brand_data.get("category", "") if self.brand_data else "",
                "suggested_domain": self.domain,
                "candidates": getattr(self, "candidates", [])
            }
            with open(confirm_path, "w", encoding="utf-8") as f:
                json.dump(confirm_data, f, indent=2)

            # Update CRM status
            try:
                from ghost.crm.db import CRMDatabase
                crm = CRMDatabase.get_instance()
                crm.update_lead_status(self.slug, "needs_confirmation")
            except Exception:
                pass

            logger.info(f"Operator confirmation required for '{self.business_name}'. Wrote CONFIRM.json. Pausing pipeline.")
            return {
                "status": "needs_confirmation",
                "slug": self.slug,
                "business_name": self.business_name,
                "google_category": confirm_data["google_category"],
                "suggested_domain": self.domain,
                "candidates": confirm_data["candidates"],
                "confirm_path": confirm_path,
                "ledger_path": self.sources_path
            }

        await self.run_phase2_design_brief()
        await self.run_phase3_build_sections()
        await self.run_phase4_visual_verification()
        return await self.run_phase5_preview_handover()

    @classmethod
    async def resume(cls, slug: str, confirmed_domain: str) -> Dict[str, Any]:
        """
        Resumes a paused demo run from Phase 2 with the operator-confirmed domain.
        Loads state from demos/<slug>/SOURCES.json and SCOUT.json, clears CONFIRM.json,
        and completes Phases 2 through 5.
        """
        out_dir = os.path.join(DEMOS_DIR, slug)
        sources_path = os.path.join(out_dir, "SOURCES.json")
        if not os.path.exists(sources_path):
            raise FileNotFoundError(f"Cannot resume demo for '{slug}': {sources_path} does not exist.")

        from ghost.workers.source_ledger import SourceLedger
        ledger = SourceLedger.load(sources_path)
        business_name = ledger.get("business_name") or slug.replace("-", " ").title()
        location = ledger.get("address") or ""

        sm = cls(business_name=business_name, location=location, slug=slug)
        sm.ledger = ledger

        # Load cached SCOUT.json and AUDIT.json if available
        scout_path = os.path.join(out_dir, "SCOUT.json")
        scout_data = {}
        if os.path.exists(scout_path):
            try:
                with open(scout_path, "r", encoding="utf-8") as f:
                    scout_data = json.load(f)
            except Exception:
                pass

        audit_path = os.path.join(out_dir, "AUDIT.json")
        audit_data = {}
        if os.path.exists(audit_path):
            try:
                with open(audit_path, "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
            except Exception:
                pass

        # Load BRAND.json if saved, else reconstruct brand_data
        brand_path = os.path.join(out_dir, "BRAND.json")
        if os.path.exists(brand_path):
            try:
                with open(brand_path, "r", encoding="utf-8") as f:
                    sm.brand_data = json.load(f)
            except Exception:
                sm.brand_data = None

        if not sm.brand_data:
            category_hint = scout_data.get("category") or ""
            assets = DOMAIN_CURATED_ASSETS.get(confirmed_domain, GENERAL_SERVICES_ASSETS).copy()
            copy_set = DOMAIN_COPY.get(confirmed_domain, DOMAIN_COPY["general_services"])

            self.stage_real_photos(assets, scout_data)

            scraped_reviews = scout_data.get("reviews", [])
            reviews = []
            for r in scraped_reviews[:4]:
                t = r.get("text", "").strip()
                if len(t) >= 15:
                    auth = r.get("author") or "Google reviewer"
                    reviews.append({
                        "quote": t,
                        "author": auth,
                        "source": f"Google Review • {r.get('stars', '5.0★')}"
                    })

            city_part = sm.location.split(',')[0].strip()
            tagline = copy_set["tagline_template"].format(location=city_part, name=business_name, business_name=business_name)

            scraped_services = scout_data.get("services", [])
            services_verified = bool(scraped_services)
            services_list = scraped_services if services_verified else []

            sm.brand_data = {
                "business_name": sm.ledger.get("business_name") or sm.business_name,
                "location": sm.ledger.get("address") or sm.location,
                "slug": sm.slug,
                "domain": confirmed_domain,
                "category": category_hint,
                "assets": assets,
                "copy": copy_set,
                "archetype_id": 1,
                "archetype_name": ARCHETYPE_MAP[1]["name"],
                "tagline": tagline,
                "services": services_list,
                "services_verified": services_verified,
                "reviews": reviews,
                "phone": sm.ledger.get("phone"),
                "email": None,
                "hours": sm.ledger.get("hours"),
                "rating": sm.ledger.get("rating"),
                "review_count": sm.ledger.get("review_count"),
                "scouted_photos": scout_data.get("photos", []),
                "audit_data": audit_data,
                "needs_confirmation": False
            }

        # Apply confirmed domain
        sm.confirm_domain(confirmed_domain)

        # Remove CONFIRM.json
        confirm_path = os.path.join(out_dir, "CONFIRM.json")
        if os.path.exists(confirm_path):
            try:
                os.remove(confirm_path)
            except OSError:
                pass

        # Update CRM status to building
        try:
            from ghost.crm.db import CRMDatabase
            crm = CRMDatabase.get_instance()
            crm.update_lead_status(slug, "building")
        except Exception:
            pass

        # Execute remaining phases 2-5
        await sm.run_phase2_design_brief()
        await sm.run_phase3_build_sections()
        await sm.run_phase4_visual_verification()
        return await sm.run_phase5_preview_handover()
