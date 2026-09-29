"""Display-only fee preview. Never creates a payment or changes enrollment."""
from decimal import Decimal, InvalidOperation


def get_payment_preview(raw, status):
    portal = (raw or {}).get("family_portal", {})
    config = portal.get("payment_preview", {}) if isinstance(portal, dict) else {}
    if not isinstance(config, dict) or config.get("enabled") is not True or status != "Fee Pending":
        return None
    try:
        amount = Decimal(str(config.get("amount", "")))
        if not amount.is_finite() or not 0 < amount <= 999999 or amount != amount.quantize(Decimal("0.01")):
            return None
    except InvalidOperation:
        return None
    return {"amount": f"{amount:.2f}", "label": str(config.get("label") or "Enrollment fee")}
