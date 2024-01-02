# Generated by Django 2.2.28 on 2023-05-19 17:25
import logging

from django.db import migrations
from django.db.utils import DatabaseError

from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils.query import RangeQuerySetWrapperWithProgressBarApprox


def _backfill(apps, schema_editor):
    cls = apps.get_model("sentry", "Activity")

    for obj in RangeQuerySetWrapperWithProgressBarApprox(cls.objects.all()):
        # load pickle, save json
        try:
            obj.save(update_fields=["data"])
        except DatabaseError as e:
            logging.warning("ignoring save error (row was likely deleted): %s", e)


class Migration(CheckedMigration):
    # data migration: must be run out of band
    is_dangerous = True

    # data migration: run outside of a transaction
    atomic = False

    dependencies = [
        ("sentry", "0492_pickle_to_json_sentry_groupedmessage"),
    ]

    operations = [
        migrations.RunPython(
            _backfill,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_activity"]},
        ),
    ]
