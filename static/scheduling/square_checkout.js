/* Square owns card entry and tokenization; Pontora receives only a one-use token. */
(async function () {
  "use strict";
  const form = document.getElementById("square-payment-form");
  if (!form) return;
  const status = document.getElementById("square-status");
  const button = document.getElementById("square-pay");
  const options = JSON.parse(document.getElementById("square-options").textContent);
  const reload = document.getElementById("square-reload");
  const sdk = document.getElementById("square-sdk");
  reload.addEventListener("click", () => window.location.reload());
  let card;
  let stopped = false;
  let timer;
  let sdkLoaded, sdkFailed;
  try {
    // A blocked script or unresolved provider iframe must not leave checkout spinning forever.
    const initialize = async () => {
      if (!window.Square) await new Promise((resolve, reject) => {
        sdkLoaded = resolve;
        sdkFailed = () => reject(new Error("Secure form unavailable"));
        sdk.addEventListener("load", sdkLoaded);
        sdk.addEventListener("error", sdkFailed);
      });
      if (stopped) return;
      const payments = window.Square.payments(options.applicationId, options.locationId);
      card = await payments.card();
      if (stopped) {await card.destroy(); return;}
      await card.attach("#square-card");
    };
    await Promise.race([initialize(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Secure form timed out")), 20000);
    })]);
    status.textContent = "";
    button.disabled = false;
  } catch (_) {
    stopped = true;
    form.hidden = true;
    status.textContent = "The secure card form could not load. No payment was submitted. Reload to try again, or use another browser.";
    reload.hidden = false;
    if (card) card.destroy().catch(() => {});
    return;
  } finally {
    clearTimeout(timer);
    sdk.removeEventListener("load", sdkLoaded);
    sdk.removeEventListener("error", sdkFailed);
  }
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = "Checking card details...";
    let token;
    try {
      token = await card.tokenize({ amount: options.amount, currencyCode: "USD", intent: "CHARGE",
        customerInitiated: true, sellerKeyedIn: false, billingContact: {} });
      if (token.status !== "OK") throw new Error("Card details need attention.");
    } catch (_) {
      status.textContent = "Check your card details and try again.";
      button.disabled = false;
      return;
    }
    status.textContent = "Processing payment. Please keep this page open.";
    const body = new FormData(form);
    body.set("source_id", token.token);
    try {
      const response = await fetch(window.location.href, { method: "POST", body,
        credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
      const result = await response.json();
      form.hidden = true;
      if (result.state === "paid" && result.return_url) {
        window.location.assign(result.return_url);
        return;
      }
      status.textContent = result.error || "Payment verification is pending. Check its status before paying again.";
      document.getElementById(result.state === "expired" ? "square-retry-form" : "square-check-form").hidden = false;
    } catch (_) {
      form.hidden = true;
      status.textContent = "We could not confirm the result. Check payment status before paying again.";
      document.getElementById("square-check-form").hidden = false;
    }
  });
}());
