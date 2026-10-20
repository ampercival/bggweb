"""Application job state and a single catalog-writer lease."""
import time
from functools import wraps

from django.db import transaction
from django.utils import timezone

from .models import CatalogLease, FetchJob

TERMINAL = ('done', 'error', 'cancelled')


class JobCancelled(Exception):
    pass


def check_job(job):
    status = FetchJob.objects.filter(pk=job.pk).values_list('status', flat=True).first()
    if status != 'running':
        raise JobCancelled()
    now = time.monotonic()
    if now - getattr(job, '_last_heartbeat', 0) >= 10:
        FetchJob.objects.filter(pk=job.pk, status='running').update(heartbeat_at=timezone.now())
        job._last_heartbeat = now


def managed_job(function):
    @wraps(function)
    def run(job_id, *args, **kwargs):
        with transaction.atomic():
            job = FetchJob.objects.filter(pk=job_id).first()
            if job is None or job.status in TERMINAL:
                return
            if job.status == 'cancelling':
                FetchJob.objects.filter(pk=job_id, status='cancelling').update(
                    status='cancelled', finished_at=timezone.now())
                return
            if job.status != 'pending':
                return  # Duplicate delivery must never execute twice.
            CatalogLease.objects.get_or_create(pk=1)
            if not CatalogLease.objects.filter(pk=1, job__isnull=True).update(job_id=job_id):
                # Raised before claiming: the queue can retry after the writer finishes.
                raise RuntimeError('Another catalog job is active; waiting for its lease.')
            claimed = FetchJob.objects.filter(pk=job_id, status='pending').update(
                status='running', heartbeat_at=timezone.now(), error=None)
            if not claimed:
                CatalogLease.objects.filter(pk=1, job_id=job_id).update(job=None)
                return
        try:
            return function(job_id, *args, **kwargs)
        except Exception as exc:
            FetchJob.objects.filter(pk=job_id, status__in=['running', 'cancelling']).update(
                status='error', error=str(exc), finished_at=timezone.now())
            raise
        finally:
            completed = FetchJob.objects.filter(pk=job_id, status='done').first()
            if completed:
                from .catalog_cache import invalidate_catalog
                invalidate_catalog(source=completed.kind)
            CatalogLease.objects.filter(pk=1, job_id=job_id).update(job=None)
    return run


def enqueue(job):
    """Queue by stored, validated parameters; called in the job creation transaction."""
    from . import tasks
    p = job.params
    if job.kind == 'top_n':
        tasks.run_fetch_top_n(job.pk, p['n'], p.get('zip_url'))
    elif job.kind == 'collection':
        tasks.run_fetch_collection(job.pk, p['username'])
    elif job.kind == 'refresh':
        tasks.run_refresh(job.pk, p['n'], p['usernames'], p.get('batch_size', 20), p.get('zip_url'))
    elif job.kind == 'rtt':
        tasks.run_scrape_rtt(job.pk)
    elif job.kind == 'bga':
        tasks.run_fetch_bga(job.pk, p.get('family_id'))
