import { test, expect } from '@playwright/test';
import { loginAsSuperuser } from './utils/auth';

test.use({
  viewport: { width: 390, height: 844 }, // typical mobile width
});

test('admin submission YAML form has no horizontal overflow on mobile', async ({ page }) => {
  test.setTimeout(20_000);
  // Create a submission with select fields via the public apply form.
  const uniqueFirst = `E2EMobile${Date.now()}`;

  await page.goto('/schools/dancemaker-studio/apply', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Student First Name').fill(uniqueFirst);
  await page.getByLabel('Student Last Name').fill('E2ELast');

  await page.getByLabel('Date of Birth').fill('2010-01-01');
  await page.getByLabel('Date of Birth').press('Tab');
  await expect(page.locator('#age')).not.toHaveValue('');

  await page.getByLabel('Parent/Guardian Full Name').fill('Parent Person');
  await page.getByLabel('Email Address').fill('parent@example.com');
  await page.getByLabel('Phone Number').fill('555-0100');

  await page.getByLabel('Dance Style').selectOption({ index: 1 });
  await page.getByLabel('Skill Level').selectOption({ index: 1 });

  await page.getByLabel('Emergency Contact Name').fill('Emergency Person');
  await page.getByLabel('Emergency Contact Phone').fill('555-0200');

  const waiver = page.getByRole('checkbox').first();
  if (await waiver.count() > 0) {
    await waiver.check();
  }

  const submitBtn = page.locator('form').locator('input[type="submit"], button[type="submit"]').first();
  await Promise.all([page.waitForURL(/.*apply\/success/), submitBtn.click()]);
  await expect(page).toHaveURL(/.*apply\/success/);

  // Open the submission in admin and verify layout is constrained.
  await loginAsSuperuser(page);
  await page.goto(`/admin/core/submission/?q=${encodeURIComponent(uniqueFirst)}`, {
    waitUntil: 'domcontentloaded',
  });

  await page.locator('table#result_list tbody tr').first().locator('th a').click();
  await expect(page).toHaveURL(/\/admin\/core\/submission\/\d+\/change\/?/);

  const yamlWrap = page.locator('.field-yaml_form .yaml-form-wrap');
  await expect(yamlWrap).toHaveCount(1);

  // Generic guardrail: no horizontal scrolling.
  const hasHorizontalOverflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth > window.innerWidth + 1;
  });
  expect(hasHorizontalOverflow).toBeFalsy();

  // Targeted check: selection widgets should fit within the YAML card.
  // Note: Django admin themes often enhance <select> into Select2, leaving hidden <select>
  // elements in the DOM. Checking those hidden elements yields false positives.
  const overflowCount = await page.evaluate(() => {
    const card = document.querySelector('.field-yaml_form .yaml-card');
    if (!card) return 0;
    const cardRect = card.getBoundingClientRect();

    const isVisible = (el: Element) => {
      const e = el as HTMLElement;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const style = window.getComputedStyle(e);
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    };

    // Prefer Select2 containers when present; otherwise fall back to native selects.
    const select2 = Array.from(card.querySelectorAll('.select2-container')).filter(isVisible) as HTMLElement[];
    const native = Array.from(card.querySelectorAll('select')).filter(isVisible) as HTMLElement[];
    const widgets = (select2.length ? select2 : native) as HTMLElement[];

    let bad = 0;
    for (const el of widgets) {
      const r = el.getBoundingClientRect();
      if (r.left < cardRect.left - 1 || r.right > cardRect.right + 1) {
        bad += 1;
      }
    }
    return bad;
  });

  expect(overflowCount).toBe(0);
});
