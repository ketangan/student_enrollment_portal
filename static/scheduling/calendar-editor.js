/* Calendar dialogs render the same protected forms used by the list workspace. */
(() => {
  "use strict";
  const dialog = document.getElementById("calendar-editor");
  if (!dialog) return;
  const content = document.getElementById("calendar-editor-body");
  const status = document.getElementById("calendar-editor-status");
  const closeButton = document.getElementById("calendar-editor-close");
  let busy = false, uncertain = false, sequence = 0, saved;
  const disabledBefore = new WeakMap();
  function close() {if (!busy) dialog.close();}
  closeButton.addEventListener("click", close);
  dialog.addEventListener("cancel", event => {if (busy) event.preventDefault();});
  dialog.addEventListener("close", () => {sequence++;});
  function controls() {
    closeButton.disabled = busy;
    content.querySelectorAll("button").forEach(button => {
      if (busy || uncertain) {
        if (!disabledBefore.has(button)) disabledBefore.set(button, Boolean(button.disabled));
        button.disabled = true;
      } else if (disabledBefore.has(button)) {
        button.disabled = disabledBefore.get(button); disabledBefore.delete(button);
      }
    });
    dialog.setAttribute("aria-busy", String(busy));
  }
  async function load(url, body, initialStart) {
    const current = ++sequence;
    busy = true;
    status.textContent = body ? "Saving..." : "Loading...";
    controls();
    try {
      const response = await fetch(url, {method: body ? "POST" : "GET", body, credentials: "same-origin",
        headers: {"X-Calendar-Editor": "1"}});
      if (current !== sequence || !dialog.open) return;
      if (response.headers.get("Content-Type")?.includes("application/json")) {
        const result = await response.json();
        if (response.ok && result.saved === true) {
          busy = false;
          controls();
          dialog.close();
          saved(result);
          return;
        }
        if (response.status === 400 && result.error) {
          status.textContent = result.error;
          return;
        }
        throw new Error("Unconfirmed response");
      }
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const fragment = doc.querySelector("[data-calendar-editor]");
      if (!fragment || response.redirected || (!response.ok && response.status !== 400)) throw new Error("Unavailable editor");
      content.replaceChildren(fragment);
      // Keep one accessible title in the dialog, rather than duplicate page headings.
      const heading = fragment.querySelector("h2");
      if (heading) {document.getElementById("calendar-editor-title").textContent = heading.textContent; heading.remove();}
      window.BookingEditFields?.mount(fragment);
      window.PontoraPartyForm?.mount();
      const sessionField = fragment.querySelector('[data-calendar-session-url]');
      if (sessionField) {
        const select = sessionField.querySelector('select');
        select.addEventListener('change', () => {
          if (select.value && !busy) load(`${sessionField.dataset.calendarSessionUrl}?session=${encodeURIComponent(select.value)}`);
        });
      }
      fragment.querySelectorAll("[data-calendar-load]").forEach(link => link.addEventListener("click", event => {
        event.preventDefault(); if (!busy) load(link.href);
      }));
      if (initialStart) {
        const start = fragment.querySelector('[name="starts_at"]');
        if (start && !start.value) start.value = initialStart;
      }
      for (const form of fragment.querySelectorAll("form")) {
        const action = form.getAttribute("action") || url;
        form.addEventListener("submit", event => {
          event.preventDefault();
          if (busy || uncertain) return;
          const data = new FormData(form);
          if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
          load(action, data);
        });
        form.querySelectorAll(".actions a").forEach(link => {
          if (link.getAttribute("data-calendar-load") === null) link.addEventListener("click", event => {event.preventDefault(); close();});
        });
        form.querySelectorAll("[data-calendar-close]").forEach(button => button.addEventListener("click", close));
      }
      status.textContent = fragment.querySelector("[data-booking-review]") ? "Review the revised price before confirming."
        : response.ok ? "" : "Please correct the highlighted fields.";
      const focus = fragment.querySelector(".errorlist") || fragment.querySelector("input:not([type=hidden]), select, textarea");
      if (focus) {focus.tabIndex = focus.tabIndex < 0 ? -1 : focus.tabIndex; focus.focus();}
      if (window.lucide) window.lucide.createIcons();
    } catch (_) {
      if (current !== sequence || !dialog.open) return;
      uncertain = Boolean(body);
      status.textContent = body ? "Could not confirm the save. Refresh the calendar before trying again."
        : "Could not open this form. Close it and try again.";
    } finally {
      if (current === sequence) {busy = false; controls();}
    }
  }
  window.BookingCalendarEditor = {
    open({url, title, choices, initialStart, onSaved}) {
      if (busy) return;
      saved = onSaved;
      uncertain = false;
      content.replaceChildren();
      controls();
      status.textContent = "";
      document.getElementById("calendar-editor-title").textContent = title;
      dialog.showModal();
      if (url) {load(url); return;}
      if (initialStart) {
        const time = document.createElement("p"); time.className = "calendar-editor-time";
        time.textContent = new Intl.DateTimeFormat(undefined, {dateStyle:"medium", timeStyle:"short", timeZone:"UTC"})
          .format(new Date(`${initialStart}:00Z`));
        content.append(time);
      }
      const label = document.createElement("label"), select = document.createElement("select");
      label.className = "calendar-workshop-field";
      label.textContent = "Workshop or party";
      select.setAttribute("aria-label", "Workshop or party");
      const placeholder = document.createElement("option");
      placeholder.textContent = "Choose a workshop or party"; placeholder.value = "";
      select.append(placeholder);
      for (const item of choices) {
        const option = document.createElement("option");
        option.textContent = item.title; option.value = item.createUrl; select.append(option);
      }
      select.addEventListener("change", () => {
        if (select.value) {
          const target = select.value + (initialStart ? `${select.value.includes('?') ? '&' : '?'}starts_at=${encodeURIComponent(initialStart)}` : '');
          load(target, undefined, initialStart);
        }
      });
      label.append(select); content.append(label); select.focus();
      if (!choices.length) status.textContent = "No workshops are available at this location.";
    }
  };
})();
