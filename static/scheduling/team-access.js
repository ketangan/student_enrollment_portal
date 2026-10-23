(() => {
  const pickers = document.querySelectorAll('.team-location-picker');
  pickers.forEach(picker => {
    const all = picker.querySelector('[value="all"]');
    const locations = [...picker.querySelectorAll('[name="locations"]')];
    const summary = picker.querySelector('summary');
    const menu = picker.querySelector('.team-location-menu');
    picker.addEventListener('change', event => {
      if (event.target === all && all.checked) locations.forEach(input => { input.checked = false; });
      else if (locations.includes(event.target) && event.target.checked) all.checked = false;
      const selected = locations.filter(input => input.checked);
      picker.querySelector('[data-location-label]').textContent = all.checked ? 'All locations'
        : selected.length === 1 ? selected[0].dataset.name : selected.length + ' locations';
    });
    picker.addEventListener('toggle', () => {
      if (!picker.open) return;
      pickers.forEach(other => { if (other !== picker) other.open = false; });
      const rect = summary.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 238)) + 'px';
      menu.style.top = Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8)) + 'px';
    });
  });
  document.addEventListener('click', event => {
    pickers.forEach(picker => { if (!picker.contains(event.target)) picker.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') pickers.forEach(picker => {
      if (picker.open) { picker.open = false; picker.querySelector('summary').focus(); }
    });
  });
})();
