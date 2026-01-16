(function () {
  function patchActionsUI() {
    // Actions dropdown (top and bottom)
    const selects = document.querySelectorAll('select[name="action"]');
    selects.forEach((sel) => {
      const opts = Array.from(sel.options);

      // Django often renders an empty option as "---------"
      const placeholder = opts.find(
        (o) => o.value === "" || o.text.trim() === "---------"
      );

      if (placeholder) {
        placeholder.text = "Select an action…";
      }
    });

    // Optional: rename the "Go" button to "Apply"
    // (Django uses name="index" for the actions submit button)
    const goBtns = document.querySelectorAll('button[name="index"]');
    goBtns.forEach((b) => {
      if ((b.textContent || "").trim().toLowerCase() === "go") {
        b.textContent = "Apply";
      }
    });
  }

  window.addEventListener("load", patchActionsUI);
  document.addEventListener("DOMContentLoaded", patchActionsUI);
})();
