(() => {
  const form = document.getElementById('workshop-request');
  if (!form) return;
  const list = document.getElementById('participants');
  const total = form.elements['participants-TOTAL_FORMS'];
  const max = Number(form.dataset.maxParticipants);
  const add = document.getElementById('add-participant');
  const remove = document.getElementById('remove-participant');
  const updateDeferred = () => {
    const later = form.querySelector('[name="choose_when"][value="later"]')?.checked;
    list.querySelectorAll('[data-participant]').forEach(person => {
      const input = person.querySelector('[name$="-selections_deferred"]');
      if (input && form.querySelector('[name="choose_when"]')) input.checked = later;
      person.hidden = Boolean(later);
      const deferred = Boolean(person.querySelector('[name$="-selections_deferred"]')?.checked);
      person.querySelectorAll('input:not([type="checkbox"])').forEach(input => {input.required = !deferred;});
      person.querySelectorAll('[data-deferred-choice]').forEach(group => {
        group.hidden = deferred;
        group.querySelectorAll('input').forEach(input => {input.disabled = deferred; input.required = !deferred;});
      });
    });
  };
  list.addEventListener('change', updateDeferred);
  form.querySelectorAll('[name="choose_when"]').forEach(input => input.addEventListener('change', updateDeferred));
  const update = () => {
    const count = list.children.length;
    total.value = count;
    add.disabled = count >= max;
    remove.disabled = count <= 1;
    document.getElementById('participant-count').textContent = `${count} participant${count === 1 ? '' : 's'}`;
    updateDeferred();
  };
  add.addEventListener('click', () => {
    if (list.children.length >= max) return;
    const index = list.children.length;
    const copy = document.getElementById('empty-participant').content.cloneNode(true);
    copy.querySelectorAll('[name], [id], [for]').forEach(element => {
      ['name', 'id', 'for'].forEach(attribute => {
        if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).replaceAll('__prefix__', String(index)));
      });
    });
    copy.querySelector('[data-number]').textContent = index + 1;
    list.append(copy);
    update();
    list.lastElementChild.querySelector('input').focus();
  });
  remove.addEventListener('click', () => {
    if (list.children.length <= 1) return;
    list.lastElementChild.remove();
    update();
    add.focus();
  });
  update();
})();
