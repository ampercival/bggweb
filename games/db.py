"""Bounded bulk writes without SQLite's large CASE-expression overhead."""
from django.db import connection


def bulk_update(model, objects, fields):
    if not objects:
        return
    if connection.vendor != 'sqlite' or len(objects) < 1000:
        model.objects.bulk_update(objects, fields, batch_size=500)
        return
    # SQLite executes prepared parameterized updates in one transaction without
    # constructing thousands of Django CASE expressions. Use field preparation
    # for JSON, dates and booleans, and quote only trusted model metadata.
    quote = connection.ops.quote_name
    model_fields = [model._meta.get_field(name) for name in fields]
    assignments = ', '.join(f'{quote(field.column)} = %s' for field in model_fields)
    sql = (f'UPDATE {quote(model._meta.db_table)} SET {assignments} '
           f'WHERE {quote(model._meta.pk.column)} = %s')
    with connection.cursor() as cursor:
        cursor.executemany(sql, [
            [field.get_db_prep_save(getattr(obj, field.attname), connection)
             for field in model_fields] + [obj.pk]
            for obj in objects
        ])
