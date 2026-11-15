(() => {
  "use strict";
  function mount(root) {
    const removals = root.querySelector('[data-original-count]');
    const quantity = root.querySelector('[name="quantity"]');
    if (!removals || !quantity) return;
    const choices = [...removals.querySelectorAll('input[type="checkbox"]')];
    const update = () => {
      const count = Math.max(0, Number(removals.dataset.originalCount) - Number(quantity.value));
      removals.hidden = !count;
      removals.querySelector('[data-removal-count]').textContent = `Select ${count} participant${count === 1 ? '' : 's'} to remove.`;
      for (const choice of choices) {
        choice.disabled = !count;
        if (!count) choice.checked = false;
      }
    };
    quantity.addEventListener('input', update);
    update();
  }
  window.BookingEditFields = {mount};
  mount(document);
})();
