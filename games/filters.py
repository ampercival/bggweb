"""Canonical, bounded filters shared by page, AJAX and exports."""
import math
from datetime import datetime

from django.http import QueryDict

from .catalog import SORTABLE


def normalize_filters(raw):
    result = QueryDict(mutable=True)
    for key, allowed, default in [
        ('type', ['base', 'expansion', 'all'], 'base'),
        ('playable', ['playable', 'not', 'all'], 'playable'),
        ('player_count', ['all', '8plus'] + [str(i) for i in range(1, 101)], 'all'),
        ('sort', SORTABLE, 'score_factor'), ('dir', ['asc', 'desc'], 'desc'),
    ]:
        value = raw.get(key, default)
        result[key] = value if value in allowed else default
    for key, default, lower, upper, integer in [
        ('page', 1, 1, 1000000, True), ('page_size', 50, 1, 1000, True),
        ('min_year', 1900, -5000, 3000, True), ('max_year', datetime.now().year, -5000, 3000, True),
        ('min_avg_rating', 0, 0, 10, False), ('max_avg_rating', 10, 0, 10, False),
        ('min_weight', 0, 0, 5, False), ('max_weight', 5, 0, 5, False),
        ('min_voters', 0, 0, 100000000, True), ('min_poll_votes', 0, 0, 100000000, True),
    ]:
        try:
            value = float(raw.get(key, default))
            if not math.isfinite(value):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            value = default
        value = max(lower, min(value, upper))
        result[key] = str(int(value) if integer else float(value))
    for lower, upper in [('min_year', 'max_year'), ('min_avg_rating', 'max_avg_rating'), ('min_weight', 'max_weight')]:
        if float(result[lower]) > float(result[upper]):
            result[lower], result[upper] = result[upper], result[lower]
    result['q'] = raw.get('q', '').strip()[:200]
    for key in ['owners', 'categories', 'mechanics']:
        result.setlist(key, sorted({value.strip()[:200] for value in raw.getlist(key)[:200] if value.strip()}))
    return result
