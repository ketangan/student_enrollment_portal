from django.db import connection, migrations, models


def add_billing_lock_fields_if_missing(apps, schema_editor):
    """Add billing lock fields only if they don't exist (safe for production)."""
    with connection.cursor() as cursor:
        # Get existing columns for core_school table
        table_name = "core_school"
        existing_columns = set(
            row[0] for row in connection.introspection.get_table_description(cursor, table_name)
        )

        # Define columns to add
        columns_to_add = {
            "is_active": "boolean DEFAULT true NOT NULL",
            "stripe_cancel_at": "timestamp with time zone NULL" if connection.vendor == "postgresql" else "datetime NULL",
            "stripe_cancel_at_period_end": "boolean DEFAULT false NOT NULL",
            "stripe_current_period_end": "timestamp with time zone NULL" if connection.vendor == "postgresql" else "datetime NULL",
        }

        # Add each column if it doesn't exist
        for column_name, column_def in columns_to_add.items():
            if column_name not in existing_columns:
                sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
                cursor.execute(sql)


def remove_billing_lock_fields(apps, schema_editor):
    """No-op reverse operation.

    Migration 0017 owns these columns and handles removal when rolled back.
    Migration 0018 only adds missing columns for production safety.
    """
    pass


class Migration(migrations.Migration):
    """
    Safe migration for production.

    Production may already have stripe_cancel_at, stripe_cancel_at_period_end,
    and stripe_current_period_end from an old migration 0013.
    This migration safely adds any missing billing lock fields using Python
    code that works across PostgreSQL and SQLite.
    """

    dependencies = [
        ("core", "0017_school_billing_lock_fields"),
    ]

    operations = [
        migrations.RunPython(
            add_billing_lock_fields_if_missing,
            reverse_code=remove_billing_lock_fields,
        ),
    ]
