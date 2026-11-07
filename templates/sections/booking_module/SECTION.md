<!-- section
name: booking_module
category: booking
summary: Interactive booking module — service/details/date fields shaped by vertical (automotive/dining/general) and a WhatsApp/tel direct button whose link updates live. Element ids are a QA/deploy-gate contract.
slots: booking_title, booking_sub, services, phone, initial_booking_href, initial_btn_text
optional_slots: services
applies_to: all
requires: always
-->
# booking_module

CONTRACT: the ids `booking-service`, `booking-detail`, `booking-date`,
`whatsapp-direct-btn`, `booking-btn-text` are grepped by the quality gate
(batch_demo_worker). Never rename them. Behavior (link updates) lives in
`_base/reveal.js`; this section is markup only.
