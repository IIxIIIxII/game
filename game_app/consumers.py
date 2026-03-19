import json
import random
import time
import string
import traceback
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from .models import GameSession, Player, GameCoordinate
from django.urls import reverse
from django.db.models import F
from django.db import transaction

class GameConsumer(WebsocketConsumer):
    
    def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.room_group_name = f'game_{self.room_code}'
        
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
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'player_list_update'}
        )

    def disconnect(self, close_code):
        async_to_sync(self.channel_layer.group_discard)(self.room_group_name, self.channel_name)
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'player_list_update'}
        )

    def safe_send(self, data):
        try:
            self.send(text_data=json.dumps(data))
        except Exception:
            pass

    def receive(self, text_data):
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type')
            
            try:
                game = GameSession.objects.filter(room_code=self.room_code).first()
            except Exception:
                return 

            if not game: return

            if message_type == 'chat_message':
                message = text_data_json.get('message', '')
                if message:
                    async_to_sync(self.channel_layer.group_send)(
                        self.room_group_name,
                        { 'type': 'chat_message', 'message': message, 'username': self.nickname }
                    )
            
            elif message_type == 'update_profile':
                new_nickname = text_data_json.get('nickname')
                new_avatar_id = text_data_json.get('avatar_id')
                if new_nickname and new_avatar_id:
                    with transaction.atomic():
                        player = Player.objects.filter(session_id=self.session_id, game=game).first()
                        if player:
                            player.nickname = new_nickname
                            player.avatar_id = new_avatar_id
                            player.save()
                            
                            self.nickname = new_nickname
                            self.scope['session']['nickname'] = new_nickname
                            self.scope['session'].save()
                            
                            async_to_sync(self.channel_layer.group_send)(
                                self.room_group_name, {'type': 'player_list_update'}
                            )

            elif message_type == 'use_ability':
                player = Player.objects.filter(session_id=self.session_id, game=game).first()
                if player and player.role == 'scientist' and not player.special_used:
                    target_id = text_data_json.get('target_id')
                    target = Player.objects.filter(id=target_id, game=game).first()
                    if target and target.session_id != self.session_id:
                        player.special_used = True
                        player.save()
                        self.safe_send({
                            'type': 'ability_result',
                            'target_id': target.id,
                            'target_nickname': target.nickname,
                            'is_alien': target.role == 'alien'
                        })

            elif self.session_id == game.host_session:
                if message_type == 'start_game' and game.current_stage == 'lobby':
                    self.start_game_logic(game)
                elif message_type == 'next_stage':
                    time.sleep(0.05) 
                    if game.current_stage == 'roles': self.generate_coordinates_logic(game)
                    elif game.current_stage == 'coordinates': self.start_voting_logic(game)
                    elif game.current_stage == 'voting': self.process_results_logic(game)
                    elif game.current_stage == 'results': self.generate_coordinates_logic(game)
                
                elif message_type == 'create_rematch':
                    self.create_rematch_logic(game)
                
                elif message_type == 'disband_room':
                    async_to_sync(self.channel_layer.group_send)(
                        self.room_group_name,
                        { 'type': 'room_disbanded' }
                    )
                    game.delete()
                
                elif message_type == 'kick_player':
                    target_id = text_data_json.get('player_id')
                    try:
                        target = Player.objects.get(id=target_id, game=game)
                        # Запоминаем session_id перед удалением, чтобы кикнуть конкретного игрока
                        target_session_id = target.session_id 
                        target.delete()
                        
                        async_to_sync(self.channel_layer.group_send)(
                            self.room_group_name, {'type': 'player_list_update'}
                        )
                        
                        async_to_sync(self.channel_layer.group_send)(
                            self.room_group_name, 
                            {'type': 'player_kicked_event', 'kicked_session_id': target_session_id}
                        )
                    except Player.DoesNotExist:
                        pass

            elif message_type == 'submit_vote' and game.current_stage == 'voting':
                with transaction.atomic():
                    player = Player.objects.filter(session_id=self.session_id, game=game).first()
                    if player and not player.has_voted:
                        coordinate_id = text_data_json.get('coordinate_id')
                        coord = GameCoordinate.objects.select_for_update().get(id=coordinate_id, game=game)
                        
                        # Нельзя голосовать в себя
                        if coord.player == player: return 
                        # Нельзя голосовать в уже посещенную координату
                        if coord.was_visited: return

                        coord.votes += 1
                        coord.save()
                        player.has_voted = True
                        player.save()
                        
                        async_to_sync(self.channel_layer.group_send)(
                            self.room_group_name, {'type': 'update_game_stage'}
                        )

        except Exception as e:
            print(f"Server Error: {e}")

    # --- ЛОГИКА ---
    
    def start_game_logic(self, game):
        with transaction.atomic():
            GameCoordinate.objects.filter(game=game).delete()
            players = list(game.players.all())
            if len(players) < 3: return

            random.shuffle(players)
            alien_count = 2 if len(players) >= 5 else 1
            game.players.all().update(special_used=False)

            for i, player in enumerate(players):
                if i < alien_count: player.role = 'alien'
                elif i == alien_count and len(players) >= 6: player.role = 'scientist'
                else: player.role = 'human'
                player.save()

            game.human_coords_to_win = max(1, len(players) - alien_count - 1)
            game.visited_human_coords_count = 0
            game.current_stage = 'roles'
            game.save()

            COORD_NAMES = ["Сектор Альфа", "Туманность Ориона", "Пояс Койпера", "Галактика M-31", "Звезда Кеплера", "Астероид", "Система Траппист", "Планета Глизе", "Черная дыра", "Марс", "Венера", "Юпитер", "Сатурн", "Сектор Омега", "Пустошь"]
            random.shuffle(COORD_NAMES)
            
            for i, player in enumerate(players):
                name = COORD_NAMES.pop(0) if COORD_NAMES else f"Сектор {i}"
                GameCoordinate.objects.create(
                    game=game, player=player, coordinate_name=name,
                    resource_description="Ожидание данных...", is_alien_coord=False, was_visited=False
                )

        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name, {'type': 'game_started'}
        )
        
    def generate_coordinates_logic(self, game):
        with transaction.atomic():
            HUMAN_RESOURCES = ["Запасы Топлива", "Партия Репликаторов", "Медикаменты", "Двигатель", "Кислород", "Гидропоника", "Батареи", "Инструменты", "Вода"]
            ALIEN_MESSAGE = "Это ловушка."
            random.shuffle(HUMAN_RESOURCES)
            
            coords = GameCoordinate.objects.filter(game=game).select_related('player')
            
            for coord in coords:
                if coord.was_visited:
                    coord.resource_description = "[ИССЛЕДОВАНО]"
                else:
                    is_alien = (coord.player.role == 'alien')
                    desc = ALIEN_MESSAGE if is_alien else (HUMAN_RESOURCES.pop(0) if HUMAN_RESOURCES else "Пусто")
                    coord.resource_description = desc
                    coord.is_alien_coord = is_alien
                
                coord.votes = 0 
                coord.save()
            
            game.current_stage = 'coordinates'
            game.save()
            
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    def start_voting_logic(self, game):
        with transaction.atomic():
            game.current_stage = 'voting'
            game.save()
            game.players.all().update(has_voted=False)
            game.coordinates.all().update(votes=0)
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    def process_results_logic(self, game):
        with transaction.atomic():
            winner = game.coordinates.order_by('-votes').first()
            if not winner or winner.votes == 0:
                pass
            else:
                winner.was_visited = True
                winner.save()
                if winner.is_alien_coord:
                    game.current_stage = 'game_over'
                    game.game_over_status = 'aliens_win'
                else:
                    game.visited_human_coords_count = F('visited_human_coords_count') + 1
                    game.save()
                    game.refresh_from_db()
                    if game.visited_human_coords_count >= game.human_coords_to_win:
                        game.current_stage = 'game_over'
                        game.game_over_status = 'humans_win'
                    else:
                        game.current_stage = 'results'
                game.save()

        if not winner or winner.votes == 0:
            self.generate_coordinates_logic(game)
        else:
            async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    def create_rematch_logic(self, old_game):
        new_url = None
        try:
            with transaction.atomic():
                new_room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                new_game = GameSession.objects.create(
                    host_session=old_game.host_session,
                    room_code=new_room_code,
                    current_stage='lobby'
                )
                
                active_players = Player.objects.filter(game=old_game).exclude(session_id__startswith='LEFT-')
                
                for player in active_players:
                    player.game = new_game
                    player.role = None
                    player.has_voted = False
                    player.special_used = False
                    player.save()
                
                old_game.delete()
                
                new_url = reverse('game_lobby', kwargs={'room_code': new_room_code})
            
            if new_url:
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name, 
                    {'type': 'game_migration', 'new_url': new_url}
                )
        except Exception as e:
            print(f"Migration Error: {e}")

    # --- Handlers ---
    
    def chat_message(self, event):
        self.safe_send({
            'type': 'chat_message',
            'message': event['message'],
            'username': event['username']
        })

    def game_started(self, event):
        self.safe_send({
            'type': 'game_started',
            'redirect_url': reverse('game_room')
        })

    def update_game_stage(self, event):
        self.safe_send({'type': 'update_stage'})

    def force_reload(self, event):
        self.safe_send({'type': 'force_reload'})

    def player_list_update(self, event):
        try:
            game = GameSession.objects.filter(room_code=self.room_code).first()
            if not game: return
            
            players = [{'id': p.id, 'nickname': p.nickname, 'avatar_id': p.avatar_id} for p in game.players.all()]
            
            self.safe_send({
                'type': 'player_list_update', 
                'players': players, 
                'player_count': len(players)
            })
        except Exception:
            pass 

    def room_disbanded(self, event):
        self.safe_send(event)
        
    # --- ДОБАВЛЕН ОБРАБОТЧИК УДАЛЕНИЯ КОМНАТЫ ИЗ VIEWS.PY ---
    def game_aborted(self, event):
        self.safe_send({
            'type': 'game_aborted',
            'message': event.get('message', 'Лобби распущено')
        })
    
    def game_migration(self, event):
        self.safe_send({
            'type': 'game_migration',
            'new_url': event['new_url']
        })
        
    def player_kicked_event(self, event):
        # Если session_id совпадает с кикнутым — отправляем команду на редирект
        if self.session_id == event.get('kicked_session_id'):
            self.safe_send({'type': 'kicked'})