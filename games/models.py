from django.db import models


class Game(models.Model):
    bgg_id = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=[('Base Game', 'Base Game'), ('Expansion', 'Expansion')])
    year = models.CharField(max_length=10, null=True, blank=True)
    avg_rating = models.FloatField(null=True, blank=True)
    num_voters = models.IntegerField(null=True, blank=True)
    weight = models.FloatField(null=True, blank=True)
    weight_votes = models.IntegerField(null=True, blank=True)
    bgg_rank = models.IntegerField(null=True, blank=True)
    owned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # New: categories (many-to-many), store category names from BGG
    categories = models.ManyToManyField('Category', blank=True, related_name='games')
    # New: families (many-to-many), store BGG family groupings (e.g., Strategy, Thematic)
    families = models.ManyToManyField('Family', blank=True, related_name='games')

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


 


class PlayerCountRecommendation(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='player_counts')
    count = models.IntegerField()
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


class Collection(models.Model):
    username = models.CharField(max_length=100)
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
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='pending')  # pending, running, done, error
    progress = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Job({self.id}) {self.kind} {self.status}"
