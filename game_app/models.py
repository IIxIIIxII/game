from django.db import models
import random
import string
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
    host_session = models.CharField(max_length=100)
    current_stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='lobby')
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    human_coords_to_win = models.IntegerField(default=0)
    visited_human_coords_count = models.IntegerField(default=0)
    game_over_status = models.CharField(max_length=20, choices=GAME_OVER_STATUS, default='playing')

    def save(self, *args, **kwargs):
        if not self.room_code:
            self.room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Игра {self.room_code}"

class Player(models.Model):
    ROLE_CHOICES = [
        ('human', 'Человек'),
        ('alien', 'Пришелец'),
        ('scientist', 'Ученый'), 
    ]

    session_id = models.CharField(max_length=100)
    game = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='players')
    nickname = models.CharField(max_length=50)
    avatar_id = models.CharField(max_length=20, default='1')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, blank=True, null=True)

    is_alive = models.BooleanField(default=True)
    has_voted = models.BooleanField(default=False)
    special_used = models.BooleanField(default=False)

    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('session_id', 'game')
    def __str__(self):
        role_display = self.get_role_display() if self.role else "Без роли"
        return f"{self.nickname} ({role_display}) в игре {self.game.room_code}"

class GameCoordinate(models.Model):
    game = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='coordinates')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='coordinate_card')
    coordinate_name = models.CharField(max_length=100)
    resource_description = models.CharField(max_length=200)
    is_alien_coord = models.BooleanField(default=False)
    was_visited = models.BooleanField(default=False)
    votes = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.coordinate_name} ({self.player.nickname}) в игре {self.game.room_code}"