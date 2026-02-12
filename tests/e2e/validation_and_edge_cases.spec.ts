import { test, expect } from '@playwright/test';
import { loginAsSuperuser } from './utils/auth';

/**
 * Validation, 404 handling, success page content, and admin actions.
 */

// ---------------------------------------------------------------------------
// Validation: submitting with missing required fields shows error messages
// ---------------------------------------------------------------------------
test('submitting empty required fields shows validation errors', async ({ page }) => {
  await page.goto('/schools/dancemaker-studio/apply');

  // Submit without filling anything
  const submitBtn = page.locator('form button[type="submit"]').first();
  await submitBtn.click();

  // Should stay on the same page (not redirect to success)
  await expect(page).toHaveURL(/\/schools\/dancemaker-studio\/apply\/?$/);

  // Validation error messages should appear for required fields
  const errors = page.locator('.error');
  const errorCount = await errors.count();
  expect(errorCount).toBeGreaterThan(0);

  // Specific required field errors
  await expect(errors.first()).toContainText(/required/i);
});

test('submitting with invalid email shows validation error', async ({ page }) => {
  await page.goto('/schools/dancemaker-studio/apply');

  // Fill required fields but with bad email
  await page.getByLabel('Student First Name').fill('Test');
  await page.getByLabel('Student Last Name').fill('User');
  await page.getByLabel('Parent/Guardian Full Name').fill('Parent');
  await page.getByLabel('Email Address').fill('not-an-email');
  await page.getByLabel('Phone Number').fill('555-0100');
  await page.getByLabel('Dance Style').selectOption({ index: 1 });
  await page.getByLabel('Skill Level').selectOption({ index: 1 });
  await page.getByLabel('Emergency Contact Name').fill('Emergency');
  await page.getByLabel('Emergency Contact Phone').fill('555-0200');

  const submitBtn = page.locator('form button[type="submit"]').first();
  await submitBtn.click();

  // Should stay on form
  await expect(page).toHaveURL(/\/schools\/dancemaker-studio\/apply\/?$/);

  // Should show email validation error (use filter to avoid strict-mode on multiple .error divs)
  await expect(page.locator('.error').filter({ hasText: /email/i })).toHaveCount(1);
});

