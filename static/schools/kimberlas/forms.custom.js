// Kimberlas per-school JS override (Phase 9.3 test)
(function () {
  // Add a small "Powered by" footer note (demo proof)
  document.addEventListener("DOMContentLoaded", function () {
    const container = document.querySelector(".container");
    if (!container) return;

    // Avoid duplicates on hot reload
    if (document.getElementById("sep-poweredby")) return;

    const note = document.createElement("div");
    note.id = "sep-poweredby";
    note.style.marginTop = "14px";
    note.style.fontSize = "12px";
    note.style.color = "var(--muted)";
    note.style.textAlign = "right";
    note.textContent = "Powered by Student Enrollment Portal";

    container.appendChild(note);
  });
})();
