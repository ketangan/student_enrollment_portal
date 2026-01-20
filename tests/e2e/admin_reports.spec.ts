import { test, expect } from '@playwright/test';
import { loginAsSuperuser } from './utils/auth';

test('admin reports hub and export', async ({ page }) => {
  await loginAsSuperuser(page);

  // Reports hub
  await page.goto('/admin/reports/');
  await expect(page).toHaveURL(/\/admin\/reports\/?/);

  const slug = 'dancemaker-studio';

  // The hub links open in new tab (target=_blank). For test stability, open in same tab.
  const reportsLink = page.locator(`a[href*="/schools/${slug}/admin/reports"]`).first();
  await expect(reportsLink).toHaveCount(1);

  const href = await reportsLink.getAttribute('href');
  expect(href).toBeTruthy();

  await page.goto(href!);
  await expect(page).toHaveURL(new RegExp(`/schools/${slug}/admin/reports`));

  // Page renders
  await expect(page.locator('body')).toBeVisible();

  // Charts exist (canvas)
  await expect(page.locator('canvas').first()).toBeVisible();

  // Click Last 7 filter (you have links, not buttons)
  const range7 = page.locator('a[href*="range=7"]').first();
  await expect(range7).toHaveCount(1);
  await Promise.all([
    page.waitForLoadState('networkidle'),
    range7.click(),
  ]);
  expect(page.url()).toContain('range=7');

  // Back to Last 30
  const range30 = page.locator('a[href*="range=30"]').first();
  await Promise.all([
    page.waitForLoadState('networkidle'),
    range30.click(),
  ]);
  expect(page.url()).toContain('range=30');

  // Export CSV download
  const exportLink = page.locator('a[href*="export=1"]').first();
  await expect(exportLink).toHaveCount(1);

  const downloadPromise = page.waitForEvent('download');
  await exportLink.click();
  const download = await downloadPromise;

  const suggested = download.suggestedFilename();
  expect(suggested).toContain(slug);
});
