from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from game.models import GameSession, PlayerInGame

class Command(BaseCommand):
    help = 'Удаляет просроченные игры и игроков'

    def handle(self, *args, **options):
        expired_players = PlayerInGame.objects.filter(
            game__current_stage='lobby',
            last_activity__lt=timezone.now() - datetime.timedelta(seconds=45)
        )
        player_count = expired_players.count()
        expired_players.delete()
        
        expired_games = GameSession.objects.filter(
            current_stage='game_over',
            last_activity__lt=timezone.now() - datetime.timedelta(seconds=45)
        )
        game_count = expired_games.count()
        expired_games.delete()
        
        self.stdout.write(
            self.style.SUCCESS(f'Удалено {player_count} игроков и {game_count} игр')
        )