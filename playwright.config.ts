import { defineConfig, devices } from '@playwright/test';
import fs from 'fs';
import dotenv from 'dotenv';
import path from 'path';


if (fs.existsSync('.env.e2e')) {
  dotenv.config({ path: '.env.e2e' });
}

const baseURL = process.env.BASE_URL || 'http://127.0.0.1:8000';

function resolvePythonExecutable(): string {
  const repoRoot = process.cwd();
  const fromEnv = process.env.E2E_PYTHON;
  if (fromEnv) return path.isAbsolute(fromEnv) ? fromEnv : path.join(repoRoot, fromEnv);

  const venv = process.env.VIRTUAL_ENV;
  if (venv) {
    const venvPath = path.isAbsolute(venv) ? venv : path.join(repoRoot, venv);
    const candidate = path.join(venvPath, 'bin', 'python');
    if (fs.existsSync(candidate)) return candidate;
  }

  const localVenv = path.join(repoRoot, 'venv', 'bin', 'python');
  if (fs.existsSync(localVenv)) return localVenv;

  return 'python3';
}

function serverAddressFromBaseURL(raw: string): string {
  const u = new URL(raw);
  const host = u.hostname || '127.0.0.1';
  const port = u.port || '8000';
  return `${host}:${port}`;
}

function webServerReadyUrlFromBaseURL(raw: string): string {
  const u = new URL(raw);
  u.pathname = '/healthz/';
  u.search = '';
  u.hash = '';
  return u.toString();
}

export default defineConfig({
  testDir: 'tests/e2e',
  globalSetup: './tests/e2e/global-setup',
  webServer: {
    command: `${resolvePythonExecutable()} manage.py runserver ${serverAddressFromBaseURL(baseURL)}`,
    url: webServerReadyUrlFromBaseURL(baseURL),
    reuseExistingServer: true,
    timeout: 20_000,
  },
  timeout: 20_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report' }]],
  use: {
    baseURL,
    headless: true,
    viewport: { width: 1280, height: 720 },
    ignoreHTTPSErrors: true,
    actionTimeout: 10_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
