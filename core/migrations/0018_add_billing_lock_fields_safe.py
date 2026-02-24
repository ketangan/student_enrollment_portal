from django.db import migrations


class Migration(migrations.Migration):
    """
    Safe migration for production.
    
    Production may already have stripe_cancel_at, stripe_cancel_at_period_end,
    and stripe_current_period_end from an old migration 0013.
    This migration safely adds any missing billing lock fields.
    """
    
    dependencies = [
        ("core", "0017_school_billing_lock_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Add is_active if it doesn't exist
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='core_school' AND column_name='is_active'
                ) THEN
                    ALTER TABLE core_school ADD COLUMN is_active boolean DEFAULT true NOT NULL;
                END IF;
            END $$;
            
            -- Add stripe_cancel_at if it doesn't exist
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='core_school' AND column_name='stripe_cancel_at'
                ) THEN
                    ALTER TABLE core_school ADD COLUMN stripe_cancel_at timestamp with time zone NULL;
                END IF;
            END $$;
            
            -- Add stripe_cancel_at_period_end if it doesn't exist
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='core_school' AND column_name='stripe_cancel_at_period_end'
                ) THEN
                    ALTER TABLE core_school ADD COLUMN stripe_cancel_at_period_end boolean DEFAULT false NOT NULL;
                END IF;
            END $$;
            
            -- Add stripe_current_period_end if it doesn't exist
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='core_school' AND column_name='stripe_current_period_end'
                ) THEN
                    ALTER TABLE core_school ADD COLUMN stripe_current_period_end timestamp with time zone NULL;
                END IF;
            END $$;
            """,
            reverse_sql="""
            ALTER TABLE core_school DROP COLUMN IF EXISTS is_active;
            ALTER TABLE core_school DROP COLUMN IF EXISTS stripe_cancel_at;
            ALTER TABLE core_school DROP COLUMN IF EXISTS stripe_cancel_at_period_end;
            ALTER TABLE core_school DROP COLUMN IF EXISTS stripe_current_period_end;
            """,
        ),
    ]
