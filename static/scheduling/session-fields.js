(() => {
  "use strict";
  function mount(root) {
    root.querySelectorAll('[data-new-session], [data-session-preflight], [data-reservation-fields]').forEach(form => {
      if (form.dataset.sessionFieldsMounted) return;
      form.dataset.sessionFieldsMounted = "true";
      const seats = form.querySelector('input[name="capacity"], input[name="quantity"]');
      if (seats && (form.hasAttribute('data-recurring-editor') || !form.hasAttribute('data-session-preflight') && !form.hasAttribute('data-new-session'))) {
        const controls = document.createElement('div'); controls.className = 'booking-seat-stepper';
        const minus = document.createElement('button'), plus = document.createElement('button');
        minus.type = plus.type = 'button'; minus.textContent = '\u2212'; plus.textContent = '+';
        minus.setAttribute('aria-label', 'Decrease seats'); plus.setAttribute('aria-label', 'Increase seats');
        seats.before(controls); controls.append(minus, seats, plus);
        const update = () => {
          minus.disabled = seats.disabled || Number(seats.value) <= Number(seats.min || 1);
          plus.disabled = seats.disabled || Boolean(seats.max && Number(seats.value) >= Number(seats.max));
        };
        for (const [button, direction] of [[minus, -1], [plus, 1]]) button.addEventListener('click', () => {
          update();
          if (button.disabled) return;
          if (!seats.value) seats.value = seats.min || '1';
          else if (direction < 0) seats.stepDown(); else seats.stepUp();
          seats.dispatchEvent(new Event('input', {bubbles:true})); update();
        });
        form.addEventListener('input', update); form.addEventListener('change', update); form.addEventListener('seatlimit', update); update();
      }
      const mode = form.querySelector('[name="mode"]');
      if (mode) {
        const update = () => {
          for (const name of ["interval", "last_date"]) {
            const field = form.querySelector(`[name="${name}"]`);
            field.closest('.form-field').hidden = mode.value !== "repeat";
            field.required = mode.value === "repeat";
          }
        };
        mode.addEventListener('change', update); update();
      }
      const notify = form.querySelector('[data-notify-customers]');
      if (notify) notify.addEventListener('change', () => {
        const field = form.querySelector('[name="notify_customers"]');
        field.value = notify.checked ? "1" : "0";
        field.dispatchEvent(new Event('input', {bubbles:true}));
      });
    });
  }
  window.SessionFields = {mount};
  mount(document);
})();
