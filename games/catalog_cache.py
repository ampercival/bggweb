"""Small process-local caches keyed by the database's shared catalog revision."""
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from .models import CatalogRevision


def invalidate_catalog(source=None):
    revision, _ = CatalogRevision.objects.get_or_create(pk=1)
    updates = {'version': F('version') + 1}
    if source:
        timestamps = dict(revision.source_updated)
        timestamps[source] = timezone.now().isoformat()
        updates['source_updated'] = timestamps
    CatalogRevision.objects.filter(pk=1).update(**updates)


def catalog_version():
    return CatalogRevision.objects.filter(pk=1).values_list('version', flat=True).first() or 0


def cached_catalog(key, compute):
    if not settings.CATALOG_CACHE_ENABLED:
        return compute()
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, value, timeout=300)
    return value


def facet_key(version, params):
    basic = params.copy()
    for key in ['categories', 'mechanics', 'page', 'page_size', 'sort', 'dir']:
        basic.pop(key, None)
    digest = hashlib.sha256(basic.urlencode().encode()).hexdigest()
    return f'catalog:facets:{version}:{digest}'
