(() => {
  const form = document.querySelector('[data-package-form]');
  if (!form) return;
  const offerings = JSON.parse(document.getElementById('package-offerings').textContent);
  const rooms = JSON.parse(document.getElementById('package-rooms').textContent);
  const fields = form.elements;
  const number = name => {
    const value = fields[name].value.trim();
    return value && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
  };
  const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(value);
  const show = (key, value) => { form.querySelector(`[data-package-summary="${key}"]`).textContent = value; };
  const filterRooms = () => {
    const location = offerings[fields.offering.value]?.location;
    for (const option of fields.room.options) {
      option.hidden = option.disabled = Boolean(option.value && rooms[option.value]?.location !== location);
    }
    if (fields.room.selectedOptions[0]?.disabled) fields.room.value = '';
  };
  const update = () => {
    const count = number('participant_limit');
    const extra = number('additional_participant_limit') || 0;
    const extraPrice = number('additional_participant_price') || 0;
    const price = number('total');
    const duration = number('duration_minutes');
    const rate = number('tax_rate') ?? Number(offerings[fields.offering.value]?.tax_rate || 0);
    const tax = rate ? ' + tax' : '';
    show('name', fields.name.value.trim() || 'New package');
    show('offering', fields.offering.value ? fields.offering.selectedOptions[0].textContent : 'Workshop not selected');
    show('duration', duration ? `${Math.floor(duration / 60) ? `${Math.floor(duration / 60)} hr ` : ''}${duration % 60 ? `${duration % 60} min` : ''}`.trim() : 'Duration not set');
    show('price', price === null ? 'Price not set' : money(price) + tax);
    show('participants', count || 'Not set');
    show('observers', number('included_observers') || 0);
    show('extras', extra ? `${extra} at ${money(extraPrice)} each` : 'None');
    show('deposit', number('deposit') ? money(number('deposit')) : 'None');
    show('maximum', count && price !== null ? `${count + extra} · ${money(price + extra * extraPrice)}${tax}` : 'Not set');
  };
  form.addEventListener('input', update);
  form.addEventListener('change', event => { if (event.target === fields.offering) filterRooms(); update(); });
  filterRooms();
  update();
})();