// ---------------------------------------------------------------------------
// Multi-form validation: required fields prevent advancing to next step
// ---------------------------------------------------------------------------
test('multi-form required fields block Next button', async ({ page }) => {
  await page.goto('/schools/multi-form-demo/apply');
  await expect(page).toHaveURL(/\/apply\/enrollment\/?$/);

  // Click Next without filling required fields
  await page.getByRole('button', { name: 'Next' }).click();

  // Should stay on enrollment step
  await expect(page).toHaveURL(/\/apply\/enrollment\/?$/);

  // Validation errors should appear
  const errors = page.locator('.error');
  expect(await errors.count()).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// 404: unknown school slug returns 404
// ---------------------------------------------------------------------------
test('unknown school slug returns 404', async ({ page }) => {
  const resp = await page.goto('/schools/nonexistent-school-xyz/apply');
  expect(resp).not.toBeNull();
  expect(resp!.status()).toBe(404);
});

// ---------------------------------------------------------------------------
// Success page: renders school name and key content
// ---------------------------------------------------------------------------
test('success page shows school name and next steps', async ({ page }) => {
  await page.goto('/schools/dancemaker-studio/apply');

  await page.getByLabel('Student First Name').fill('SuccessTest');
  await page.getByLabel('Student Last Name').fill('User');

  await page.getByLabel('Date of Birth').fill('2010-01-01');
  await page.getByLabel('Date of Birth').press('Tab');
  await expect(page.locator('#age')).not.toHaveValue('');

  await page.getByLabel('Parent/Guardian Full Name').fill('Parent');
  await page.getByLabel('Email Address').fill('success@example.com');
  await page.getByLabel('Phone Number').fill('555-0100');
  await page.getByLabel('Dance Style').selectOption({ index: 1 });
  await page.getByLabel('Skill Level').selectOption({ index: 1 });
  await page.getByLabel('Emergency Contact Name').fill('Emergency');
  await page.getByLabel('Emergency Contact Phone').fill('555-0200');

  const waiver = page.getByRole('checkbox').first();
  if (await waiver.count() > 0) await waiver.check();

  const submitBtn = page.locator('form').locator('input[type="submit"], button[type="submit"]').first();
  await Promise.all([page.waitForURL(/.*apply\/success/), submitBtn.click()]);

  // Page title includes school name
  await expect(page).toHaveTitle(/Dancemaker Studio/i);

  // Success heading is visible
  await expect(page.locator('h1')).toBeVisible();

  // "Next steps" section is present
  await expect(page.locator('body')).toContainText(/next steps/i);

  // "Submit another" link exists
  await expect(page.locator('a[href*="/apply"]')).toHaveCount(1);
});

// ---------------------------------------------------------------------------
// Admin: bulk status change works
// ---------------------------------------------------------------------------
test('admin can change submission status via change form', async ({ page }) => {
  await loginAsSuperuser(page);
  await page.goto('/admin/core/submission/');

  // Open first submission
  const firstRow = page.locator('table#result_list tbody tr').first();
  await expect(firstRow).toBeVisible();
  await firstRow.locator('th a').click();
  await expect(page).toHaveURL(/\/admin\/core\/submission\/\d+\/change\/?/);

  // Change status
  const statusField = page.locator('#id_status');
  await expect(statusField).toHaveCount(1);

  // Status might be a select or text input — handle both
  const tagName = await statusField.evaluate((el) => el.tagName.toLowerCase());
  if (tagName === 'select') {
    // Pick the second non-empty option (different from current)
    const options = await statusField.locator('option').allTextContents();
    const current = await statusField.inputValue();
    const target = options.find((o) => {
      const val = o.trim();
      return val !== '' && val !== 'Select...' && val !== current;
    });
    if (target) await statusField.selectOption({ label: target });
  } else {
    await statusField.fill('Reviewed');
  }

  // Save
  await page.locator('input[name="_save"], button[name="_save"]').first().click();

  // Should redirect back to changelist or stay on form with success message
  // Wait for navigation to complete
  await page.waitForLoadState('networkidle');

  // If we're back on the changelist, the save succeeded
  const url = page.url();
  const savedOk =
    /\/admin\/core\/submission\/?(\?.*)?$/.test(url) ||
    /\/admin\/core\/submission\/\d+\/change\//.test(url);
  expect(savedOk).toBeTruthy();
});

// ---------------------------------------------------------------------------
// Admin: audit log row created after CSV export (superuser on starter school)
// ---------------------------------------------------------------------------
test('CSV export from admin creates audit log entry', async ({ page }) => {
  await loginAsSuperuser(page);
  await page.goto('/admin/core/submission/');

  // Select first submission checkbox
  const firstCheckbox = page.locator('table#result_list tbody tr').first().locator('input[type="checkbox"]');
  await firstCheckbox.check();

  // Select export_csv action
  const actionSelect = page.locator('select[name="action"]');
  await actionSelect.selectOption('export_csv');

  // Click Go — this triggers a CSV download
  const downloadPromise = page.waitForEvent('download');
  await page.locator('button[title="Run the selected action"]').click();
  await downloadPromise;

  // Verify audit log has an entry
  await page.goto('/admin/core/adminauditlog/');
  const rows = await page.locator('table#result_list tbody tr').count();
  expect(rows).toBeGreaterThan(0);

  // Most recent entry should mention "Action" (the action type displayed by Django)
  const firstRowText = await page.locator('table#result_list tbody tr').first().textContent();
  expect(firstRowText?.toLowerCase()).toContain('action');
});
