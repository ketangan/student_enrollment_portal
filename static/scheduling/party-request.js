(() => {
  const form = document.querySelector('[data-party-request]');
  if (!form) return;
  const defaults = {occasion: 'Custom party', offering: 'Location not selected', starts_at: 'Date and time not selected',
    group_size: 'Not set', space: 'Not selected', organizer_name: 'Not entered'};
  const update = () => Object.entries(defaults).forEach(([name, fallback]) => {
    const field = form.elements[name];
    let value = field.value.trim();
    if (field.tagName === 'SELECT' && value) value = field.selectedOptions[0].textContent;
    if (name === 'starts_at' && value) {
      // Display the studio wall-clock input without applying the browser's timezone.
      const date = new Date(value + 'Z');
      if (!Number.isNaN(date.getTime())) value = new Intl.DateTimeFormat('en-US', {
        weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'UTC'
      }).format(date);
    }
    form.querySelector(`[data-summary="${name}"]`).textContent = value || fallback;
  });
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
})();
