(() => {
  const button = document.getElementById('reservation-new');
  const menu = document.getElementById('reservation-new-menu');
  if (!button || !menu) return;
  function position() {
    const rect = button.getBoundingClientRect();
    menu.style.left = `${Math.max(12, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 12))}px`;
    menu.style.top = `${Math.min(rect.bottom + 8, innerHeight - menu.offsetHeight - 12)}px`;
  }
  menu.addEventListener('toggle', event => { if (event.newState === 'open') position(); });
  window.addEventListener('resize', position);
  menu.addEventListener('click', event => {
    const action = event.target.closest('[data-calendar-new]');
    if (!action) return;
    const party = action.dataset.calendarNew === 'party';
    const choices = JSON.parse(document.getElementById(party ? 'party-create-choices' : 'reservation-create-choices').textContent);
    menu.hidePopover();
    window.BookingCalendarEditor.open({title: party ? 'Add Party' : 'Add Reservation', choices,
      onSaved: () => window.location.reload()});
  });
})();
