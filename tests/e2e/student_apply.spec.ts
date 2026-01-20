import { test, expect } from '@playwright/test';
import { loginAsSuperuser } from './utils/auth';

test('student apply flow creates a submission and is visible in admin', async ({ page }) => {
  await page.goto('/schools/dancemaker-studio/apply');

  await page.getByLabel('Student First Name').fill('E2EFirst');
  await page.getByLabel('Student Last Name').fill('E2ELast');

  await page.getByLabel('Date of Birth').fill('2010-01-01');
  await page.getByLabel('Date of Birth').press('Tab');

  // If your age computation is based on current date, value may vary.
  // Safer: assert it becomes non-empty.
  await expect(page.locator('#age')).not.toHaveValue('');

  await page.getByLabel('Parent/Guardian Full Name').fill('Parent Person');
  await page.getByLabel('Email Address').fill('parent@example.com');
  await page.getByLabel('Phone Number').fill('555-0100');

  // Prefer index 1 to avoid placeholder
  await page.getByLabel('Dance Style').selectOption({ index: 1 });
  await page.getByLabel('Skill Level').selectOption({ index: 1 });

  await page.getByLabel('Emergency Contact Name').fill('Emergency Person');
  await page.getByLabel('Emergency Contact Phone').fill('555-0200');

  // Waiver checkbox (label text flexible)
  const waiver = page.getByRole('checkbox').first();
  if (await waiver.count() > 0) {
    await waiver.check();
  }

  const submitBtn = page.locator('form').locator('input[type="submit"], button[type="submit"]').first();
  await Promise.all([page.waitForURL(/.*apply\/success/), submitBtn.click()]);
  await expect(page).toHaveURL(/.*apply\/success/);

  // Verify in admin
  await loginAsSuperuser(page);
  await page.goto('/admin/core/submission/');

  const rows = await page.locator('table#result_list tbody tr').count();
  expect(rows).toBeGreaterThan(0);
});
