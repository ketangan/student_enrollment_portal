/**
 * Admin Theme Picker
 *
 * Injects a "Theme ›" sub-menu item into Jazzmin's top-right user dropdown
 * (#jazzy-usermenu). Clicking the item expands an inline sub-panel with theme
 * buttons. Main menu stays clean — sub-panel only shown on demand.
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

  // ── Build the sub-menu DOM ───────────────────────────────────────────

  /**
   * Returns { trigger, subPanel } — two elements to inject into the dropdown.
   *
   * trigger  — a dropdown-item row: "Theme  ›"
   * subPanel — hidden div with the theme buttons; shown/hidden on trigger click
   */
  function buildThemeSubMenu(themes, current) {
    // ── Trigger row ("Theme  ›") ──────────────────────────────────────
    var trigger = document.createElement("a");
    trigger.href = "#";
    trigger.className =
      "dropdown-item d-flex justify-content-between align-items-center";
    trigger.setAttribute("aria-haspopup", "true");
    trigger.setAttribute("aria-expanded", "false");

    var triggerLabel = document.createElement("span");
    triggerLabel.textContent = "Theme";
    trigger.appendChild(triggerLabel);

    var chevron = document.createElement("i");
    chevron.className = "fas fa-chevron-right";
    chevron.style.cssText =
      "font-size:11px; opacity:0.55; transition:transform 0.15s ease;";
    trigger.appendChild(chevron);

    // ── Sub-panel (hidden by default) ─────────────────────────────────
    var subPanel = document.createElement("div");
    subPanel.style.cssText =
      "display:none; padding: 4px 16px 10px; overflow:hidden;";

    var group = document.createElement("div");
    group.className = "btn-group btn-group-sm d-flex";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Choose theme");

    themes.forEach(function (t) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "btn flex-fill " +
        (t.key === current ? "btn-primary" : "btn-outline-secondary");
      btn.dataset.theme = t.key;
      btn.title = t.description;
      btn.innerHTML = '<i class="' + t.icon + '"></i> ' + t.label;

      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation(); // keep dropdown open while request is in flight

        if (t.key === current) return;

        fetch(API_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: JSON.stringify({ theme: t.key }),
        }).then(function (resp) {
          if (resp.ok) window.location.reload();
        });
      });

      group.appendChild(btn);
    });

    subPanel.appendChild(group);

    // ── Toggle logic ─────────────────────────────────────────────────
    var expanded = false;

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation(); // prevent Bootstrap from closing the dropdown

      expanded = !expanded;
      subPanel.style.display = expanded ? "block" : "none";
      chevron.style.transform = expanded ? "rotate(90deg)" : "";
      trigger.setAttribute("aria-expanded", String(expanded));
    });

    return { trigger: trigger, subPanel: subPanel };
  }

  // ── Inject into user dropdown ────────────────────────────────────────

  function injectPicker(data) {
    var menu = document.getElementById("jazzy-usermenu");
    if (!menu) return;

    var logoutForm = menu.querySelector("#logout-form");
    if (!logoutForm) return;

    var parts = buildThemeSubMenu(data.themes, data.current);

    var divider = document.createElement("div");
    divider.className = "dropdown-divider";

    // Find the divider immediately before the logout form (if any)
    var prevDivider = logoutForm.previousElementSibling;
    if (prevDivider && prevDivider.classList.contains("dropdown-divider")) {
      // Insert: [divider] [trigger] [subPanel] before the existing pre-logout divider
      menu.insertBefore(parts.subPanel, prevDivider);
      menu.insertBefore(parts.trigger, parts.subPanel);
      menu.insertBefore(divider, parts.trigger);
    } else {
      // Fallback: insert directly before the logout form
      menu.insertBefore(parts.subPanel, logoutForm);
      menu.insertBefore(parts.trigger, parts.subPanel);
      menu.insertBefore(divider, parts.trigger);
    }
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
