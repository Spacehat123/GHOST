// _base/reveal.js — the ONE behavior layer. Sections are markup+styles only;
// everything interactive lives here (theme, booking, lead form, decor).
(function () {
  // ---- Theme Switcher -----------------------------------------------------
  var toggleBtn = document.getElementById('theme-toggle');
  var sunIcon = document.getElementById('theme-icon-sun');
  var moonIcon = document.getElementById('theme-icon-moon');

  function applyTheme(isDark) {
    if (isDark) {
      document.documentElement.classList.add('dark');
      if (sunIcon) { sunIcon.classList.remove('hidden'); sunIcon.classList.add('block'); }
      if (moonIcon) { moonIcon.classList.remove('block'); moonIcon.classList.add('hidden'); }
    } else {
      document.documentElement.classList.remove('dark');
      if (sunIcon) { sunIcon.classList.remove('block'); sunIcon.classList.add('hidden'); }
      if (moonIcon) { moonIcon.classList.remove('hidden'); moonIcon.classList.add('block'); }
    }
  }

  var savedTheme = null;
  try { savedTheme = localStorage.getItem('theme-pref'); } catch (e) { /* private mode */ }
  applyTheme(savedTheme === 'dark');

  if (toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      var isDark = document.documentElement.classList.toggle('dark');
      try { localStorage.setItem('theme-pref', isDark ? 'dark' : 'light'); } catch (e) { /* ignore */ }
      applyTheme(isDark);
    });
  }

  // ---- Interactive Booking Module (ids are a QA/deploy-gate contract) -----
  var BUSINESS = {{ business | tojson }};

  function initBookingModule() {
    var serviceEl = document.getElementById('booking-service');
    var detailEl = document.getElementById('booking-detail');
    var dateEl = document.getElementById('booking-date');
    var bookBtn = document.getElementById('whatsapp-direct-btn');
    var btnText = document.getElementById('booking-btn-text');
    if (!bookBtn) return;

    function updateBookingLink() {
      if (!BUSINESS.hasPhone) {
        bookBtn.href = '#contact';
        if (btnText) btnText.innerText = 'Contact Directly';
        return;
      }
      var s = serviceEl ? serviceEl.value : '';
      var d = detailEl ? detailEl.value : '';
      var t = dateEl ? dateEl.value : '';
      if (BUSINESS.isLandline) {
        bookBtn.href = 'tel:' + BUSINESS.phone;
        if (btnText) btnText.innerText = 'Call Directly: ' + BUSINESS.phone;
        return;
      }
      var msg = 'Hello ' + BUSINESS.name + ', I would like to inquire about booking:';
      if (s) msg += '\n- Service: ' + s;
      if (d) msg += '\n- Details: ' + d;
      if (t) msg += '\n- Preferred Date: ' + t;
      bookBtn.href = 'https://wa.me/' + BUSINESS.cleanPhone + '?text=' + encodeURIComponent(msg);
    }

    if (serviceEl) serviceEl.addEventListener('change', updateBookingLink);
    if (detailEl) detailEl.addEventListener('input', updateBookingLink);
    if (dateEl) dateEl.addEventListener('change', updateBookingLink);
    updateBookingLink();
  }
  initBookingModule();

  // ---- Lead capture form (the leads inbox) ---------------------------------
  // Same-origin (local preview): POST to the site's lead endpoint, show honest status.
  // Hosted (Vercel): the builder cannot receive public webhooks — fall back to the
  // business's own WhatsApp/phone, which is where the reply would come from anyway.
  function initLeadForm() {
    var form = document.querySelector('form[data-lead-inbox]');
    if (!form) return;
    var statusEl = form.querySelector('.lead-status');
    function say(msg) { if (statusEl) statusEl.textContent = msg; }

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (form.querySelector('input[name="company_website"]') &&
          form.querySelector('input[name="company_website"]').value) {
        return; // honeypot: silently swallowed
      }
      var name = (form.querySelector('input[name="name"]') || {}).value || '';
      var contact = (form.querySelector('input[name="contact"]') || {}).value || '';
      var message = (form.querySelector('textarea[name="message"]') || {}).value || '';
      var onLocal = location.hostname === '127.0.0.1' || location.hostname === 'localhost';

      if (!onLocal) {
        // Hosted fallback: hand the inquiry straight to the business.
        if (BUSINESS.hasPhone && !BUSINESS.isLandline) {
          var msg = 'Hello ' + BUSINESS.name + ', I found your website. ';
          if (message) msg += message + ' ';
          msg += '— ' + (name || 'a visitor') + (contact ? ' (' + contact + ')' : '');
          window.open('https://wa.me/' + BUSINESS.cleanPhone + '?text=' + encodeURIComponent(msg), '_blank', 'noopener');
          say('Opening WhatsApp — your message is ready to send.');
          return;
        }
        say('Please reach us directly' + (BUSINESS.phone ? ': ' + BUSINESS.phone : '.') );
        return;
      }

      var endpoint = form.getAttribute('data-lead-inbox');
      fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ name: name, contact: contact, message: message }).toString()
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (res.ok) { say('Thank you — your inquiry was recorded. ' + (BUSINESS.name || 'The team') + ' will get back to you.'); form.reset(); }
          else { say(res.j && res.j.detail ? String(res.j.detail) : 'Could not record the inquiry — please call us directly.'); }
        })
        .catch(function () { say('Could not record the inquiry — please call us directly.'); });
    });
  }
  initLeadForm();

  // ---- Entrance motion (anime.js) -----------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    if (typeof anime === 'undefined') return;
    var reduce = false;
    try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* ignore */ }
    if (reduce) return;
    anime.timeline({ easing: 'easeOutExpo' })
      .add({ targets: '.reveal-el', translateY: [35, 0], opacity: [0, 1], delay: anime.stagger(140), duration: 1000 })
      .add({ targets: '.spring-card', translateY: [25, 0], opacity: [0, 1], delay: anime.stagger(90), duration: 900 }, '-=600');
  });

  // ---- The ONE svg.js decor: hand-drawn divider, drawn once on view -------
  // The static stroke is IN the markup (page is complete with JS disabled);
  // svg.js only performs the draw-in when motion is allowed.
  function initKitDivider() {
    var holder = document.getElementById('kit-divider-svg');
    var raw = holder ? holder.querySelector('path') : null;
    if (!raw || typeof SVG === 'undefined') return;
    var reduce = false;
    try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* ignore */ }
    if (reduce || !('IntersectionObserver' in window)) return; // static stroke is the no-motion default
    var path = SVG(raw);
    var len = path.length();
    path.attr('stroke-dasharray', len + ' ' + len).attr('stroke-dashoffset', len);
    var seen = false;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting && !seen) {
          seen = true;
          path.animate(1100, 0, 'now').ease('-').attr('stroke-dashoffset', 0);
          io.disconnect();
        }
      });
    }, { threshold: 0.4 });
    io.observe(holder);
  }
  initKitDivider();
})();
