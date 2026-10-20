import os
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError


def validate_ranks_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValidationError('Invalid ranks download URL.') from exc
    hosts = os.environ.get('BGG_RANKS_ALLOWED_HOSTS',
                           'boardgamegeek.com,geek-export-stats.s3.amazonaws.com').split(',')
    if (parsed.scheme != 'https' or parsed.hostname not in {host.strip() for host in hosts}
            or parsed.username or parsed.password or port not in (None, 443)):
        raise ValidationError('Use the HTTPS download link from the BGG ranks data-dump page.')
    if parsed.hostname == 'boardgamegeek.com' and parsed.path.rstrip('/') == '/data_dumps/bg_ranks':
        raise ValidationError('This is the BGG download page. Open it and copy the "Click to Download" link, then paste that link here.')


class ImportForm(forms.Form):
    action = forms.ChoiceField(choices=[(x, x) for x in ['top_n', 'refresh', 'collection', 'rtt', 'bga', 'add_user', 'delete_user']])
    n = forms.IntegerField(min_value=0, max_value=100000, required=False, initial=100)
    username = forms.RegexField(r'^[A-Za-z0-9_ .-]{1,100}$', required=False)
    family_id = forms.RegexField(r'^\d{1,20}$', required=False)
    zip_url = forms.URLField(max_length=4096, required=False, validators=[validate_ranks_url])

    def clean(self):
        data = super().clean()
        action = data.get('action')
        if action in ['top_n', 'refresh'] and not data.get('zip_url'):
            self.add_error('zip_url', 'A fresh ranks download link is required.')
        if action in ['collection', 'add_user', 'delete_user'] and not data.get('username'):
            self.add_error('username', 'A username is required.')
        if data.get('username') in ['Board Game Arena', 'Rally the Troops']:
            self.add_error('username', 'This name is reserved for an availability filter.')
        return data
