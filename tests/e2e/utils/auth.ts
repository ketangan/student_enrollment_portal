import { Page, expect } from '@playwright/test';

async function doLogin(page: Page, username?: string, password?: string) {
  if (!username || !password) {
    throw new Error('E2E credentials are not set in environment variables');
  }

  await page.goto('/admin/login/', { waitUntil: 'domcontentloaded' });

  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);

  await Promise.all([
    page.waitForURL('**/admin/**'),
    page.locator('button[type="submit"], input[type="submit"]').first().click(),
  ]);

  // sanity check: we should not still be on login page
  await expect(page).not.toHaveURL(/\/admin\/login\/?/);
}

export async function loginAsSuperuser(page: Page) {
  await doLogin(page, process.env.ADMIN_USER, process.env.ADMIN_PASS);
}

export async function loginAsSchoolAdmin(page: Page) {
  await doLogin(page, process.env.SCHOOL_ADMIN_USER, process.env.SCHOOL_ADMIN_PASS);
}
