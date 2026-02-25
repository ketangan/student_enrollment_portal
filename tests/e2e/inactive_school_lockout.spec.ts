import { test, expect, Page } from '@playwright/test';
import { loginAsSuperuser, loginAsSchoolAdmin } from './utils/auth';

/**
 * Test that inactive schools are properly locked out of admin access
 * while still allowing billing page access for reactivation.
 *
 * Test school: dancemaker-studio (will be temporarily deactivated)
 */

test.describe('Inactive school lockout', () => {
  const TEST_SCHOOL_SLUG = 'dancemaker-studio';

  // Helper to set school active state via Django admin UI
  async function setSchoolActive(page: Page, slug: string, isActive: boolean) {
    await loginAsSuperuser(page);
    await page.goto('/admin/core/school/', { waitUntil: 'domcontentloaded' });

    // Find the school row and click edit
    const schoolRow = page.locator(`tr:has-text("${slug}")`).first();
    await schoolRow.locator('a[href*="/change/"]').first().click();

    // Wait for change form to load
    await expect(page).toHaveURL(/\/admin\/core\/school\/\d+\/change\/?/);

    // Set is_active checkbox
    const checkbox = page.locator('#id_is_active');
    if (isActive) {
      await checkbox.check();
    } else {
      await checkbox.uncheck();
    }

    // Save
    await page.locator('input[name="_save"]').click();
    await expect(page).toHaveURL(/\/admin\/core\/school\/?/);
  }

  test('inactive school admin blocked from admin but can access billing', async ({ page }) => {
    // Setup: deactivate the test school
    await setSchoolActive(page, TEST_SCHOOL_SLUG, false);

    try {
      // Logout superuser, login as school admin
      await page.goto('/admin/logout/', { waitUntil: 'domcontentloaded' });
      await loginAsSchoolAdmin(page);

      // Test 1: Admin index should be blocked (403 or redirect)
      const adminResp = await page.goto('/admin/', { waitUntil: 'domcontentloaded' });
      // Either 403 forbidden or redirected away from /admin/
      const blockedFromAdmin =
        adminResp?.status() === 403 ||
        !page.url().includes('/admin/') ||
        page.url().includes('/admin/login');
      expect(blockedFromAdmin).toBeTruthy();

      // Test 2: Billing page SHOULD be accessible
      await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin\/billing\/?/);
      await expect(page.locator('h1')).toContainText(/billing/i);

      // Test 3: Apply form should return 404
      const applyResp = await page.goto(`/schools/${TEST_SCHOOL_SLUG}/apply`, {
        waitUntil: 'domcontentloaded',
      });
      expect(applyResp?.status()).toBe(404);

      // Test 4: Reports should return 404
      const reportsResp = await page.goto(`/schools/${TEST_SCHOOL_SLUG}/admin/reports`, {
        waitUntil: 'domcontentloaded',
      });
      expect(reportsResp?.status()).toBe(404);

    } finally {
      // Cleanup: restore school to active state
      await setSchoolActive(page, TEST_SCHOOL_SLUG, true);
    }
  });

  test('superuser bypasses inactive school restrictions', async ({ page }) => {
    // Setup: deactivate the test school
    await setSchoolActive(page, TEST_SCHOOL_SLUG, false);

    try {
      await loginAsSuperuser(page);

      // Superuser should access admin index
      await page.goto('/admin/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin\/?/);

      // Superuser should access billing
      await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin\/billing\/?/);

      // Superuser should access reports
      await page.goto(`/schools/${TEST_SCHOOL_SLUG}/admin/reports`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(page).toHaveURL(new RegExp(`/schools/${TEST_SCHOOL_SLUG}/admin/reports`));
      await expect(page.locator('h1')).toContainText(/reports/i);

    } finally {
      // Cleanup: restore school to active state
      await setSchoolActive(page, TEST_SCHOOL_SLUG, true);
    }
  });

  test('inactive school public apply form returns 404', async ({ page }) => {
    // Setup: deactivate the test school
    await setSchoolActive(page, TEST_SCHOOL_SLUG, false);

    try {
      // Unauthenticated request to apply form should get 404
      const resp = await page.goto(`/schools/${TEST_SCHOOL_SLUG}/apply`, {
        waitUntil: 'domcontentloaded',
      });
      expect(resp?.status()).toBe(404);

    } finally {
      // Cleanup: restore school to active state
      await setSchoolActive(page, TEST_SCHOOL_SLUG, true);
    }
  });
});
