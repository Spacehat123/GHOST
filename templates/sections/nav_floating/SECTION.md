<!-- section
name: nav_floating
category: navigation
summary: Fixed floating pill nav with the business wordmark, section links (plan-aware), theme toggle and the primary CTA.
slots: main_title, cta_primary, present_ids
optional_slots: sub_title
applies_to: all
requires: always
-->
# nav_floating

The floating glass nav. Links render conditionally from `present_ids` — the
planner decides which sections exist, the nav never points at a missing id.
Carries the `theme-toggle` button (behavior in `_base/reveal.js`).
