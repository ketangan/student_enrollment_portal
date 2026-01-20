import { test, expect } from '@playwright/test';
import { loginAsSchoolAdmin } from './utils/auth';

test('school admin can access own reports and not other schools; admin submissions scoped', async ({ page }) => {
  await loginAsSchoolAdmin(page);

  // Known school slugs in your repo (from your YAML list)
  const knownSlugs = [
    'dancemaker-studio',
    'kimberlas-classical-ballet',
    'torrance-sister-city-association',
  ];

  // 1) Discover own school by using the School admin list + its Reports link
  await page.goto('/admin/core/school/', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/admin\/core\/school\/?/);

  // Clickable "Reports" button/link in list_display
  const reportsLink = page.locator('a[href*="/schools/"][href*="/admin/reports"]').first();
  await expect(reportsLink).toHaveCount(1);

  const href = await reportsLink.getAttribute('href');
  expect(href).toBeTruthy();

  // href might be absolute or relative; extract the slug robustly
  const match = href!.match(/\/schools\/([^/]+)\/admin\/reports/);
  expect(match).toBeTruthy();

  const ownSlug = match![1];
  expect(ownSlug.length).toBeGreaterThan(0);

  // Pick an "other" school that is different
  const otherSlug = knownSlugs.find((s) => s !== ownSlug) || 'kimberlas-classical-ballet';

  // 2) Own reports should work
  await page.goto(`/schools/${ownSlug}/admin/reports`, { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(new RegExp(`/schools/${ownSlug}/admin/reports`));

  await expect(page.locator('h1')).toContainText(/reports/i);
  await expect(page.locator('.reports-subtitle')).toHaveCount(1);

  // 3) Other school's reports must be blocked (404/403)
  const resp = await page.goto(`/schools/${otherSlug}/admin/reports`, {
    waitUntil: 'domcontentloaded',
  });

  expect(resp).not.toBeNull();
  expect([404, 403]).toContain(resp!.status());

  // 4) Submissions list should load (scoped by membership)
  await page.goto('/admin/core/submission/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('table#result_list')).toBeVisible();
});
