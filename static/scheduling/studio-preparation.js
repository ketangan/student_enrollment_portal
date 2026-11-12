(() => {
  document.addEventListener('change', event => {
    if (!event.target.matches('[data-hide-prepared]')) return;
    const checked = event.target.checked;
    document.querySelector('.studio-prep').classList.toggle('hide-prepared', checked);
    document.querySelectorAll('[data-hide-prepared]').forEach(input => { input.checked = checked; });
    const url = new URL(location.href);
    if (checked) url.searchParams.set('hide_prepared', '1');
    else url.searchParams.delete('hide_prepared');
    history.replaceState(null, '', url);
    document.querySelectorAll('.prep-print').forEach(link => {
      const printUrl = new URL(link.href);
      if (checked) printUrl.searchParams.set('hide_prepared', '1');
      else printUrl.searchParams.delete('hide_prepared');
      link.href = printUrl.href;
    });
  });
  let saving = false;
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('.prep-action')) return;
    event.preventDefault();
    if (saving) return;
    const error = document.getElementById('prep-error');
    error.hidden = true;
    const button = event.submitter || form.querySelector('button[type="submit"]');
    const data = new FormData(form);
    const row = form.closest('.prep-person') || form.closest('.prep-booking');
    const anchor = row.id;
    const offset = row.getBoundingClientRect().top;
    saving = true;
    button.disabled = true;
    try {
      const response = await fetch(form.getAttribute('action'), {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to save. Reload and try again.');
      const url = new URL(location.href);
      url.searchParams.delete('edit');
      url.searchParams.delete('staff');
      url.searchParams.delete('note');
      url.hash = '';
      const refresh = await fetch(url);
      if (!refresh.ok) throw new Error('Saved, but the page could not refresh. Please reload.');
      const page = new DOMParser().parseFromString(await refresh.text(), 'text/html');
      const content = page.querySelector('.studio-prep');
      if (!content) throw new Error('Saved. Sign in again to reload the preparation page.');
      document.querySelector('.studio-prep').replaceWith(content);
      history.replaceState(null, '', url);
      window.lucide?.createIcons();
      const replacement = document.getElementById(anchor);
      if (replacement && replacement.getClientRects().length) {
        window.scrollBy(0, replacement.getBoundingClientRect().top - offset);
        replacement.querySelector('button:not(:disabled)')?.focus({preventScroll: true});
      }
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      error.scrollIntoView({block: 'nearest'});
    } finally {
      saving = false;
      button.disabled = false;
    }
  });
})();
