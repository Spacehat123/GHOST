<!-- section
name: lead_capture
category: leads
summary: The leads-inbox form — visitors inquire; on local preview it POSTs to Ghost (quarantine → CRM → operator ping), on hosted domains it hands the inquiry to the business's WhatsApp/phone directly.
slots: main_title, form_path
optional_slots: none
applies_to: all
requires: always
-->
# lead_capture

The Webild steal: every Ghost site captures leads. The form carries
`data-lead-inbox` (the endpoint path) and a `company_website` honeypot. ALL
behavior lives in `_base/reveal.js` — this file is markup only, no fetch, no
JS. The `.lead-status` line always tells the visitor the truth about where
their message went.
