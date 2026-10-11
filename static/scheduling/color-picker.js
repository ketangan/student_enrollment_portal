(() => {
  document.querySelectorAll('.color-picker').forEach(control => {
    const picker = control.querySelector('input[type="color"]');
    const hex = control.querySelector('input[type="text"]');
    const sync = () => {
      if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) picker.value = hex.value;
    };
    picker.hidden = false;
    picker.addEventListener('input', () => {
      hex.value = picker.value.toUpperCase();
      hex.dispatchEvent(new Event('input', {bubbles: true}));
    });
    hex.addEventListener('input', sync);
    sync();
  });
})();
