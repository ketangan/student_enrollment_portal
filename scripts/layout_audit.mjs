import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';
import dotenv from 'dotenv';

const repoRoot = process.cwd();

// Load .env.e2e if present (keeps secrets out of commands/output).
const envPath = path.join(repoRoot, '.env.e2e');
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
}

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8000';
const ADMIN_USER = process.env.ADMIN_USER;
const ADMIN_PASS = process.env.ADMIN_PASS;

const MAX_ADMIN_PAGES = Number.parseInt(process.env.AUDIT_MAX_ADMIN_PAGES || '40', 10);
const VIEWPORT_BUDGET_MS = Number.parseInt(process.env.AUDIT_VIEWPORT_BUDGET_MS || '20000', 10);

const OUT_DIR = path.join(repoRoot, 'test-results', 'layout-audit');

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function safeSlug(s) {
  return String(s).replace(/[^a-zA-Z0-9_-]+/g, '_').slice(0, 80);
}

async function login(page) {
  if (!ADMIN_USER || !ADMIN_PASS) {
    throw new Error('Missing ADMIN_USER/ADMIN_PASS in environment (.env.e2e recommended).');
  }

  await page.goto(new URL('/admin/login/?next=/admin/', BASE_URL).toString(), { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="username"]').fill(ADMIN_USER);
  await page.locator('input[name="password"]').fill(ADMIN_PASS);

  await Promise.all([
    page.waitForURL(/\/admin\//),
    page.locator('button[type="submit"], input[type="submit"]').first().click(),
  ]);
}

async function checkHorizontalOverflow(page) {
  return await page.evaluate(() => {
    const doc = document.documentElement;
    return {
      innerWidth: window.innerWidth,
      scrollWidth: doc.scrollWidth,
      overflow: doc.scrollWidth > window.innerWidth + 1,
    };
  });
}

async function checkYamlCardSelectOverflow(page) {
  return await page.evaluate(() => {
    const card = document.querySelector('.field-yaml_form .yaml-card');
    if (!card) return { hasCard: false, bad: 0, checked: 0, widget: 'none' };

    const cardRect = card.getBoundingClientRect();

    const isVisible = (el) => {
      const e = el;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const style = window.getComputedStyle(e);
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    };

    const select2 = Array.from(card.querySelectorAll('.select2-container')).filter(isVisible);
    const native = Array.from(card.querySelectorAll('select')).filter(isVisible);
    const widgets = (select2.length ? select2 : native);

    let bad = 0;
    for (const el of widgets) {
      const r = el.getBoundingClientRect();
      if (r.left < cardRect.left - 1 || r.right > cardRect.right + 1) bad += 1;
    }

    return {
      hasCard: true,
      bad,
      checked: widgets.length,
      widget: select2.length ? 'select2' : 'native',
    };
  });
}

async function snapshot(page, outPath) {
  await page.screenshot({ path: outPath, fullPage: true });
}

async function snapshotViewport(page, outPath) {
  await page.screenshot({ path: outPath, fullPage: false });
}

function normalizeAdminUrl(u) {
  const url = new URL(u, BASE_URL);
  // Keep path + search; strip hashes.
  url.hash = '';
  // Normalize trailing slash for stability.
  const p = url.pathname.replace(/\/+$/, '');
  url.pathname = p || '/';
  return url.toString();
}

function isUsefulAdminUrl(u) {
  try {
    const url = new URL(u, BASE_URL);
    if (!url.pathname.startsWith('/admin')) return false;
    // Avoid noisy/unsafe endpoints.
    if (url.pathname.includes('/logout')) return false;
    if (url.pathname.includes('/password_change')) return false;
    if (url.pathname.includes('/jsi18n')) return false;
    if (url.pathname.includes('/autocomplete')) return false;
    if (url.pathname.includes('/history')) return false;
    if (url.pathname.includes('/delete')) return false;
    if (url.pathname.endsWith('/add')) return false;
    if (url.pathname.endsWith('/add/')) return false;
    return true;
  } catch {
    return false;
  }
}

async function collectAdminLinks(page) {
  const hrefs = await page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll('a[href]'));
    const out = [];
    for (const a of anchors) {
      const href = a.getAttribute('href') || '';
      if (!href) continue;
      // Prefer navigation/sidebar anchors when possible.
      const inNav = !!a.closest('nav') || !!a.closest('.sidebar') || !!a.closest('#jazzy-sidebar');
      if (inNav || href.startsWith('/admin')) out.push(href);
    }
    return out;
  });

  const normalized = hrefs
    .map((h) => {
      try {
        return normalizeAdminUrl(h);
      } catch {
        return null;
      }
    })
    .filter(Boolean);

  const unique = Array.from(new Set(normalized));
  return unique.filter(isUsefulAdminUrl);
}

