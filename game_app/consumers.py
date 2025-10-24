import json
import random
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from .models import GameSession, Player, GameCoordinate
from django.urls import reverse

class GameConsumer(WebsocketConsumer):
    
    def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.room_group_name = f'game_{self.room_code}'
        
        # Получаем сессию
        session = self.scope['session']
        self.session_id = session.get('session_id')
        self.nickname = session.get('nickname', 'Игрок')

        if not self.session_id:
            self.close()
            return

        async_to_sync(self.channel_layer.group_add)(
            self.room_group_name,
            self.channel_name
        )

        self.accept()
        
        # Уведомляем о подключении (только для игроков, не для ведущего)
        game = GameSession.objects.filter(room_code=self.room_code).first()
        if game and self.session_id != game.host_session:
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': f'{self.nickname} присоединился к комнате.',
                    'username': 'Система'
                }
            )

    def disconnect(self, close_code):
        game = GameSession.objects.filter(room_code=self.room_code).first()
        if not game:
            return

        # Уведомляем об отключении (только для игроков)
        if self.session_id != game.host_session:
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': f'{self.nickname} покинул комнату.',
                    'username': 'Система'
                }
            )

        # Если отключается игрок (не ведущий), удаляем его из игры
        if self.session_id != game.host_session and game.current_stage == 'lobby':
            player = Player.objects.filter(session_id=self.session_id, game=game).first()
            if player:
                player.delete()
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {'type': 'player_list_update'}
                )

        async_to_sync(self.channel_layer.group_discard)(
            self.room_group_name,
            self.channel_name
        )

    def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message_type = text_data_json.get('type')

        game = GameSession.objects.filter(room_code=self.room_code).first()
        if not game:
            return

        if message_type == 'chat_message':
            message = text_data_json.get('message', '')
            if message:
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message': message,
                        'username': self.nickname
                    }
                )
        
        elif message_type == 'refresh_players':
            # Отправляем актуальный список игроков
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                {'type': 'player_list_update'}
            )
        
        elif self.session_id == game.host_session:
            if message_type == 'start_game' and game.current_stage == 'lobby':
                self.start_game_logic(game)
            
            elif message_type == 'next_stage':
                if game.current_stage == 'roles':
                    self.generate_coordinates_logic(game)
                elif game.current_stage == 'coordinates':
                    self.start_voting_logic(game)
                elif game.current_stage == 'voting':
                    self.process_results_logic(game)
                elif game.current_stage == 'results':
                    self.generate_coordinates_logic(game)

        elif message_type == 'submit_vote' and game.current_stage == 'voting':
            try:
                player = Player.objects.get(session_id=self.session_id, game=game)
                if player.has_voted:
                    return
                
                coordinate_id = text_data_json.get('coordinate_id')
                coord = GameCoordinate.objects.get(id=coordinate_id, game=game)
                
                coord.votes += 1
                coord.save()
                
                player.has_voted = True
                player.save()
                
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {'type': 'update_game_stage'}
                )
            except (Player.DoesNotExist, GameCoordinate.DoesNotExist):
                pass

    def start_game_logic(self, game):
        players = list(game.players.all())  # Только обычные игроки
        player_count = len(players)

        if player_count < 3 or game.current_stage != 'lobby':
            return

        random.shuffle(players)
        
        alien_count = 1 if player_count < 5 else 2
        human_count = player_count - alien_count
        
        for i, player in enumerate(players):
            if i < alien_count:
                player.role = 'alien'
            else:
                player.role = 'human'
            player.save()

        game.human_coords_to_win = human_count - 1
        game.visited_human_coords_count = 0
        game.current_stage = 'roles'
        game.save()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'game_started'
            }
        )
