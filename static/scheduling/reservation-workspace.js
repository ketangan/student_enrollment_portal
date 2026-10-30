(() => {
  const workspace = document.querySelector('[data-reservation-workspace]');
  if (!workspace) return;
  document.addEventListener('click', event => {
    document.querySelectorAll('.reservation-action-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.reservation-action-menu[open]').forEach(menu => {
      menu.open = false;
      menu.querySelector('summary').focus();
    });
  });
  const tabs = [...document.querySelectorAll('.settings-tabs .settings-tab')];
  const names = ['details', 'preparation'];
  const show = (name, url, push = true) => {
    workspace.querySelectorAll('[data-reservation-panel]').forEach(panel => {
      panel.hidden = panel.dataset.reservationPanel !== name;
    });
    tabs.forEach((tab, index) => {
      const active = names[index] === name;
      tab.classList.toggle('active', active);
      if (active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
    if (push) history.pushState({reservationTab: name}, '', url);
  };
  tabs.forEach((tab, index) => tab.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    show(names[index], tab.href);
  }));
  window.addEventListener('popstate', () => {
    const index = tabs.findIndex(tab => {
      const target = new URL(tab.href);
      return target.pathname === location.pathname && target.searchParams.get('tab') === new URL(location.href).searchParams.get('tab');
    });
    if (index >= 0) show(names[index], location.href, false);
  });
  let busy = false;
  const updateParticipants = async (url, options = {}, kind = '') => {
    if (busy) return;
    busy = true;
    const selector = kind === 'notes' ? 'section[aria-labelledby="internal-notes"]' : kind === 'guests' ? 'section[aria-labelledby="party-guests"]' : kind === 'payment' ? 'section[aria-labelledby="money-title"]' : 'section[aria-labelledby="participants-title"]';
    const section = workspace.querySelector(selector);
    section.setAttribute('aria-busy', 'true');
    const y = window.scrollY;
    const rowId = document.activeElement?.closest('tr')?.id;
    try {
      const response = await fetch(url, {credentials: 'same-origin', ...options});
      if (!response.ok && response.status !== 400) throw new Error();
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const replacement = page.querySelector(selector);
      if (!replacement) throw new Error();
      section.replaceWith(replacement);
      // Preparation quantities and readiness may have changed with these selections.
      const prep = kind !== 'notes' && page.querySelector('[data-reservation-panel="preparation"]');
      if (prep) {
        const current = workspace.querySelector('[data-reservation-panel="preparation"]');
        current.innerHTML = prep.innerHTML;
      }
      for (const selector of (kind === 'notes' ? [] : ['.reservation-heading', '.reservation-progress'])) {
        const fresh = page.querySelector(selector);
        const current = document.querySelector(selector);
        if (fresh && current) current.replaceWith(fresh);
      }
      const audit = document.getElementById('audit-title')?.closest('details');
      const freshAudit = page.getElementById('audit-title')?.closest('details');
      if (audit && freshAudit) {
        freshAudit.open = audit.open;
        audit.replaceWith(freshAudit);
      }
      window.lucide?.createIcons();
      if (document.dispatchEvent) document.dispatchEvent(new Event('booking:content-updated'));
      const focusRow = rowId ? document.getElementById(rowId) : replacement;
      focusRow?.querySelector('textarea, select, input:not([type="hidden"]), .participant-actions a')?.focus({preventScroll: true});
      window.scrollTo({top: y, behavior: 'instant'});
    } catch (_) {
      let error = section.querySelector('[data-inline-error]');
      if (!error) {
        error = document.createElement('p');
        error.dataset.inlineError = '';
        error.setAttribute('role', 'alert');
        section.append(error);
      }
      error.textContent = kind === 'notes' ? 'Could not refresh notes. Your text is still here. Check the saved notes before trying again.' : 'Could not refresh selections. Check the reservation before trying again.';
    } finally {
      section.removeAttribute('aria-busy');
      busy = false;
    }
  };
  workspace.addEventListener('click', event => {
    const switchLink = event.target.closest('[data-reservation-switch]');
    if (switchLink) {
      event.preventDefault();
      show(switchLink.dataset.reservationSwitch, switchLink.href);
      document.getElementById('participants-title')?.scrollIntoView({block: 'nearest'});
      return;
    }
    const link = event.target.closest('[data-inline-section], .participant-actions a');
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    updateParticipants(link.href, {}, link.dataset.inlineSection);
  });
  workspace.addEventListener('submit', event => {
    const form = event.target;
    if (['request_payment', 'void_terminal'].includes(form.elements.action?.value || event.submitter?.value)) form.setAttribute('action', tabs[0].href);
    const kind = {save_selections: '', save_guest: 'guests', correct_terminal: 'payment', save_notes: 'notes'}[form.elements.action?.value];
    if (kind === undefined) return;
    event.preventDefault();
    updateParticipants(form.getAttribute('action') || location.href, {method: 'POST', body: new FormData(form)}, kind);
  });
})();
