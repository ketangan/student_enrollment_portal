import { execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';

const repoRoot = path.resolve(__dirname, '..', '..');

function resolvePythonExecutable(): string {
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

function runPython(args: string[]) {
  const python = resolvePythonExecutable();
  execFileSync(python, args, {
    stdio: 'inherit',
    cwd: repoRoot,
    env: process.env,
  });
}

export default async function globalSetup() {
  // Ensure schema is up-to-date for E2E.
  runPython(['manage.py', 'migrate', '--noinput']);

  const adminUser = process.env.ADMIN_USER;
  const adminPass = process.env.ADMIN_PASS;
  const schoolAdminUser = process.env.SCHOOL_ADMIN_USER;
  const schoolAdminPass = process.env.SCHOOL_ADMIN_PASS;

  // Seed one known school + the admin users used by E2E.
  // This command is designed to be idempotent-ish.
  if (adminUser && adminPass && schoolAdminUser && schoolAdminPass) {
    runPython([
      'manage.py',
      'seed_demo_data',
      '--school-slug',
      'dancemaker-studio',
      '--school-name',
      'Dancemaker Studio',
      '--submissions',
      '0',
      '--superuser-username',
      adminUser,
      '--superuser-password',
      adminPass,
      '--school-admin-username',
      schoolAdminUser,
      '--school-admin-password',
      schoolAdminPass,
    ]);

    // Ensure additional schools referenced by E2E exist without changing memberships.
    runPython([
      'manage.py',
      'shell',
      '-c',
      [
        'from core.models import School',
        "schools = ['kimberlas-classical-ballet', 'torrance-sister-city-association', 'multi-form-demo', 'enrollment-request-demo']",
        'for slug in schools:',
        "  School.objects.get_or_create(slug=slug, defaults={'display_name': slug, 'website_url': '', 'source_url': ''})",
      ].join('\n'),
    ]);

    // Upgrade all E2E schools to 'starter' plan so reports (and other gated features) are enabled.
    runPython([
      'manage.py',
      'shell',
      '-c',
      [
        'from core.models import School',
        "School.objects.filter(slug__in=['dancemaker-studio', 'kimberlas-classical-ballet', 'torrance-sister-city-association']).update(plan='starter')",
      ].join('\n'),
    ]);

    // multi-form-demo needs 'pro' plan for multi_form_enabled feature flag.
    runPython([
      'manage.py',
      'shell',
      '-c',
      [
        'from core.models import School',
        "School.objects.filter(slug='multi-form-demo').update(plan='pro')",
      ].join('\n'),
    ]);
  }
}
