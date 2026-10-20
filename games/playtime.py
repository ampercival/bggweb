"""BGG durations are minutes; zero and malformed values mean unknown."""


def positive_minutes(value):
    try:
        minutes = int(str(value))
    except (TypeError, ValueError):
        return None
    return minutes if 0 < minutes <= 2147483647 else None


def playtime_fields(detail):
    values = {field: positive_minutes(detail.get(key)) for field, key in [
        ('playing_time', 'Playing Time'), ('min_playtime', 'Min Playtime'),
        ('max_playtime', 'Max Playtime'),
    ]}
    if (values['min_playtime'] is not None and values['max_playtime'] is not None
            and values['min_playtime'] > values['max_playtime']):
        values['min_playtime'] = values['max_playtime'] = None
    single = values['playing_time']
    if single is not None and (
            (values['min_playtime'] is not None and single < values['min_playtime'])
            or (values['max_playtime'] is not None and single > values['max_playtime'])):
        values['playing_time'] = None
    return values
