from django.db import connection, migrations


def add_billing_lock_fields(apps, schema_editor):
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
    """Remove billing lock fields (reverse operation)."""
    with connection.cursor() as cursor:
        table_name = "core_school"
        columns_to_drop = [
            "is_active",
            "stripe_cancel_at",
            "stripe_cancel_at_period_end",
            "stripe_current_period_end",
        ]

        if connection.vendor == "postgresql":
            # PostgreSQL supports DROP COLUMN IF EXISTS
            for column_name in columns_to_drop:
                cursor.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS {column_name}")
        else:
            # For other databases, check first
            existing_columns = set(
                row[0] for row in connection.introspection.get_table_description(cursor, table_name)
            )
            for column_name in columns_to_drop:
                if column_name in existing_columns:
                    cursor.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")


class Migration(migrations.Migration):
    """
    Add billing lock fields to School model.

    Safe for production: checks if columns exist before adding them.
    Production may already have stripe_cancel_at, stripe_cancel_at_period_end,
    and stripe_current_period_end from an old migration 0013 that was removed.
    """

    dependencies = [
        ("core", "0016_add_stripe_fields_to_school"),
    ]

    operations = [
        migrations.RunPython(
            add_billing_lock_fields,
            reverse_code=remove_billing_lock_fields,
        ),
    ]
