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


test('multi-form apply flow completes across steps', async ({ page }) => {
  const slug = 'multi-form-demo';

  // /apply should redirect to first form key (YAML order)
  await page.goto(`/schools/${slug}/apply`);
  await expect(page).toHaveURL(new RegExp(`/schools/${slug}/apply/enrollment/?$`));

  // Step: enrollment (required)
  await page.getByLabel('First Name').fill('E2EMultiFirst');
  await page.getByLabel('Last Name').fill('E2EMultiLast');
  await page.getByLabel('Email').fill('e2e-multi@example.com');
  await page.getByLabel('Program').selectOption('ballet');

  await Promise.all([
    page.waitForURL(new RegExp(`/schools/${slug}/apply/address/?$`)),
    page.getByRole('button', { name: 'Next' }).click(),
  ]);

  // Step: address (required)
  await page.getByLabel('Street Address').fill('123 E2E St');
  await page.getByLabel('City').fill('Testville');
  await page.getByLabel('State').fill('CA');
  await page.getByLabel('ZIP Code').fill('90210');
  await page.getByLabel('Emergency Contact Name').fill('Emergency Person');
  await page.getByLabel('Emergency Contact Phone').fill('555-0200');

  await Promise.all([
    page.waitForURL(new RegExp(`/schools/${slug}/apply/waiver/?$`)),
    page.getByRole('button', { name: 'Next' }).click(),
  ]);

  // Step: waiver (required checkbox)
  await page.getByLabel('I agree to the waiver terms').check();

  await Promise.all([
    page.waitForURL(new RegExp(`/schools/${slug}/apply/medical/?$`)),
    page.getByRole('button', { name: 'Next' }).click(),
  ]);

  // Step: medical (no required fields)
  await Promise.all([
    page.waitForURL(new RegExp(`/schools/${slug}/apply/payment/?$`)),
    page.getByRole('button', { name: 'Next' }).click(),
  ]);

  // Step: payment (required)
  await page.getByLabel('Preferred Payment Method').selectOption('cash');

  await Promise.all([
    page.waitForURL(new RegExp(`/schools/${slug}/apply/success/?$`)),
    page.getByRole('button', { name: /submit/i }).click(),
  ]);
  await expect(page).toHaveURL(new RegExp(`/schools/${slug}/apply/success/?$`));
});


test('admin can edit submission via YAML form and history shows updated label', async ({ page }) => {
  // Create a submission first (single-form school)
  const uniqueFirst = `E2EAdminEdit${Date.now()}`;

  await page.goto('/schools/dancemaker-studio/apply');
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

  // Admin edit via YAML form
  await loginAsSuperuser(page);
  await page.goto(`/admin/core/submission/?q=${encodeURIComponent(uniqueFirst)}`);

  // Open first result
  await page.locator('table#result_list tbody tr').first().locator('th a').click();
  await expect(page).toHaveURL(/\/admin\/core\/submission\/\d+\/change\/?/);

  const changeUrl = page.url();
  const idMatch = changeUrl.match(/\/admin\/core\/submission\/(\d+)\/change\/?/);
  expect(idMatch).toBeTruthy();
  const submissionId = idMatch![1];

  const editedFirst = `${uniqueFirst}-Edited`;
  // Use the YAML admin field's stable id/name to avoid strict-mode ambiguity.
  const yamlFirstName = page.locator('input#id_dyn__student_first_name');
  await expect(yamlFirstName).toHaveCount(1);
  await yamlFirstName.fill(editedFirst);

  const save = page.locator('input[name="_save"], button[name="_save"], input[value="Save"]').first();
  // Django admin "Save" typically redirects back to the changelist.
  await Promise.all([
    page.waitForURL(/\/admin\/core\/submission\/?(\?.*)?$/),
    save.click(),
  ]);

  // Re-open change page and verify persisted in YAML field.
  await page.goto(`/admin/core/submission/${submissionId}/change/`, { waitUntil: 'domcontentloaded' });
  const yamlFirstNameAfter = page.locator('input#id_dyn__student_first_name');
  await expect(yamlFirstNameAfter).toHaveCount(1);
  await expect(yamlFirstNameAfter).toHaveValue(editedFirst);

  // Verify the change message in History uses the human label
  await page.goto(`/admin/core/submission/${submissionId}/history/`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#change-history')).toContainText('Updated:');
  await expect(page.locator('#change-history')).toContainText('Student First Name');
});
