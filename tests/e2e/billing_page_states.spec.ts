import { test, expect } from '@playwright/test';
import { loginAsSuperuser, loginAsSchoolAdmin } from './utils/auth';

/**
 * Test billing page UI rendering for different school states:
 * - Trial: shows upgrade cards
 * - Active: shows subscription management
 * - Locked: shows reactivation banner
 */

test.describe('Billing page states', () => {
  test('trial school shows upgrade cards', async ({ page }) => {
    // enrollment-request-demo is seeded as trial (no subscription)
    await loginAsSuperuser(page);
    await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });

    // Should show "Upgrade Your Plan" or similar section
    const body = page.locator('body');
    const hasUpgradeSection =
      (await body.locator('h2:has-text("Upgrade")').count()) > 0 ||
      (await body.locator('.pricing-card').count()) > 0 ||
      (await body.locator('button:has-text("Upgrade")').count()) > 0;

    expect(hasUpgradeSection).toBeTruthy();
  });

  test('active school shows current plan and billing management', async ({ page }) => {
    // dancemaker-studio is the school admin's school (starter plan)
    await loginAsSchoolAdmin(page);
    await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });

    // Billing page should load successfully
    await expect(page).toHaveURL(/\/admin\/billing\/?/);
    await expect(page.locator('h1')).toContainText(/billing/i);

    // Should show current plan section
    const body = page.locator('body');
    const hasCurrentPlan =
      (await body.locator('h2:has-text("Current Plan")').count()) > 0 ||
      (await body.locator('.current-plan').count()) > 0 ||
      (await body.locator('text=/plan/i').count()) > 0;

    expect(hasCurrentPlan).toBeTruthy();
  });

  test('locked school shows reactivation options', async ({ page }) => {
    const TEST_SCHOOL_SLUG = 'dancemaker-studio';

    // Helper to set school active state
    async function setSchoolActive(isActive: boolean) {
      await loginAsSuperuser(page);
      await page.goto('/admin/core/school/', { waitUntil: 'domcontentloaded' });

      const schoolRow = page.locator(`tr:has-text("${TEST_SCHOOL_SLUG}")`).first();
      await schoolRow.locator('a[href*="/change/"]').first().click();
      await expect(page).toHaveURL(/\/admin\/core\/school\/\d+\/change\/?/);

      const checkbox = page.locator('#id_is_active');
      if (isActive) {
        await checkbox.check();
      } else {
        await checkbox.uncheck();
      }

      await page.locator('input[name="_save"]').click();
      await expect(page).toHaveURL(/\/admin\/core\/school\/?/);
    }

    // Setup: deactivate school
    await setSchoolActive(false);

    try {
      // Logout superuser, login as school admin
      await page.goto('/admin/logout/', { waitUntil: 'domcontentloaded' });
      await loginAsSchoolAdmin(page);

      // Navigate to billing (should still be accessible)
      await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin\/billing\/?/);

      // Should show error/warning about subscription ending
      const body = page.locator('body');
      const hasEndedBanner =
        (await body.locator('.error:has-text("subscription")').count()) > 0 ||
        (await body.locator('.error:has-text("ended")').count()) > 0 ||
        (await body.locator('.error:has-text("expired")').count()) > 0 ||
        (await body.locator('text=/subscription.*ended/i').count()) > 0;

      expect(hasEndedBanner).toBeTruthy();

      // Should show upgrade/reactivation options
      const hasUpgradeOptions =
        (await body.locator('.pricing-card').count()) > 0 ||
        (await body.locator('button:has-text("Upgrade")').count()) > 0 ||
        (await body.locator('a:has-text("Reactivate")').count()) > 0;

      expect(hasUpgradeOptions).toBeTruthy();

    } finally {
      // Cleanup: restore school to active state
      await setSchoolActive(true);
    }
  });

  test('billing page does not show stale subscription status when locked', async ({ page }) => {
    const TEST_SCHOOL_SLUG = 'dancemaker-studio';

    // Helper to set school active state
    async function setSchoolActive(isActive: boolean) {
      await loginAsSuperuser(page);
      await page.goto('/admin/core/school/', { waitUntil: 'domcontentloaded' });

      const schoolRow = page.locator(`tr:has-text("${TEST_SCHOOL_SLUG}")`).first();
      await schoolRow.locator('a[href*="/change/"]').first().click();
      await expect(page).toHaveURL(/\/admin\/core\/school\/\d+\/change\/?/);

      const checkbox = page.locator('#id_is_active');
      if (isActive) {
        await checkbox.check();
      } else {
        await checkbox.uncheck();
      }

      await page.locator('input[name="_save"]').click();
      await expect(page).toHaveURL(/\/admin\/core\/school\/?/);
    }

    // Setup: deactivate school
    await setSchoolActive(false);

    try {
      await page.goto('/admin/logout/', { waitUntil: 'domcontentloaded' });
      await loginAsSchoolAdmin(page);

      await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin\/billing\/?/);

      // Should NOT show "Subscription status: active" when locked
      const body = page.locator('body');
      const hasStatusActive = await body.locator('.billing-status:has-text("active")').count();

      // If there's a billing-status div showing "active", that's a bug
      expect(hasStatusActive).toBe(0);

    } finally {
      // Cleanup: restore school to active state
      await setSchoolActive(true);
    }
  });
});
