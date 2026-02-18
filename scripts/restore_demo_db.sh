#!/usr/bin/env bash
# Restore the demo database by running migrations and seeding demo data.
# To run:
#   chmod +x scripts/restore_demo_db.sh
#   ./scripts/restore_demo_db.sh
set -euo pipefail

echo "==> migrate"
python manage.py migrate

echo "==> seed demo data: Enrollment Request Demo"
python manage.py seed_demo_data \
  --skip-superuser \
  --school-admin-username demo_admin \
  --school-admin-email kg.ketan@gmail.com \
  --school-admin-password "Demo@12345" \
  --school-slug "enrollment-request-demo" \
  --school-name "Enrollment Request Demo" \
  --submissions 15

echo "==> seed demo data: Multi-Form Demo"
python manage.py seed_demo_data \
  --skip-superuser \
  --school-admin-username multi_form_admin \
  --school-admin-email kg.ketan@gmail.com \
  --school-admin-password "MultiForm@12345" \
  --school-slug "multi-form-demo" \
  --school-name "Multi-Form Demo" \
  --submissions 15

echo "==> done"