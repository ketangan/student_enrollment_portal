(() => {
  const button = document.getElementById('user-menu-btn');
  const panel = document.getElementById('user-menu-dropdown');
  const themes = Array.from(document.querySelectorAll('[data-dash-theme]'));
  function setOpen(open) {
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  }
  function applyTheme(name) {
    const selected = ['clean', 'midnight', 'mint'].includes(name) ? name : 'clean';
    document.body.classList.toggle('dash-midnight', selected === 'midnight');
    document.body.classList.toggle('dash-mint', selected === 'mint');
    themes.forEach(item => {
      const active = item.dataset.dashTheme === selected;
      item.setAttribute('aria-pressed', String(active));
      item.querySelector('span').textContent = active ? '\u2713' : '';
    });
  }
  try { applyTheme(localStorage.getItem('dash_theme')); } catch { applyTheme('clean'); }
  themes.forEach(item => item.addEventListener('click', () => {
    applyTheme(item.dataset.dashTheme);
    try { localStorage.setItem('dash_theme', item.dataset.dashTheme); } catch { /* Page theme remains usable. */ }
  }));
  button.addEventListener('click', () => setOpen(panel.hidden));
  document.addEventListener('click', event => {
    if (!button.contains(event.target) && !panel.contains(event.target)) setOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) {
      setOpen(false);
      button.focus();
    }
  });
})();
