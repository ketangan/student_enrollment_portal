/**
 * E2E: AI Summary button layout
 *
 * Verifies that "Generate AI Summary" and "History" buttons are visually
 * consistent (equal width, both btn-block) in the Jazzmin sidebar.
 *
 * Uses a growth-plan school so the button is visible.
 * Fixture school + submission are created before and deleted after.
 */
import { test, expect } from '@playwright/test';
import { execFileSync } from 'child_process';
import path from 'path';
import { loginAsSuperuser } from './utils/auth';

const repoRoot = path.resolve(__dirname, '..', '..');
const python = path.join(repoRoot, 'venv', 'bin', 'python');

function shell(code: string): string {
  const raw = execFileSync(python, ['manage.py', 'shell', '-c', code], {
    cwd: repoRoot,
    env: process.env,
  }).toString();
  // Django may emit fixture-import notices before our output; take the last non-empty line.
  const lines = raw.split('\n').map(l => l.trim()).filter(Boolean);
  return lines[lines.length - 1] ?? '';
}

test.describe('AI Summary button layout', () => {
  let submissionPk: string;

  test.beforeAll(() => {
    // Clean up any stale fixture, then create fresh.
    shell(`
from core.models import School, Submission
Submission.objects.filter(public_id='E2E-AI-LAYOUT').delete()
School.objects.filter(slug='e2e-ai-layout-test').delete()
    `.trim());

    submissionPk = shell(`
from core.models import School, Submission
school = School.objects.create(
    slug='e2e-ai-layout-test',
    display_name='E2E AI Layout Test',
    plan='growth',
)
sub = Submission.objects.create(
    school=school,
    public_id='E2E-AI-LAYOUT',
    data={'first_name': 'Test', 'last_name': 'Applicant'},
)
print(sub.pk)
    `.trim());

    console.log(`[setup] submission PK: ${submissionPk}`);
  });

  test.afterAll(() => {
    shell(`
from core.models import School, Submission
Submission.objects.filter(public_id='E2E-AI-LAYOUT').delete()
School.objects.filter(slug='e2e-ai-layout-test').delete()
    `.trim());
  });

  test('Generate AI Summary and History buttons have equal width', async ({ page }) => {
    await loginAsSuperuser(page);

    await page.goto(`/admin/core/submission/${submissionPk}/change/`, { waitUntil: 'domcontentloaded' });

    // Dump sidebar HTML for diagnostics
    const sidebarHtml = await page.locator('#jazzy-actions').innerHTML().catch(() => 'NOT FOUND');
    console.log(`[debug] sidebar HTML:\n${sidebarHtml}\n`);
    await page.screenshot({ path: 'tests/e2e/screenshots/ai_summary_full_page.png', fullPage: true });

    // Both buttons must be present
    const aiBtn = page.locator('#ai-summary-btn');
    const historyBtn = page.locator('.object-tools a:not(#ai-summary-btn)').first();

    await expect(aiBtn).toBeVisible();
    await expect(historyBtn).toBeVisible();

    // Both must use btn-block (Jazzmin full-width sidebar button pattern)
    const aiBtnClass = await aiBtn.getAttribute('class') ?? '';
    const historyBtnClass = await historyBtn.getAttribute('class') ?? '';
    console.log(`[debug] AI btn classes:      ${aiBtnClass}`);
    console.log(`[debug] History btn classes:  ${historyBtnClass}`);
    expect(aiBtnClass).toContain('btn-block');
    expect(historyBtnClass).toContain('btn-block');

    // Widths must be equal (same container, same btn-block)
    const aiBox = await aiBtn.boundingBox();
    const historyBox = await historyBtn.boundingBox();
    console.log(`[debug] AI btn box:      ${JSON.stringify(aiBox)}`);
    console.log(`[debug] History btn box: ${JSON.stringify(historyBox)}`);
    expect(aiBox).not.toBeNull();
    expect(historyBox).not.toBeNull();
    expect(Math.abs(aiBox!.width - historyBox!.width)).toBeLessThanOrEqual(2);

    // Save sidebar screenshot for human review
    await page.locator('#jazzy-actions').screenshot({ path: 'tests/e2e/screenshots/ai_summary_buttons.png' });
  });
});
