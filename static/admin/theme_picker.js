/**
 * Admin Theme Picker
 *
 * Injects a theme selector into Jazzmin's top-right user dropdown (#jazzy-usermenu).
 * Fetches available themes from /admin/api/theme/ and saves selection via POST.
 *
 * Zero coupling to specific theme keys — everything is driven by the API
 * response, so adding a new theme in Python is all you need to do.
 */
(function () {
  "use strict";

  var API_URL = "/admin/api/theme/";

  // ── Helpers ──────────────────────────────────────────────────────────

  function getCookie(name) {
    var value = "; " + document.cookie;
    var parts = value.split("; " + name + "=");
    if (parts.length === 2) return parts.pop().split(";").shift();
    return "";
  }

  // ── Build the picker DOM ─────────────────────────────────────────────

  function buildPicker(themes, current) {
    // Container div that sits inside the dropdown
    var wrapper = document.createElement("div");
    wrapper.className = "theme-picker-section";
    wrapper.style.cssText = "padding: 8px 16px 4px;";

    var label = document.createElement("span");
    label.className = "dropdown-header";
    label.style.cssText = "padding: 0 0 6px; display: block;";
    label.textContent = "Theme";
    wrapper.appendChild(label);

    var group = document.createElement("div");
    group.className = "btn-group btn-group-sm d-flex";
    group.setAttribute("role", "group");

    themes.forEach(function (t) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "btn flex-fill " +
        (t.key === current ? "btn-primary" : "btn-outline-secondary");
      btn.dataset.theme = t.key;
      btn.title = t.description;
      btn.innerHTML = "<i class=\"" + t.icon + "\"></i> " + t.label;
      group.appendChild(btn);
    });

    wrapper.appendChild(group);
    return wrapper;
  }

  // ── Inject into user dropdown ────────────────────────────────────────

  function injectPicker(data) {
    // Jazzmin renders the user dropdown as #jazzy-usermenu
    var menu = document.getElementById("jazzy-usermenu");
    if (!menu) return;

    // Insert a divider + theme picker before the last divider (above "See Profile")
    // Structure: Account | divider | Change password | divider | Log out | divider | See Profile
    // We want to inject before the logout form.
    var logoutForm = menu.querySelector("#logout-form");
    if (!logoutForm) return;

    var divider = document.createElement("div");
    divider.className = "dropdown-divider";

    var picker = buildPicker(data.themes, data.current);

    // Insert divider + picker before the logout form's preceding divider
    // Find the divider just before the logout form
    var prevDivider = logoutForm.previousElementSibling;
    if (prevDivider && prevDivider.classList.contains("dropdown-divider")) {
      menu.insertBefore(picker, prevDivider);
      menu.insertBefore(divider, picker);
    } else {
      // Fallback: just insert before the logout form
      menu.insertBefore(divider, logoutForm);
      menu.insertBefore(picker, logoutForm);
    }

    // Handle clicks
    picker.querySelectorAll("[data-theme]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();

        var theme = btn.dataset.theme;
        if (theme === data.current) return;

        fetch(API_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: JSON.stringify({ theme: theme }),
        }).then(function (resp) {
          if (resp.ok) window.location.reload();
        });
      });
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    fetch(API_URL)
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (data) {
        if (data && data.themes) injectPicker(data);
      })
      .catch(function () {
        // Silently ignore — theme picker is a nice-to-have
      });
  });
})();
