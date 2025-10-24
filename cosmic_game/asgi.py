import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.sessions import SessionMiddlewareStack
import game_app.routing  # ИЗМЕНИТЬ на game_app

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cosmic_game.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": SessionMiddlewareStack(  # Используем SessionMiddleware вместо AuthMiddleware
        URLRouter(
            game_app.routing.websocket_urlpatterns
        )
    ),
})