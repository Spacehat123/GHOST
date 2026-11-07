<!-- section
name: hours_location
category: nap
summary: Visit & Contact NAP block — name, address, hours, direct telephone. Byte-consistent with the LocalBusiness schema (NAP consistency is an SEO move).
slots: main_title, location, hours, phone
optional_slots: hours, phone
applies_to: all
requires: location
-->
# hours_location

NAP (Name / Address / Phone) block. The values must match the LocalBusiness
JSON-LD byte-for-byte — this section renders from the same facts the SEO pack
schemas are built from. Missing hours/phone lines simply don't render.
