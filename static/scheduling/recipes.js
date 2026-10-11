(() => {
  const add = document.getElementById('add-recipe-row');
  if (!add) return;
  const total = document.getElementById('id_recipe-TOTAL_FORMS');
  const rows = document.getElementById('recipe-rows');
  const refresh = () => { add.disabled = rows.querySelectorAll('.recipe-row:not([hidden])').length >= 30; };
  rows.addEventListener('click', event => {
    const button = event.target.closest('.recipe-delete');
    if (!button) return;
    const row = button.closest('.recipe-row');
    row.querySelector('[name$="-DELETE"]').value = 'on';
    row.hidden = true;
    refresh();
    add.focus();
  });
  add.addEventListener('click', () => {
    if (add.disabled) return;
    // Reuse removed, unsaved rows without losing persisted deletion requests.
    const reusable = [...rows.querySelectorAll('.recipe-row[hidden]')].find(row => !row.querySelector('[name$="-id"]').value);
    if (reusable) {
      reusable.querySelectorAll('input,select').forEach(input => { input.value = ''; });
      reusable.hidden = false;
      reusable.querySelector('select').focus();
      refresh();
      return;
    }
    const index = Number(total.value);
    if (index >= 61) return;
    const content = document.getElementById('recipe-empty').content.cloneNode(true);
    content.querySelectorAll('[id],[name],[for]').forEach(node => {
      for (const key of ['id', 'name', 'for']) {
        if (node.hasAttribute(key)) node.setAttribute(key, node.getAttribute(key).replaceAll('__prefix__', index));
      }
    });
    rows.append(content);
    total.value = index + 1;
    window.lucide?.createIcons();
    rows.lastElementChild.querySelector('select').focus();
    refresh();
  });
  refresh();
})();
