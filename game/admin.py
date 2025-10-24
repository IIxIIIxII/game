from django.contrib import admin
from .models import GameSession, PlayerInGame, GameCoordinate

@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ('room_code', 'host', 'current_stage', 'created_at', 'game_over_status')
    list_filter = ('current_stage', 'game_over_status')
    search_fields = ('room_code', 'host__username')

@admin.register(PlayerInGame)
class PlayerInGameAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'game', 'user', 'role', 'avatar_id', 'is_alive')
    list_filter = ('game', 'role', 'is_alive')
    search_fields = ('nickname', 'user__username')

@admin.register(GameCoordinate)
class GameCoordinateAdmin(admin.ModelAdmin):
    list_display = ('coordinate_name', 'player', 'game', 'is_alien_coord', 'was_visited')
    list_filter = ('game', 'is_alien_coord', 'was_visited')
    search_fields = ('coordinate_name', 'player__nickname')