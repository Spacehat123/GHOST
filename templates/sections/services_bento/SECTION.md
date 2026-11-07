<!-- section
name: services_bento
category: services
summary: Services bento grid — verified scraped services with prices when verified, a clearly-labelled sample layout when allowed, or omitted entirely.
slots: services, offering_kicker, offering_title
optional_slots: services
applies_to: all
requires: services or show_sample
-->
# services_bento

Three states, chosen by the planner:
1. **Verified services** (scraped, `services_verified`) — real cards, prices in
   the lead's currency, descriptions only when actually scraped.
2. **Sample layout** (`show_sample`) — generic categories, explicitly labelled
   "Example service layout (replace with your real services)", no invented
   prices.
3. **Omitted** — when unverified and samples are off. Never fabricate.
