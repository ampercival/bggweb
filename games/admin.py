from django.contrib import admin
from .models import Game, PlayerCountRecommendation, Collection, OwnedGame, FetchJob, Category


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("bgg_id", "title", "type", "year", "avg_rating", "num_voters", "bgg_rank", "owned")
    search_fields = ("title", "bgg_id")
    list_filter = ("type", "owned")


@admin.register(PlayerCountRecommendation)
class PCRAdmin(admin.ModelAdmin):
    list_display = ("game", "count", "best_pct", "rec_pct", "notrec_pct", "vote_count")
    list_filter = ("count",)


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("username", "created_at")


@admin.register(OwnedGame)
class OwnedGameAdmin(admin.ModelAdmin):
    list_display = ("collection", "game")


@admin.register(FetchJob)
class FetchJobAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status", "progress", "total", "created_at", "finished_at")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name",)
