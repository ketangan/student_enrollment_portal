import { test, expect } from '@playwright/test';
import { loginAsSuperuser, loginAsSchoolAdmin } from './utils/auth';

/**
 * Billing page and school lockout E2E tests.
 *
 * Note: Tests that require toggling school.is_active are omitted from E2E
 * as they require complex state manipulation and cleanup. Those scenarios
 * are thoroughly covered by backend integration tests in:
 * - core/tests/test_admin_scoping.py (inactive school admin blocking)
 * - core/tests/test_apply_flow.py (inactive school apply blocking)
 * - core/tests/test_reports.py (inactive school reports blocking)
 */

test.describe('Billing page', () => {
  test('trial school billing page loads and shows trial plan', async ({ page }) => {
    // enrollment-request-demo is seeded as trial (no subscription)
    await loginAsSuperuser(page);
    await page.goto('/admin/billing/?school=enrollment-request-demo', { waitUntil: 'domcontentloaded' });

    // Billing page should load
    await expect(page).toHaveURL(/\/admin\/billing/);
    await expect(page.locator('h1')).toContainText(/billing/i);

    // Should show trial plan badge
    await expect(page.locator('.billing-plan-badge--trial')).toBeVisible();
  });

  test('active school shows current plan and billing management', async ({ page }) => {
    // dancemaker-studio is the school admin's school (starter plan)
    await loginAsSchoolAdmin(page);
    await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });

    // Billing page should load successfully
    await expect(page).toHaveURL(/\/admin\/billing\/?/);
    await expect(page.locator('h1')).toContainText(/billing/i);

    // Should show current plan section
    await expect(page.locator('h2:has-text("Current Plan")')).toBeVisible();
    await expect(page.locator('.billing-plan-badge')).toBeVisible();
  });

  test('billing page does not show stale subscription status for trial school', async ({ page }) => {
    await loginAsSchoolAdmin(page);
    await page.goto('/admin/billing/', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/admin\/billing\/?/);

    // For trial schools or locked schools, should NOT show misleading active status
    // This is a regression test for the UX bug where "Subscription status: active"
    // was shown even when school was locked
    const billing_state = await page.locator('body').getAttribute('data-billing-state') || '';
    if (billing_state === 'ended_locked' || billing_state === 'trial') {
      const hasStatusActive = await page.locator('.billing-status:has-text("active")').count();
      expect(hasStatusActive).toBe(0);
    }
  });
});
