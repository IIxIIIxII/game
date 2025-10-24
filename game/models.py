from django.db import models
from django.contrib.auth.models import User
import random
from django.utils import timezone
import datetime

class GameSession(models.Model):
    STAGE_CHOICES = [
        ('lobby', 'Лобби'),
        ('roles', 'Показ ролей'),
        ('coordinates', 'Показ координат'),
        ('voting', 'Голосование'),
        ('results', 'Результаты раунда'),
        ('game_over', 'Игра окончена'),
    ]
    
    GAME_OVER_STATUS = [
        ('playing', 'В игре'),
        ('aliens_win', 'Пришельцы победили'),
        ('humans_win', 'Люди победили'),
    ]

    room_code = models.CharField(max_length=10, unique=True, blank=True)
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hosted_games')
    current_stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='lobby')
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    
    human_coords_to_win = models.IntegerField(default=0)
    visited_human_coords_count = models.IntegerField(default=0)
    game_over_status = models.CharField(max_length=20, choices=GAME_OVER_STATUS, default='playing')

    def save(self, *args, **kwargs):
        if not self.room_code:
            self.room_code = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=5))
        super().save(*args, **kwargs)

    def is_expired(self):
        if self.current_stage == 'game_over':
            return timezone.now() - self.last_activity > datetime.timedelta(seconds=45)
        return False

    def __str__(self):
        return f"Игра {self.room_code} (Ведущий: {self.host.username})"

class PlayerInGame(models.Model):
    ROLE_CHOICES = [
        ('human', 'Человек'),
        ('alien', 'Пришелец'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='players')
    nickname = models.CharField(max_length=50)
    avatar_id = models.CharField(max_length=20, default='1')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, blank=True, null=True)
    is_alive = models.BooleanField(default=True)
    has_voted = models.BooleanField(default=False)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'game')

    def is_expired(self):
        if self.game.current_stage == 'lobby':
            return timezone.now() - self.last_activity > datetime.timedelta(seconds=45)
        return False

    def __str__(self):
        return f"{self.nickname} в игре {self.game.room_code}"

class GameCoordinate(models.Model):
    game = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='coordinates')
    player = models.ForeignKey(PlayerInGame, on_delete=models.CASCADE, related_name='coordinate_card')
    coordinate_name = models.CharField(max_length=100)
    resource_description = models.CharField(max_length=200)
    is_alien_coord = models.BooleanField(default=False)
    was_visited = models.BooleanField(default=False)
    votes = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.coordinate_name} ({self.player.nickname}) в игре {self.game.room_code}"