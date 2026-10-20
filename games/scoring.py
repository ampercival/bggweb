"""Published recommendation weights; keep product explanations in sync."""
from django.db.models import ExpressionWrapper, F, FloatField, Value

BEST_WEIGHT = 3.0
RECOMMENDED_WEIGHT = 2.0
NOT_RECOMMENDED_WEIGHT = -2.0
RATING_WEIGHT = 3.0
PLAYABLE_THRESHOLD = 150


def raw_player_score():
    return ExpressionWrapper(F('best_pct_c') * Value(BEST_WEIGHT)
                             + F('rec_pct_c') * Value(RECOMMENDED_WEIGHT)
                             + F('not_pct_c') * Value(NOT_RECOMMENDED_WEIGHT), output_field=FloatField())


def normalized_score(raw, low, width):
    return (raw - low) / width * 10.0 if width else 0.0
