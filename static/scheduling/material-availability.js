/* Both guided and group selections use the same integer-thousandths check. */
const materialAvailability = {
  enough(selections, available) {
    const required = {};
    for (const selection of selections) {
      if (!selection || !Object.keys(selection.requirements).length) return false;
      for (const [id, amount] of Object.entries(selection.requirements)) {
        if (!Number.isSafeInteger(amount) || amount <= 0) return false;
        required[id] = (required[id] || 0) + amount;
      }
    }
    return Object.entries(required).every(([id, amount]) => amount <= (available[id] || 0));
  }
};
if (typeof module !== 'undefined') module.exports = materialAvailability;
