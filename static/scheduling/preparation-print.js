(() => {
  document.getElementById('print-report').addEventListener('click', () => window.print());
  window.addEventListener('load', () => {
    if (document.body.dataset.printReady === 'true') window.print();
  });
})();
