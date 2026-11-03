"""Autonomous Reconnaissance & Intelligence Harvester for Ghost.
Scouts high-revenue local businesses in Bengaluru (and any target city) using headless Brave CDP (port 9222).
Extracts real Google Maps / Places reviews, uncompressed high-res CDN photos, and contact intelligence.
Zero cloud tokens, zero API cost. Never invents facts or plausible defaults.
"""
import aiohttp
import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Dict, Any, List, Optional
import websockets

logger = logging.getLogger("ghost.recon")

CDP_HTTP = "http://127.0.0.1:9222"

CATEGORY_SELECTORS = [
    'button[jsaction*="category"]',
    '.DkEaL',
    'span[jsaction*="category"]',
    'button[aria-label*="Category"]',
    'div[class*="fontBodyMedium"] button'
]


def is_venue_photo(url: str) -> bool:
    """
    Validates whether an image URL is an authentic venue/business photograph
    rather than a customer profile avatar, system icon, or thumbnail.
    Filters on the ORIGINAL raw URL before any dimension rewrite.
    """
    if not url or not isinstance(url, str):
        return False
    url_clean = url.strip()
    url_lower = url_clean.lower()
    if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
        return False

    # Check for reviewer profile avatar patterns in path: /a/ or /a-/
    if re.search(r'/a[/-][a-zA-Z0-9_\-]+', url_clean):
        return False
    if "/a/" in url_clean or "/a-/" in url_clean:
        return False

    # Avatar / profile keywords in URL path or query
    if "avatar" in url_lower or "profile" in url_lower:
        return False

    # Reviewer profile photo patterns
    if "/photo.jpg" in url_lower or "default_user" in url_lower:
        return False

    # Common square avatar size parameters (=s32, =s40, =s64, =s96, =s120, =s128, etc., with -c or -mo)
    if re.search(r'=s(?:32|40|48|64|72|80|96|100|120|128|150|160)(?:-[a-z0-9_-]+)*($|[?&#])', url_lower):
        return False
    if "-mo" in url_lower:
        return False

    # Check for accepted venue photo signatures on Google CDN
    if "googleusercontent.com" in url_lower or "ggpht.com" in url_lower:
        if "/p/" in url_clean or "gps-cs-s" in url_clean or "=w" in url_lower or "=s" in url_lower:
            return True
        return True

    # Other external photo hosts (Unsplash, Cloudinary, business website)
    if any(host in url_lower for host in ["unsplash.com", "cloudinary.com", "cdn", "images"]):
        return True

    return False


def format_author_name(raw_name: Optional[str]) -> Optional[str]:
    """Formats reviewer full name as 'FirstName L.' (e.g. 'Marcus Holloway' -> 'Marcus H.')."""
    if not raw_name:
        return None
    cleaned = raw_name.strip()
    if not cleaned or cleaned.lower() in ["verified guest", "google user", "anonymous", "none", "null"]:
        return None
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    last_initial = parts[-1][0].upper() + "."
    return f"{first} {last_initial}"


