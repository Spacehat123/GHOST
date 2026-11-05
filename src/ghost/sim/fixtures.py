"""SIM — the 50-lead fixture pack (T6): deterministic, seeded, honest.

The simulated day runs 50 fixture leads through the whole pipeline with zero
operator input. Same seed → same leads, byte-identical. Fixtures are shaped
exactly like the lead dicts the real sources produce (crm_leads columns).
"""
import random
from typing import Any, Dict, List

NAMES = ["Spice Symphony", "The Copper Kettle", "Obsidian Dining", "Zenith Lounge",
         "Heritage Table", "Ember & Oak", "The Palm Court", "Saffron House",
         "Marble Room", "Velvet Fork", "Blue Ginger", "Stone Mason's",
         "The Gilded Spoon", "Lotus Terrace", "Iron Chef House", "Amber Room",
         "The Quiet Quill", "Fern & Flame", "Crystal Bay", "Monsoon Deck",
         "The Ruby Kitchen", "Cedar Smoke", "Pearl Diver", "The Long Table",
         "Golden Hour", "Salt & Sky", "The Velvet Vine", "Basalt Bar",
         "Orchid Lane", "The Tin Roof", "Jade Mirror", "Second Course",
         "The Open Hearth", "Cinnabar", "Windmill House", "The Grove",
         "Night Jasmine", "Copper Leaf", "The White Peacock", "Driftwood",
         "Saffron Steps", "The Marbled Hall", "Ember Lane", "Tide & Table",
         "The Silent Chef", "Cardamom Club", "Silver Sands", "The Oak Barrel",
         "Heron Point", "The Last Course"]
CITIES = ["Bengaluru", "Bengaluru", "Bengaluru", "Bengaluru", "Mysuru"]  # weighted
CATEGORIES = ["fine_dining", "rooftop_restaurant", "specialty_coffee", "boutique_cafe",
              "luxury_lounge", "farm_to_table"]
STREETS = ["Church Street", "Indiranagar 100ft Rd", "Koramangala 5th Block",
           "UB City", "Lavelle Road", "Residency Road", "Whitefield Main Rd"]


def fixture_leads(n: int = 50, seed: int = 20261106) -> List[Dict[str, Any]]:
    """n deterministic leads. Same seed → identical list, forever. The seed
    also selects WHICH businesses (roster shuffle) — different seeds are
    genuinely different towns."""
    rng = random.Random(seed)
    roster = list(NAMES)
    rng.shuffle(roster)
    leads: List[Dict[str, Any]] = []
    for i, name in enumerate(roster[:n]):
        slug = name.lower().replace("&", "and").replace("'", "")
        slug = "-".join(slug.split())
        has_site = rng.random() < 0.25          # ~25% already have websites
        rating = round(rng.uniform(3.9, 4.9), 1)
        leads.append({
            "id": f"lead_{seed}_{i:03d}",
            "slug": slug,
            "name": name,
            "address": f"{rng.randint(1, 120)} {rng.choice(STREETS)}, {rng.choice(CITIES)}",
            "phone": f"+91{rng.randint(7000000000, 9999999999)}",
            "website": f"https://{slug}.example.com" if has_site else "",
            "rating": rating,
            "review_count": rng.randint(40, 900),
            "google_category": rng.choice(CATEGORIES),
            "entity_type": "restaurant",
            "score_revenue": round(rng.uniform(0.4, 1.0), 2),
            "score_fit": round(rng.uniform(0.3, 1.0), 2),
            "score_friction": round(rng.uniform(0.2, 0.9), 2),
            "gaps_json": "{}",
            "status": "scouted",
            "needs_confirmation": 0,
            "demo_url": "",
        })
    return leads


def fixture_replies(seed: int = 20261106) -> List[Dict[str, Any]]:
    """Deterministic inbound replies spanning ALL the classifier's categories
    (the sim day's inbox — includes a distress case, which must freeze)."""
    return [
        {"from": "Spice Symphony", "text": "This looks great, send me the details."},
        {"from": "The Copper Kettle", "text": "How much does it cost?"},
        {"from": "Obsidian Dining", "text": "Automatic reply: out of office until Monday"},
        {"from": "Zenith Lounge", "text": "550 5.1.1 user unknown"},
        {"from": "Heritage Table", "text": "That is too expensive for us."},
        {"from": "Ember & Oak", "text": "Not right now, maybe next quarter."},
        {"from": "The Palm Court", "text": "Never contact me again, you spammers."},
    ]
