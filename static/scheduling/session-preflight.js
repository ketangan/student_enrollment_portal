/* Availability previews never write. The normal submit repeats the same checks. */
(() => {
  "use strict";
  function mount(form, url) {
    if (!form.matches("[data-session-preflight]") || form.dataset.preflightMounted) return;
    form.dataset.preflightMounted = "true";
    const message = form.querySelector("[data-session-conflict]");
    const submit = form.querySelector('button[type="submit"]');
    const review = form.querySelector('[data-session-live-review]');
    const reviewToken = form.querySelector('[name="review_token"], [name="preview_token"]');
    const customers = form.querySelector('.session-customers');
    const notification = form.querySelector('.session-notify');
    function updateNotification() {
      if (notification) notification.hidden = !Boolean(review?.querySelector('.session-change-highlight') &&
        review.querySelector('.session-customer-list, .recurring-recipients'));
    }
    updateNotification();
    const fingerprint = () => JSON.stringify(Array.from(new FormData(form).entries()).filter(([name]) => !['review_token', 'preview_token'].includes(name)));
    let timer, revision = 0, lastValues = fingerprint();
    function block(text) {
      form.dataset.preflightBlocked = "true";
      submit.disabled = true;
      message.hidden = false;
      message.textContent = text;
    }
    async function check(version) {
      try {
        const response = await fetch(url, {method: "POST", body: new FormData(form),
          credentials: "same-origin", headers: {"X-Session-Preflight": "1"}});
        const result = await response.json();
        if (version !== revision || !form.isConnected) return;
        if (response.ok && result.valid === true) {
          if (review) {
            if (typeof result.review_html !== 'string' || !result.review_token) throw new Error('Missing review');
            review.innerHTML = result.review_html;
            reviewToken.value = result.review_token;
            if (customers) customers.hidden = Boolean(result.review_html);
            updateNotification();
          }
          form.dataset.preflightBlocked = "false";
          submit.disabled = false;
          message.hidden = true;
          message.textContent = "";
        } else {
          if (review && typeof result.review_html === 'string') review.innerHTML = result.review_html;
          updateNotification();
          const errors = Object.values(result.errors || {}).flat().map(error => error.message);
          block(errors.join(" ") || (result.review_html ? "Resolve the listed conflicts before saving." : "Could not check availability. Change the time to try again."));
        }
      } catch (_) {
        if (version === revision && form.isConnected) block("Could not check availability. Change the time to try again.");
      }
    }
    function changed() {
      const values = fingerprint();
      // The change event on blur must not disable Save after input was checked.
      if (values === lastValues) return;
      lastValues = values;
      if (reviewToken) reviewToken.value = '';
      if (review) review.replaceChildren();
      updateNotification();
      if (customers) customers.hidden = false;
      clearTimeout(timer);
      const version = ++revision;
      block("Checking availability...");
      timer = setTimeout(() => check(version), 350);
    }
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);
    form.addEventListener("submit", event => {
      if (form.dataset.preflightBlocked === "true") event.preventDefault();
    });
  }
  window.SessionPreflight = {mount};
  document.querySelectorAll("[data-session-preflight]").forEach(form => mount(form, form.action));
})();
