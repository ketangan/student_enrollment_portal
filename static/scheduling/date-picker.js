(() => {
  if (!window.flatpickr) return;
  const button = (label, action, className = '') => {
    const node = document.createElement('button');
    node.type = 'button'; node.textContent = label; node.className = className;
    node.addEventListener('click', action);
    return node;
  };
  const place = picker => {
    if (picker.config?.static) return;
    requestAnimationFrame(() => {
      if (!picker.isOpen) return;
      const popup = picker.calendarContainer, rect = popup.getBoundingClientRect();
      popup.style.top = `${window.scrollY + Math.max(8, Math.min(rect.top, window.innerHeight - rect.height - 8))}px`;
      popup.style.left = `${window.scrollX + Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8))}px`;
      popup.style.right = 'auto';
    });
  };
  document.querySelectorAll('input[type="datetime-local"],input[type="date"]').forEach(input => {
    if (input.disabled || input.readOnly) return;
    const withTime = input.type === 'datetime-local';
    const sync = (_, __, picker) => {
      const pm = picker.amPM?.textContent === 'PM';
      picker.calendarContainer.querySelectorAll('[data-period]').forEach(node => {
        node.setAttribute('aria-pressed', String((node.dataset.period === 'PM') === pm));
      });
    };
    window.flatpickr(input, {
      altInput: true, altFormat: withTime ? 'm/d/Y h:i K' : 'm/d/Y',
      dateFormat: withTime ? 'Y-m-d\\TH:i' : 'Y-m-d',
      enableTime: withTime, time_24hr: false, minuteIncrement: 1,
      disableMobile: true, monthSelectorType: 'static',
      static: Boolean(input.closest('dialog')),
      minDate: input.min || undefined, maxDate: input.max || undefined,
      onDayCreate: (_, __, picker, day) => {
        if ([0, 6].includes(day.dateObj.getDay())) day.classList.add('weekend');
      },
      onReady: (_, __, picker) => {
        const popup = picker.calendarContainer;
        popup.classList.add('booking-calendar');
        popup.setAttribute('role', 'dialog');
        popup.setAttribute('aria-label', withTime ? 'Choose date and time' : 'Choose date');
        const originalId = input.id;
        if (originalId) { input.id = `${originalId}__value`; picker.altInput.id = originalId; }
        for (const attr of ['aria-label', 'aria-describedby', 'aria-invalid']) {
          if (input.hasAttribute(attr)) picker.altInput.setAttribute(attr, input.getAttribute(attr));
        }
        picker.altInput.placeholder = '';
        picker.altInput.setAttribute('aria-haspopup', 'dialog');
        for (const direction of ['prev', 'next']) {
          const nav = popup.querySelector(`.flatpickr-${direction}-month`);
          nav.setAttribute('role', 'button'); nav.tabIndex = 0;
          nav.setAttribute('aria-label', direction === 'prev' ? 'Previous month' : 'Next month');
          nav.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); nav.click(); }
          });
        }
        window.addEventListener('resize', () => place(picker));
        if (withTime) {
          picker.hourElement.tabIndex = picker.minuteElement.tabIndex = 0;
          const periods = document.createElement('div'); periods.className = 'calendar-periods';
          for (const period of ['AM', 'PM']) {
            const choice = button(period, () => {
              const selected = new Date(picker.selectedDates[0] || new Date());
              selected.setHours(Number(picker.hourElement.value) % 12 + (period === 'PM' ? 12 : 0), Number(picker.minuteElement.value), 0, 0);
              picker.setDate(selected, true);
            });
            choice.dataset.period = period; periods.append(choice);
          }
          picker.timeContainer.append(periods);
        }
        const footer = document.createElement('div'); footer.className = 'calendar-footer';
        footer.append(button('Today', () => {
          const today = new Date();
          if (withTime && picker.selectedDates[0]) today.setHours(picker.selectedDates[0].getHours(), picker.selectedDates[0].getMinutes());
          if (picker.isEnabled(today, !withTime)) picker.setDate(today, true);
        }), button('Clear', () => picker.clear()), button('Close', () => picker.close()));
        popup.append(footer);
        sync(_, __, picker);
      },
      onOpen: (_, __, picker) => { picker.altInput.setAttribute('aria-expanded', 'true'); sync(_, __, picker); place(picker); },
      onClose: (_, __, picker) => picker.altInput.setAttribute('aria-expanded', 'false'),
      onChange: sync, onValueUpdate: sync,
    });
  });
})();
