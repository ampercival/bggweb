from django.contrib import admin
from .catalog_cache import invalidate_catalog
from .models import Game, PlayerCountRecommendation, Collection, OwnedGame, FetchJob, Category, RTTGame, BGAGame


class CatalogAdmin(admin.ModelAdmin):
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        invalidate_catalog()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        invalidate_catalog()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        invalidate_catalog()


@admin.register(Game)
class GameAdmin(CatalogAdmin):
    list_display = ("bgg_id", "title", "type", "year", "avg_rating", "num_voters", "bgg_rank", "owned")
    search_fields = ("title", "bgg_id")
    list_filter = ("type", "owned")


@admin.register(PlayerCountRecommendation)
class PCRAdmin(CatalogAdmin):
    list_display = ("game", "count", "best_pct", "rec_pct", "notrec_pct", "vote_count")
    list_filter = ("count",)


@admin.register(Collection)
class CollectionAdmin(CatalogAdmin):
    list_display = ("username", "created_at")


@admin.register(OwnedGame)
class OwnedGameAdmin(CatalogAdmin):
    list_display = ("collection", "game")


@admin.register(FetchJob)
class FetchJobAdmin(CatalogAdmin):
    list_display = ("id", "kind", "status", "progress", "total", "created_at", "finished_at")
    readonly_fields = [field.name for field in FetchJob._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(CatalogAdmin):
    search_fields = ("name",)
    list_display = ("name",)


@admin.register(RTTGame)
class RTTGameAdmin(CatalogAdmin):
    list_display = ("bgg_id", "title", "slug", "updated_at")
    search_fields = ("title", "bgg_id", "slug")


@admin.register(BGAGame)
class BGAGameAdmin(CatalogAdmin):
    list_display = ("bgg_id", "title", "updated_at")
    search_fields = ("title", "bgg_id")
