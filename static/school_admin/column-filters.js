(() => {
  const close = () => {
    document.querySelectorAll('.col-filter-panel.open').forEach(panel => panel.classList.remove('open'));
    document.querySelectorAll('[data-column-menu]').forEach(button => button.setAttribute('aria-expanded', 'false'));
  };
  window.colPanelToggle = (id, event, anchor = event.currentTarget) => {
    event.stopPropagation();
    const panel = document.getElementById(id);
    const open = panel.classList.contains('open');
    close();
    if (open) return;
    panel.classList.add('open');
    const rect = anchor.getBoundingClientRect();
    panel.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - panel.offsetHeight - 12)}px`;
    panel.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 12))}px`;
    anchor.setAttribute('aria-expanded', 'true');
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-column-menu]');
    if (button) window.colPanelToggle(button.dataset.columnMenu, event, button);
    else if (!event.target.closest('.col-filter-panel, .flatpickr-calendar')) close();
  });
  document.querySelectorAll('[data-column-menu]').forEach(button => {
    const panel = document.getElementById(button.dataset.columnMenu);
    if ([...panel.querySelectorAll('input')].some(input => input.value)) button.classList.add('column-menu-active');
    if (panel.querySelector('.errorlist')) window.colPanelToggle(panel.id, {stopPropagation() {}}, button);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      const active = document.querySelector('[data-column-menu][aria-expanded="true"]');
      close();
      active?.focus();
    }
  });
  window.addEventListener('resize', close);
  window.addEventListener('scroll', event => { if (!event.target.closest?.('.col-filter-panel')) close(); }, true);
})();
