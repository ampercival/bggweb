from django.db import models


class Game(models.Model):
    bgg_id = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=[('Base Game', 'Base Game'), ('Expansion', 'Expansion')], db_index=True)
    year = models.IntegerField(null=True, blank=True, db_index=True)
    avg_rating = models.FloatField(null=True, blank=True, db_index=True)
    num_voters = models.IntegerField(null=True, blank=True)
    weight = models.FloatField(null=True, blank=True, db_index=True)
    weight_votes = models.IntegerField(null=True, blank=True)
    bgg_rank = models.IntegerField(null=True, blank=True, db_index=True)
    owned = models.BooleanField(default=False, db_index=True)
    owned_by = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    categories = models.ManyToManyField('Category', blank=True, related_name='games')
    families = models.ManyToManyField('Family', blank=True, related_name='games')
    mechanics = models.ManyToManyField('Mechanic', blank=True, related_name='games')


    def __str__(self):
        return f"{self.title} ({self.bgg_id})"


class Category(models.Model):
    name = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class Family(models.Model):
    name = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class Mechanic(models.Model):
    name = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class BGGUser(models.Model):
    username = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['username']

    def __str__(self):
        return self.username


class PlayerCountRecommendation(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='player_counts')
    count = models.IntegerField(db_index=True)
    best_pct = models.FloatField(default=0.0)
    best_votes = models.IntegerField(default=0)
    rec_pct = models.FloatField(default=0.0)
    rec_votes = models.IntegerField(default=0)
    notrec_pct = models.FloatField(default=0.0)
    notrec_votes = models.IntegerField(default=0)
    vote_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ('game', 'count')

    def __str__(self):
        return f"{self.game.title} - {self.count}p"


class RTTGame(models.Model):
    """A game available to play on Rally the Troops (rally-the-troops.com).

    Source of truth for the "Rally the Troops" availability tag, keyed by the
    game's BGG id so it can be matched to :class:`Game`. Kept separate from the
    catalog so a scraped game is remembered even when it is not currently in the
    catalog (outside the Top N and unowned); it gets tagged if/when a later
    refresh pulls that game in. ``Game.bgg_id`` is a string, so ``bgg_id`` here
    is a string too.
    """
    bgg_id = models.CharField(max_length=20, unique=True)
    slug = models.CharField(max_length=200, blank=True)
    title = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return f"RTTGame({self.title or self.slug} / {self.bgg_id})"


class Collection(models.Model):
    username = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    games = models.ManyToManyField(Game, through='OwnedGame')

    def __str__(self):
        return f"Collection({self.username})"


class OwnedGame(models.Model):
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)


class FetchJob(models.Model):
    KIND_CHOICES = [
        ('top_n', 'Top N'),
        ('collection', 'Collection'),
        ('refresh', 'Refresh'),
        ('rtt', 'Rally the Troops'),
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='pending')
    progress = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Job({self.id}) {self.kind} {self.status}"