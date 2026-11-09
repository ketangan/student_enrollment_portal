/* Reuse the existing protected forms and their preview/confirmation endpoints. */
(() => {
  "use strict";
  let active;
  function close() {
    active.container.remove();
    active.link.setAttribute("aria-expanded", "false");
    active.link.focus();
    active = null;
  }
  function message(text) {
    active.status.textContent = text;
  }
  async function load(method, body) {
    const editor = active;
    editor.busy = true;
    editor.panel.setAttribute("aria-busy", "true");
    editor.panel.querySelectorAll("button").forEach(button => {button.disabled = true;});
    message(method === "POST" ? "Saving..." : "Loading...");
    try {
      const response = await fetch(editor.url, {method, body, credentials: "same-origin"});
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const content = doc.querySelector("[data-inline-editor]");
      // A successful writer redirects to its saved record; login redirects are not success.
      if (method === "POST" && response.ok && response.redirected && doc.querySelector("[data-inline-workspace]")) {
        window.location.reload();
        return;
      }
      if (!content || (!response.ok && response.status !== 400)) throw new Error("Unavailable editor");
      editor.content.replaceChildren(content);
      const form = content.querySelector("form");
      form.action = editor.url;
      form.querySelectorAll(".actions a").forEach(link => {
        link.addEventListener("click", event => {event.preventDefault(); if (!editor.busy) close();});
      });
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (editor.busy || editor.uncertain) return;
        const data = new FormData(form);
        if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
        load("POST", data);
      });
      message(response.ok ? "" : "Please correct the highlighted fields.");
      const focus = content.querySelector(".errorlist") || content.querySelector("h2");
      focus.tabIndex = -1;
      focus.focus();
      if (window.lucide) window.lucide.createIcons();
    } catch {
      editor.uncertain = method === "POST";
      message(method === "POST" ? "Could not confirm the save. Refresh this page before trying again." : "Could not open the editor. Refresh this page and try again.");
    } finally {
      editor.busy = false;
      editor.panel.removeAttribute("aria-busy");
      editor.panel.querySelectorAll("button").forEach(button => {button.disabled = Boolean(editor.uncertain && button.type === "submit");});
    }
  }
  document.querySelectorAll("[data-inline-edit]").forEach(link => {
    link.setAttribute("aria-expanded", "false");
    link.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (active) {
        if (active.busy) return;
        if (active.link === link) {close(); return;}
        // Keep unfinished edits visible rather than silently throwing them away.
        active.panel.focus();
        return;
      }
      const panel = document.createElement("section");
      panel.className = "inline-editor";
      panel.tabIndex = -1;
      panel.setAttribute("aria-label", link.getAttribute("aria-label") || link.textContent.trim());
      const status = document.createElement("p");
      status.setAttribute("role", "status");
      const content = document.createElement("div");
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "button inline-editor-close";
      cancel.setAttribute("aria-label", "Close editor");
      cancel.title = "Close editor";
      const closeIcon = document.createElement("i");
      closeIcon.setAttribute("data-lucide", "x");
      closeIcon.setAttribute("aria-hidden", "true");
      cancel.append(closeIcon);
      cancel.addEventListener("click", () => {if (!active.busy) close();});
      panel.append(cancel, status, content);
      const target = link.getAttribute("data-inline-target");
      const body = target ? document.querySelector(target) : null;
      const row = link.closest("tr") || body?.querySelector("tr");
      const table = (body || link).closest(".table-wrap, .dash-table-scroll");
      let container = panel;
      if ((row || body) && table && table.scrollWidth > table.clientWidth) {
        table.after(panel);
      } else if (row || body) {
        container = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = body ? body.closest("table").querySelector("thead tr").children.length : row.children.length;
        cell.append(panel);
        container.append(cell);
        if (body) body.prepend(container);
        else row.after(container);
      } else {
        (link.closest(".section-heading") || link.closest(".actions") || link).after(panel);
      }
      active = {link, url: link.href, container, panel, content, status, busy: false};
      link.setAttribute("aria-expanded", "true");
      panel.addEventListener("keydown", event => {
        if (event.key === "Escape" && !active.busy) {event.preventDefault(); close();}
      });
      if (window.lucide) window.lucide.createIcons();
      load("GET");
    });
  });
})();
