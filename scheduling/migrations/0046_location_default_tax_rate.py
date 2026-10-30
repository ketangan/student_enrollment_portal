from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0045_party_participant_estimate")]
    operations = [
        migrations.AddField(model_name="location", name="default_tax_rate",
            field=models.DecimalField(blank=True, null=True, max_digits=5, decimal_places=3,
                validators=[django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(Decimal("99.999"))])),
        migrations.AddConstraint(model_name="location", constraint=models.CheckConstraint(
            condition=models.Q(default_tax_rate__isnull=True) | models.Q(
                default_tax_rate__gte=0, default_tax_rate__lte=Decimal("99.999")),
            name="location_tax_rate_valid")),
    ]
