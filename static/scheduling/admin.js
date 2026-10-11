(() => {
  window.lucide?.createIcons();
  const nav = document.getElementById('booking-nav');
  const toggle = document.getElementById('booking-nav-toggle');
  const account = document.getElementById('booking-account');
  const theme = document.getElementById('booking-theme');
  const locationFilter = document.getElementById('dashboard-location-filter');
  locationFilter?.addEventListener('change', () => locationFilter.requestSubmit());
  const setNav = open => {
    if (!nav || !toggle) return;
    nav.classList.toggle('dash-nav--mobile-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  toggle?.addEventListener('click', () => {
    setNav(toggle.getAttribute('aria-expanded') !== 'true');
    if (account) account.open = false;
  });
  document.addEventListener('click', event => {
    if (!nav?.contains(event.target) && !toggle?.contains(event.target)) setNav(false);
    if (!account?.contains(event.target) && account) account.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (account?.open) {
      account.open = false;
      account.querySelector('summary').focus();
    }
    if (toggle?.getAttribute('aria-expanded') === 'true') {
      setNav(false);
      toggle.focus();
    }
  });
  window.matchMedia('(max-width: 900px)').addEventListener('change', () => setNav(false));
  const applyTheme = name => {
    const selected = ['clean', 'midnight', 'mint'].includes(name) ? name : 'clean';
    document.body.classList.toggle('dash-midnight', selected === 'midnight');
    document.body.classList.toggle('dash-mint', selected === 'mint');
    if (theme) theme.value = selected;
  };
  // Share the existing enrollment preference, without requiring browser storage.
  try { applyTheme(localStorage.getItem('dash_theme')); } catch { applyTheme('clean'); }
  theme?.addEventListener('change', () => {
    applyTheme(theme.value);
    try { localStorage.setItem('dash_theme', theme.value); } catch { /* Theme still works for this page. */ }
  });
})();
