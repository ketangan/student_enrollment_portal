from decimal import Decimal
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower


def preserve_existing_stock(apps, schema_editor):
    alias = schema_editor.connection.alias
    Variant = apps.get_model('scheduling', 'CatalogVariant')
    Material = apps.get_model('scheduling', 'Material')
    Recipe = apps.get_model('scheduling', 'MaterialRequirement')
    Level = apps.get_model('scheduling', 'StockLevel')
    Allocation = apps.get_model('scheduling', 'StockAllocation')
    Reservation = apps.get_model('scheduling', 'Reservation')
    # Preserve other tenants' existing kits, balances and reservation history.
    for variant in Variant.objects.using(alias).select_related('catalog').iterator():
        material = Material.objects.using(alias).create(profile_id=variant.catalog.profile_id,
            name=f'{variant.name[:140]} (legacy kit)', sku=f'LEGACY-{variant.pk}', unit='pieces', is_active=variant.is_active)
        Recipe.objects.using(alias).create(variant_id=variant.pk, material_id=material.pk, quantity=1)
        Level.objects.using(alias).filter(item_id=variant.pk).update(material_id=material.pk)
    for reservation in Reservation.objects.using(alias).filter(pk__in=Allocation.objects.using(alias).values('reservation_id')).iterator():
        allocations = list(Allocation.objects.using(alias).filter(reservation=reservation).select_related('level__material', 'level__item'))
        reservation.snapshot = {**reservation.snapshot, 'material_catalog_id': allocations[0].level.item.catalog_id,
            'material_location_id': allocations[0].level.location_id,
            'materials': [{'material_id': a.level.material_id, 'name': a.level.material.name,
                           'unit': a.level.material.unit, 'quantity': str(a.quantity)} for a in allocations]}
        reservation.save(using=alias, update_fields=['snapshot'])
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def check_legacy_rollback(apps, schema_editor):
    from django.db.migrations.exceptions import IrreversibleError
    alias = schema_editor.connection.alias
    Material = apps.get_model('scheduling', 'Material')
    Variant = apps.get_model('scheduling', 'CatalogVariant')
    Recipe = apps.get_model('scheduling', 'MaterialRequirement')
    for material in Material.objects.using(alias).iterator():
        recipes = list(Recipe.objects.using(alias).filter(material_id=material.pk))
        if material.unit != 'pieces' or len(recipes) != 1 or recipes[0].quantity != 1:
            raise IrreversibleError('Raw-material recipes cannot be converted back to finished-item stock. Restore the pre-migration backup instead.')
    for variant in Variant.objects.using(alias).iterator():
        if Recipe.objects.using(alias).filter(variant_id=variant.pk).count() != 1:
            raise IrreversibleError('A multi-material recipe cannot be rolled back to a single stock item.')
    for name, field in [('StockLevel', 'on_hand'), ('StockAllocation', 'quantity'), ('StockAdjustment', 'delta')]:
        for value in apps.get_model('scheduling', name).objects.using(alias).values_list(field, flat=True).iterator():
            if value != value.to_integral_value():
                raise IrreversibleError('Fractional material stock cannot be rolled back without losing data.')


def restore_existing_stock(apps, schema_editor):
    alias = schema_editor.connection.alias
    Level = apps.get_model('scheduling', 'StockLevel')
    Recipe = apps.get_model('scheduling', 'MaterialRequirement')
    for recipe in Recipe.objects.using(alias).iterator():
        Level.objects.using(alias).filter(material_id=recipe.material_id).update(item_id=recipe.variant_id)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):
    dependencies = [('scheduling', '0021_reservation_completed')]
    operations = [
        migrations.RenameModel(old_name='StockItem', new_name='CatalogVariant'),
        migrations.CreateModel(name='Material', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
            ('name', models.CharField(max_length=160)), ('sku', models.CharField(max_length=64)),
            ('unit', models.CharField(max_length=8, default='pieces', choices=[('pieces', 'Pieces'), ('m', 'Meters'), ('g', 'Grams'), ('ml', 'Milliliters')])),
            ('is_active', models.BooleanField(default=True)),
            ('profile', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='materials', to='scheduling.schedulingprofile')),
        ], options={'ordering': ['name', 'pk'], 'constraints': [models.UniqueConstraint(Lower('sku'), 'profile', name='material_sku_unique')]}),
        migrations.CreateModel(name='MaterialRequirement', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
            ('quantity', models.DecimalField(max_digits=10, decimal_places=3, validators=[django.core.validators.MinValueValidator(Decimal('.001')), django.core.validators.MaxValueValidator(1000000)])),
            ('material', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recipes', to='scheduling.material')),
            ('variant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='requirements', to='scheduling.catalogvariant')),
        ], options={'ordering': ['material__name', 'pk'], 'constraints': [models.UniqueConstraint(fields=['variant', 'material'], name='recipe_material_unique'), models.CheckConstraint(condition=models.Q(quantity__gt=0, quantity__lte=1000000), name='recipe_quantity_valid')]}),
        migrations.AddField(model_name='stocklevel', name='material', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='levels', to='scheduling.material')),
        migrations.AlterField(model_name='stocklevel', name='item', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='levels', to='scheduling.catalogvariant')),
        migrations.RunPython(preserve_existing_stock, restore_existing_stock),
        migrations.RemoveConstraint(model_name='stocklevel', name='inventory_stock_location_unique'),
        migrations.RemoveField(model_name='stocklevel', name='item'),
        migrations.AlterField(model_name='stocklevel', name='material', field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='levels', to='scheduling.material')),
        migrations.AddConstraint(model_name='stocklevel', constraint=models.UniqueConstraint(fields=['material', 'location'], name='material_stock_location_unique')),
        migrations.AlterField(model_name='stocklevel', name='on_hand', field=models.DecimalField(max_digits=10, decimal_places=3, default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1000000)])),
        migrations.RemoveConstraint(model_name='stockallocation', name='inventory_allocation_positive'),
        migrations.AlterField(model_name='stockallocation', name='quantity', field=models.DecimalField(max_digits=10, decimal_places=3, validators=[django.core.validators.MinValueValidator(Decimal('.001'))])),
        migrations.AddConstraint(model_name='stockallocation', constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0), name='inventory_allocation_positive')),
        migrations.AlterField(model_name='stockadjustment', name='delta', field=models.DecimalField(max_digits=10, decimal_places=3)),
        migrations.RunPython(migrations.RunPython.noop, check_legacy_rollback),
    ]
