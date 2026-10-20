from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from games.models import CatalogLease, FetchJob


class Command(BaseCommand):
    help = 'Mark jobs without a heartbeat for 15 minutes as failed and release their writer lease.'

    def handle(self, **options):
        cutoff = timezone.now() - timedelta(minutes=15)
        with transaction.atomic():
            stale = FetchJob.objects.annotate(last_seen=Coalesce('heartbeat_at', 'created_at')).filter(
                status__in=['running', 'cancelling'], last_seen__lt=cutoff)
            ids = list(stale.values_list('pk', flat=True))
            stale.update(status='error', error='Worker stopped responding. Retry this job to restart safely.',
                         finished_at=timezone.now())
            CatalogLease.objects.filter(job_id__in=ids).update(job=None)
        self.stdout.write(f'Recovered {len(ids)} stale jobs.')
