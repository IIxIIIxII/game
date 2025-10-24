from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('create_game/', views.create_game, name='create_game'),
    path('join_game/', views.join_game, name='join_game'),
    path('lobby/<str:room_code>/', views.game_lobby, name='game_lobby'),
    path('game/<str:room_code>/', views.game_room, name='game_room'),
    path('continue_without_player/<int:player_id>/', views.continue_without_player, name='continue_without_player'),
]