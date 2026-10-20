"""The existing queue runner with an idle heartbeat and stale-job recovery."""
from background_task.management.commands.process_tasks import Command as QueueCommand
from django.core.management import call_command
from django.utils import timezone

from games.models import CatalogRevision


class ReportingRunner:
    def __init__(self, runner):
        self.runner = runner

    def run_next_task(self, queue=None):
        CatalogRevision.objects.get_or_create(pk=1)
        CatalogRevision.objects.filter(pk=1).update(worker_seen_at=timezone.now())
        return self.runner.run_next_task(queue)


class Command(QueueCommand):
    help = 'Run the background queue with health reporting and stale-job recovery.'

    def handle(self, *args, **options):
        call_command('recover_jobs', stdout=self.stdout)
        self._tasks = ReportingRunner(self._tasks)
        return super().handle(*args, **options)
