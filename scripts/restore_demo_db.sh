#!/usr/bin/env bash
# Restore the demo database by running migrations and seeding demo data.
# To run - chmod +x scripts/restore_demo_db.sh
#     ./scripts/restore_demo_db.sh
set -euo pipefail

echo "==> migrate"
python manage.py migrate

echo "==> seed demo data"
python manage.py seed_demo_data \
  --superuser-username admin \
  --superuser-email admin@example.com \
  --superuser-password "admin12345" \
  --school-admin-username schooladmin \
  --school-admin-email schooladmin@example.com \
  --school-admin-password "schooladmin12345" \
  --school-slug "enrollment-request-demo" \
  --school-name "Enrollment Request Demo" \
  --submissions 15

echo "==> done"