async function openFirstChangeIfPresent(page, outDir, labelPrefix, results, takeFullPage) {
  const table = page.locator('table#result_list');
  if (!(await table.count())) return;

  const first = page.locator('table#result_list tbody tr').first().locator('th a');
  if (!(await first.count())) return;

  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    first.click(),
  ]);

  const overflow = await checkHorizontalOverflow(page);
  const yamlOverflow = await checkYamlCardSelectOverflow(page);

  const file = path.join(outDir, `${safeSlug(`${labelPrefix}_change`)}.png`);
  if (takeFullPage) await snapshot(page, file);
  else await snapshotViewport(page, file);

  results.push({
    label: `${labelPrefix}_change`,
    url: page.url(),
    overflow,
    yamlOverflow,
    screenshot: path.relative(repoRoot, file),
  });
}

async function fillFormGenerically(page) {
  // Fill as much as possible without knowing exact YAML.
  // Avoid readonly fields (e.g., age) and file uploads.
  await page.evaluate(() => {
    const isVisible = (el) => {
      const e = el;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const style = window.getComputedStyle(e);
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    };

    const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
      .filter((el) => el instanceof HTMLElement && isVisible(el));

    for (const el of inputs) {
      if (el instanceof HTMLInputElement) {
        const type = (el.getAttribute('type') || 'text').toLowerCase();
        if (type === 'hidden' || type === 'file') continue;
        if (el.readOnly || el.disabled) continue;

        if (type === 'checkbox') {
          el.checked = true;
          continue;
        }

        if (type === 'email') {
          el.value = el.value || 'test@example.com';
          continue;
        }
        if (type === 'tel') {
          el.value = el.value || '555-0100';
          continue;
        }
        if (type === 'date') {
          el.value = el.value || '2010-01-01';
          continue;
        }
        if (type === 'number') {
          el.value = el.value || '10';
          continue;
        }
        // default text
        el.value = el.value || 'Test';
        continue;
      }

      if (el instanceof HTMLTextAreaElement) {
        if (el.readOnly || el.disabled) continue;
        el.value = el.value || 'Test';
        continue;
      }

      if (el instanceof HTMLSelectElement) {
        if (el.disabled) continue;
        // Choose first non-empty option.
        const opts = Array.from(el.options);
        const firstNonEmpty = opts.find((o) => (o.value || '').trim() !== '') || opts[0];
        if (!firstNonEmpty) continue;

        if (el.multiple) {
          firstNonEmpty.selected = true;
        } else {
          el.value = firstNonEmpty.value;
        }
      }
    }

    // Trigger change events for frameworks/listeners.
    for (const el of inputs) {
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
}

async function auditApplyFlow({
  page,
  outDir,
  label,
  startPath,
  maxSteps = 10,
}) {
  const startUrl = new URL(startPath, BASE_URL).toString();
  await page.goto(startUrl, { waitUntil: 'domcontentloaded' });

  const formShot = path.join(outDir, `${safeSlug(`${label}_form`)}.png`);
  await snapshot(page, formShot);

  const overflowBefore = await checkHorizontalOverflow(page);

  // If required file uploads exist, we can't submit generically.
  const hasRequiredFile = await page.evaluate(() => {
    const files = Array.from(document.querySelectorAll('input[type="file"][required]'));
    return files.length > 0;
  });

  if (hasRequiredFile) {
    return {
      label,
      url: page.url(),
      overflow: overflowBefore,
      skipped: 'required_file_upload',
      screenshot: path.relative(repoRoot, formShot),
    };
  }

  for (let step = 0; step < maxSteps; step += 1) {
    await fillFormGenerically(page);

    const nextBtn = page.locator('button[type="submit"], input[type="submit"]').first();
    if (!(await nextBtn.count())) break;

    // Click and wait for either URL change or same-page validation render.
    const prev = page.url();
    await Promise.race([
      Promise.all([
        page.waitForURL((u) => u.toString() !== prev, { timeout: 5000 }).catch(() => null),
        nextBtn.click(),
      ]),
      (async () => {
        await nextBtn.click();
        await page.waitForTimeout(400);
      })(),
    ]);

    const now = page.url();
    if (now.includes('/apply/success')) {
      const successShot = path.join(outDir, `${safeSlug(`${label}_success`)}.png`);
      await snapshot(page, successShot);
      return {
        label,
        url: now,
        overflow: await checkHorizontalOverflow(page),
        screenshot: path.relative(repoRoot, successShot),
      };
    }
  }

  // If we didn't reach success, still return state for debugging.
  const endShot = path.join(outDir, `${safeSlug(`${label}_end`)}.png`);
  await snapshot(page, endShot);
  return {
    label,
    url: page.url(),
    overflow: await checkHorizontalOverflow(page),
    screenshot: path.relative(repoRoot, endShot),
    incomplete: true,
  };
}

async function runAuditForViewport(name, viewport) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();

  const outDir = path.join(OUT_DIR, name);
  ensureDir(outDir);

  const results = [];

  const t0 = Date.now();
  const timeLeft = () => VIEWPORT_BUDGET_MS - (Date.now() - t0);

  const visit = async (label, urlPath) => {
    if (timeLeft() <= 0) return;
    const url = new URL(urlPath, BASE_URL).toString();
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const overflow = await checkHorizontalOverflow(page);
    const file = path.join(outDir, `${safeSlug(label)}.png`);
    await snapshotViewport(page, file);
    results.push({ label, url: page.url(), overflow, screenshot: path.relative(repoRoot, file) });
  };

  await login(page);

  // Crawl admin pages discovered from the nav/sidebar.
  await page.goto(new URL('/admin/', BASE_URL).toString(), { waitUntil: 'domcontentloaded' });
  const adminLinks = (await collectAdminLinks(page)).slice(0, MAX_ADMIN_PAGES);

  // Always include the dashboard.
  const allAdmin = Array.from(new Set([normalizeAdminUrl('/admin/'), ...adminLinks]));

  for (const u of allAdmin) {
    if (timeLeft() <= 0) break;

    await page.goto(u, { waitUntil: 'domcontentloaded' });
    const label = `admin_${new URL(u).pathname.replace(/\W+/g, '_').replace(/^_+|_+$/g, '')}`;
    const overflow = await checkHorizontalOverflow(page);
    const file = path.join(outDir, `${safeSlug(label)}.png`);
    await snapshotViewport(page, file);
    results.push({ label, url: page.url(), overflow, screenshot: path.relative(repoRoot, file) });

    // If it's a list page, open the first change page for additional coverage.
    await openFirstChangeIfPresent(page, outDir, label, results, /*takeFullPage*/ false);
  }

  // Also audit the two demo apply flows (desktop + mobile) with full-page screenshots.
  if (timeLeft() > 0) {
    const single = await auditApplyFlow({
      page,
      outDir,
      label: 'apply_enrollment_request_demo',
      startPath: '/schools/enrollment-request-demo/apply/',
    });
    results.push(single);
  }

  if (timeLeft() > 0) {
    const multi = await auditApplyFlow({
      page,
      outDir,
      label: 'apply_multi_form_demo',
      startPath: '/schools/multi-form-demo/apply/enrollment/',
    });
    results.push(multi);
  }

  await browser.close();
  return results;
}

async function main() {
  ensureDir(OUT_DIR);

  // Hard stop to avoid hanging.
  const hardTimeout = setTimeout(() => {
    // eslint-disable-next-line no-console
    console.error('layout_audit: hard timeout reached; exiting');
    process.exit(2);
  }, 60_000);

  try {
    const desktop = await runAuditForViewport('desktop', { width: 1280, height: 720 });
    const mobile = await runAuditForViewport('mobile', { width: 390, height: 844 });

    const out = {
      baseURL: BASE_URL,
      generatedAt: new Date().toISOString(),
      desktop,
      mobile,
    };

    const jsonPath = path.join(OUT_DIR, 'summary.json');
    fs.writeFileSync(jsonPath, JSON.stringify(out, null, 2));

    // eslint-disable-next-line no-console
    console.log(`layout_audit: wrote ${path.relative(repoRoot, jsonPath)}`);

    const summarize = (rows) =>
      rows
        .map((r) => {
          const flag = r.overflow?.overflow ? 'OVERFLOW' : 'ok';
          const yaml = r.yamlOverflow ? ` yaml(bad=${r.yamlOverflow.bad}/${r.yamlOverflow.checked},widget=${r.yamlOverflow.widget})` : '';
          return `- ${flag} ${r.label}${yaml} (${r.url})`; 
        })
        .join('\n');

    console.log('desktop:');
    console.log(summarize(desktop));
    console.log('mobile:');
    console.log(summarize(mobile));

    clearTimeout(hardTimeout);
    process.exit(0);
  } catch (e) {
    clearTimeout(hardTimeout);
    // eslint-disable-next-line no-console
    console.error(`layout_audit: failed: ${e?.message || e}`);
    process.exit(1);
  }
}

main();
