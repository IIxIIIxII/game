from django.contrib import admin
from .models import GameSession, Player, GameCoordinate  # ИЗМЕНИТЬ PlayerInGame на Player

@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ('room_code', 'host_session', 'current_stage', 'created_at', 'game_over_status')
    list_filter = ('current_stage', 'game_over_status')
    search_fields = ('room_code', 'host_session')

@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'game', 'session_id', 'role', 'avatar_id', 'is_alive')
    list_filter = ('game', 'role', 'is_alive')
    search_fields = ('nickname', 'session_id')

@admin.register(GameCoordinate)
class GameCoordinateAdmin(admin.ModelAdmin):
    list_display = ('coordinate_name', 'player', 'game', 'is_alien_coord', 'was_visited')
    list_filter = ('game', 'is_alien_coord', 'was_visited')
    search_fields = ('coordinate_name', 'player__nickname')