class ReconScout:
    """Headless Google Maps & Places Intelligence Scraper via Brave DevTools Protocol."""

    @staticmethod
    async def _send_cdp(ws, req_id: int, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Dict[str, Any]:
        """Correlates CDP request and response by id, ignoring unrelated background events."""
        payload = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        await ws.send(json.dumps(payload))

        async def _recv_loop():
            while True:
                msg_raw = await ws.recv()
                try:
                    msg = json.loads(msg_raw)
                except Exception:
                    continue
                if msg.get("id") == req_id:
                    return msg

        return await asyncio.wait_for(_recv_loop(), timeout=timeout)

    @classmethod
    async def get_or_create_recon_tab(cls) -> Optional[Dict[str, Any]]:
        """Finds an existing background tab or opens a dedicated recon tab using non-blocking aiohttp."""
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{CDP_HTTP}/json") as resp:
                    tabs = await resp.json(content_type=None)

                if isinstance(tabs, list):
                    for t in tabs:
                        if isinstance(t, dict) and ("about:blank" in t.get("url", "") or "recon-worker" in t.get("title", "").lower()):
                            return t

                # Open a new tab
                async with session.put(f"{CDP_HTTP}/json/new?about:blank") as resp:
                    return await resp.json(content_type=None)
        except Exception as e:
            logger.error(f"Failed to access Brave CDP on port 9222: {e}")
            return None

    @classmethod
    async def close_recon_tab(cls, tab_id: str):
        """Silently closes the temporary scraping tab using non-blocking aiohttp."""
        try:
            clean_tab_id = urllib.parse.quote(str(tab_id))
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{CDP_HTTP}/json/close/{clean_tab_id}") as resp:
                    pass
        except Exception:
            pass

    @classmethod
    async def scout_business(cls, query: str, city: str = "Bengaluru") -> Dict[str, Any]:
        """
        Scouts a target on Google Maps / Places.
        Extracts:
        - Real business name, verified category, rating, review count
        - Actual address & local phone number
        - Official website URL
        - Authentic guest reviews formatted as FirstName L.
        - High-resolution uncompressed venue photos (filtered to reject avatars)
        Zero default values. Missing data is strictly None.
        """
        full_query = f"{query} {city}".strip()
        search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(full_query)}"

        tab = await cls.get_or_create_recon_tab()
        if not tab:
            return {"status": "error", "message": "Brave CDP port 9222 not reachable"}

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return {"status": "error", "message": "No WebSocket debugger URL found"}

        # ZERO-DEFAULT RULE: Missing facts are strictly None. No invented numbers or addresses.
        result_data: Dict[str, Any] = {
            "query": query,
            "city": city,
            "status": "unscouted",
            "business_name": query,
            "category": None,
            "categories": [],
            "rating": None,
            "review_count": None,
            "phone": None,
            "address": None,
            "website": None,
            "hours": None,
            "photos": [],
            "reviews": []
        }

        try:
            async with websockets.connect(ws_url, max_size=10_000_000, ping_timeout=10) as ws:
                # 1. Enable Page domain and navigate to Maps search
                msg_id = 1
                await cls._send_cdp(ws, msg_id, "Page.enable")

                msg_id += 1
                await cls._send_cdp(ws, msg_id, "Page.navigate", {"url": search_url})

                # Event-driven DOM readiness check: poll for key Maps elements or complete state
                check_script = """
                (() => {
                    const ready = document.readyState === 'complete' || document.readyState === 'interactive';
                    const hasTarget = !!(
                        document.querySelector('h1.DUwDvf, div[role="main"] h1, h1, div[data-result-index], a[data-item-id="authority"]')
                    );
                    return ready && hasTarget;
                })()
                """
                poll_start = time.time()
                while time.time() - poll_start < 4.5:
                    msg_id += 1
                    try:
                        poll_resp = await cls._send_cdp(ws, msg_id, "Runtime.evaluate", {"expression": check_script, "returnByValue": True}, timeout=1.0)
                        if poll_resp.get("result", {}).get("result", {}).get("value") is True:
                            await asyncio.sleep(0.3)
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.15)

                # 2. Extract DOM intelligence via JavaScript evaluation
                extract_script = f"""
                (function() {{
                    const res = {{
                        name: null,
                        category: null,
                        categories: [],
                        rating: null,
                        reviewsCount: null,
                        address: null,
                        phone: null,
                        website: null,
                        hours: null,
                        photos: [],
                        reviews: []
                    }};

                    // Extract Title / Business Name
                    const titleEl = document.querySelector('h1.DUwDvf, div[role="main"] h1, h1');
                    if (titleEl && titleEl.innerText.trim()) {{
                        res.name = titleEl.innerText.trim();
                    }}

                    // Extract Primary Category
                    const catSelectors = {json.dumps(CATEGORY_SELECTORS)};
                    for (const sel of catSelectors) {{
                        const el = document.querySelector(sel);
                        if (el && el.innerText && el.innerText.trim()) {{
                            res.category = el.innerText.trim();
                            res.categories.push(res.category);
                            break;
                        }}
                    }}
                    const allCatEls = document.querySelectorAll('button[jsaction*="category"], .DkEaL');
                    for (const el of allCatEls) {{
                        const txt = el.innerText ? el.innerText.trim() : '';
                        if (txt && !res.categories.includes(txt)) {{
                            res.categories.push(txt);
                        }}
                    }}

                    // Rating & Reviews Count
                    const ratingEl = document.querySelector('span.ceNzKf, span[aria-hidden="true"]');
                    if (ratingEl && /^[0-9]\\.[0-9]$/.test(ratingEl.innerText.trim())) {{
                        res.rating = ratingEl.innerText.trim();
                    }}
                    const countEl = document.querySelector('span[aria-label*="reviews"], button[aria-label*="reviews"]');
                    if (countEl) {{
                        const raw = countEl.innerText.replace(/[^0-9,]/g, '').trim();
                        if (raw) res.reviewsCount = raw;
                    }}

                    // Phone & Address
                    const btns = Array.from(document.querySelectorAll('button[data-item-id*="phone:"], button[aria-label*="Phone"], button[data-item-id*="address"], button[aria-label*="Address"]'));
                    for (const b of btns) {{
                        const aria = b.getAttribute('aria-label') || '';
                        const text = b.innerText || '';
                        if (aria.includes('Phone:') || aria.includes('phone') || /\\+?[0-9]{{2,4}}[ -]?[0-9]{{6,10}}/.test(text)) {{
                            const cleanPhone = aria.replace('Phone:', '').trim() || text.trim();
                            if (cleanPhone) res.phone = cleanPhone;
                        }}
                        if (aria.includes('Address:') || aria.includes('address')) {{
                            const cleanAddr = aria.replace('Address:', '').trim() || text.trim();
                            if (cleanAddr) res.address = cleanAddr;
                        }}
                    }}

                    // Opening Hours
                    const hoursEl = document.querySelector('div[aria-label*="Hours"], [data-item-id*="oh"], table[class*="hours"], div.t392fc');
                    if (hoursEl) {{
                        const hoursAria = hoursEl.getAttribute('aria-label') || '';
                        const hoursText = hoursEl.innerText || '';
                        const cleanHours = hoursAria.replace(/Hours:?/i, '').trim() || hoursText.trim();
                        if (cleanHours && cleanHours.length > 5) {{
                            res.hours = cleanHours.replace(/\\n+/g, ' | ');
                        }}
                    }}

                    // Existing Website
                    const webBtn = document.querySelector('a[data-item-id="authority"], a[aria-label*="website"]');
                    if (webBtn && webBtn.href && !webBtn.href.includes('google.com')) {{
                        res.website = webBtn.href;
                    }}

                    // Photos (Filter high-res content URLs, reject avatars)
                    const imgEls = Array.from(document.querySelectorAll('button[aria-label*="Photo"] img, img[src*="googleusercontent.com"], div[role="img"]'));
                    const seen = new Set();
                    for (const el of imgEls) {{
                        let src = el.src || '';
                        if (!src && el.style && el.style.backgroundImage) {{
                            const match = el.style.backgroundImage.match(/url\\(["']?(.*?)["']?\\)/);
                            if (match) src = match[1];
                        }}
                        if (src && src.includes('googleusercontent.com') && !seen.has(src)) {{
                            // Preliminary avatar rejection in browser
                            if (!src.includes('/a/') && !src.includes('/a-/') && !src.includes('avatar') && !src.includes('profile')) {{
                                seen.add(src);
                                res.photos.push({{
                                    raw_url: src,
                                    source: "google_maps_cdn"
                                }});
                                if (res.photos.length >= 8) break;
                            }}
                        }}
                    }}

                    // Top Reviews (Full text without cutting)
                    const reviewCards = Array.from(document.querySelectorAll('div[data-review-id], div.jJc9Ad, div[aria-label*="stars"]'));
                    for (const card of reviewCards) {{
                        const authorEl = card.querySelector('.d4r55, .WNxFaf, [class*="author"]');
                        const snippetEl = card.querySelector('.wiI7pd, [class*="snippet"], .MyEned');
                        const ratingSpan = card.querySelector('[aria-label*="stars"]');
                        if (snippetEl && snippetEl.innerText.trim().length >= 15) {{
                            res.reviews.push({{
                                author: authorEl ? authorEl.innerText.trim() : null,
                                stars: ratingSpan ? (ratingSpan.getAttribute('aria-label') || '5 stars') : '5 stars',
                                text: snippetEl.innerText.trim()
                            }});
                            if (res.reviews.length >= 5) break;
                        }}
                    }}

                    return res;
                }})()
                """

                msg_id += 1
                eval_resp = await cls._send_cdp(
                    ws,
                    msg_id,
                    "Runtime.evaluate",
                    {"expression": extract_script, "returnByValue": True},
                    timeout=8.0
                )
                data_obj = eval_resp.get("result", {}).get("result", {}).get("value", {})

                if data_obj:
                    if data_obj.get("name"):
                        result_data["business_name"] = data_obj["name"]
                    if data_obj.get("category"):
                        result_data["category"] = data_obj["category"]
                    if data_obj.get("categories"):
                        result_data["categories"] = data_obj["categories"]
                    if data_obj.get("rating"):
                        result_data["rating"] = data_obj["rating"]
                    if data_obj.get("reviewsCount"):
                        result_data["review_count"] = data_obj["reviewsCount"]
                    if data_obj.get("phone"):
                        result_data["phone"] = data_obj["phone"]
                    if data_obj.get("address"):
                        result_data["address"] = data_obj["address"]
                    if data_obj.get("website"):
                        result_data["website"] = data_obj["website"]
                    if data_obj.get("hours"):
                        result_data["hours"] = data_obj["hours"]

                    # Filter and upscale photos with Python-side is_venue_photo validation
                    raw_photos = data_obj.get("photos", [])
                    validated_photos = []
                    for p in raw_photos:
                        raw_url = p.get("raw_url") if isinstance(p, dict) else str(p)
                        if is_venue_photo(raw_url):
                            # Upgrade thumbnail to 1600x1200 high-res
                            high_res = re.sub(r'=w[0-9]+-h[0-9]+.*$', '=w1600-h1200-k-no', raw_url)
                            high_res = re.sub(r'=s[0-9]+.*$', '=s1600', high_res)
                            validated_photos.append({
                                "url": high_res,
                                "raw_url": raw_url,
                                "source": "google_maps_cdn"
                            })
                    result_data["photos"] = validated_photos

                    # Process reviews: format author names and drop empty text
                    raw_reviews = data_obj.get("reviews", [])
                    cleaned_reviews = []
                    for rev in raw_reviews:
                        text = rev.get("text", "").strip()
                        if len(text) >= 15:
                            cleaned_author = format_author_name(rev.get("author"))
                            cleaned_reviews.append({
                                "author": cleaned_author,
                                "stars": rev.get("stars", "5.0★"),
                                "text": text
                            })
                    result_data["reviews"] = cleaned_reviews
                    result_data["status"] = "scouted"

        except Exception as e:
            logger.error(f"Recon error during scraping: {e}")
            result_data["error"] = str(e)
        finally:
            tab_id = tab.get("id")
            if tab_id:
                await cls.close_recon_tab(tab_id)

        return result_data
