from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('lobby/', views.game_lobby, name='game_lobby'),
    path('game/', views.game_room, name='game_room'),
    path('leave/<str:room_code>/', views.leave_game, name='leave_game'),
    path('rematch/<str:old_room_code>/', views.create_rematch, name='create_rematch'),
    path('support-api/', views.send_support_email, name='support_api'),
]