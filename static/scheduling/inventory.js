(() => {
  document.addEventListener('click', event => {
    const button = event.target.closest('#add-material');
    if (!button) return;
    const count = document.getElementById('id_materials-TOTAL_FORMS');
    const index = Number(count.value);
    if (index >= 50) return;
    const fragment = document.getElementById('material-template').content.cloneNode(true);
    fragment.querySelectorAll('[name],[id],[for]').forEach(el => {
      for (const attr of ['name', 'id', 'for']) {
        if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replaceAll('__prefix__', index));
      }
    });
    document.getElementById('material-rows').append(fragment);
    document.dispatchEvent(new Event('party-material-added'));
    count.value = index + 1;
    button.disabled = index + 1 >= 50;
  });
})();
