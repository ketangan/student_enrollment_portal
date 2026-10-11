(() => {
  "use strict";
  const token = window.location.hash.slice(1);
  // Fragments never reach HTTP access logs; discard before showing private content.
  window.history.replaceState(null, "", window.location.pathname);
  if (/^[A-Za-z0-9_-]{43}$/.test(token)) {
    document.getElementById("access-token").value = token;
    document.getElementById("open-schedule").disabled = false;
    const help = document.getElementById("access-help");
    help.textContent = help.dataset.ready || "Your studio has shared private schedule access with you.";
  }
})();
