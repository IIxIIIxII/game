from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('lobby/<str:room_code>/', views.game_lobby, name='game_lobby'),
    path('game/<str:room_code>/', views.game_room, name='game_room'),
]