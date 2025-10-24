import json
import random
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from .models import GameSession, PlayerInGame, GameCoordinate
from django.urls import reverse

class GameConsumer(WebsocketConsumer):
    
    def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.room_group_name = f'game_{self.room_code}'
        self.user = self.scope['user']

        if not self.user.is_authenticated:
            self.close()
            return

        async_to_sync(self.channel_layer.group_add)(
            self.room_group_name,
            self.channel_name
        )

        self.accept()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': f'{self.user.username} присоединился к комнате.',
                'username': 'Система'
            }
        )

    def disconnect(self, close_code):
        game = GameSession.objects.filter(room_code=self.room_code).first()
        if not game:
            return

        if self.user == game.host:
            if game.current_stage == 'lobby':
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {'type': 'game_aborted'}
                )
                game.delete()
            else:
                print(f"Host {self.user.username} disconnected from ACTIVE game. Not deleting.")
        else:
            player = PlayerInGame.objects.filter(user=self.user, game=game).first()
            if game.current_stage == 'lobby' and player:
                player.delete()
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {'type': 'player_list_update'}
                )
            else:
                print(f"Player {self.user.username} disconnected from ACTIVE game.")

        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': f'{self.user.username} покинул комнату.',
                'username': 'Система'
            }
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
            print(f"Game {self.room_code} not found, aborting receive.")
            return

        if message_type == 'chat_message':
            message = text_data_json.get('message', '')
            if message:
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message': message,
                        'username': self.user.username
                    }
                )
        
        elif self.user == game.host:
            if message_type == 'start_game' and game.current_stage == 'lobby':
                self.start_game_logic()
            
            elif message_type == 'next_stage':
                if game.current_stage == 'roles':
                    self.generate_coordinates_logic()
                elif game.current_stage == 'coordinates':
                    self.start_voting_logic()
                elif game.current_stage == 'voting':
                    self.process_results_logic()
                elif game.current_stage == 'results':
                    self.generate_coordinates_logic()

            elif message_type == 'continue_without_player':
                player_id = text_data_json.get('player_id')
                try:
                    player = PlayerInGame.objects.get(id=player_id, game=game)
                    player.is_alive = False
                    player.save()
                    
                    async_to_sync(self.channel_layer.group_send)(
                        self.room_group_name,
                        {
                            'type': 'chat_message',
                            'message': f'Игра продолжается без игрока {player.nickname}',
                            'username': 'Система'
                        }
                    )
                    
                    async_to_sync(self.channel_layer.group_send)(
                        self.room_group_name,
                        {'type': 'update_game_stage'}
                    )
                except PlayerInGame.DoesNotExist:
                    pass

        elif message_type == 'submit_vote' and game.current_stage == 'voting':
            try:
                player = PlayerInGame.objects.get(user=self.user, game=game)
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
            except (PlayerInGame.DoesNotExist, GameCoordinate.DoesNotExist):
                print(f"Error processing vote from {self.user.username}")

    def start_game_logic(self):
        game = GameSession.objects.get(room_code=self.room_code)
        players = list(game.players.all())
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

    def generate_coordinates_logic(self):
        game = GameSession.objects.get(room_code=self.room_code)
        players = list(game.players.all())
        
        COORD_NAMES = [
            "Сектор Альфа-9", "Туманность Ориона", "Пояс Койпера", 
            "Галактика M-31", "Звезда Кеплера-186f", "Астероид Оумуамуа", 
            "Система Траппист-1", "Планета Глизе 581c", "Черная дыра Стрелец A*"
        ]
        HUMAN_RESOURCES = [
            "Запасы Топлива", "Партия Репликаторов", "Медицинский отсек",
            "Запчасти для Двигателя", "Запасы Кислорода", "Гидропонные Фермы"
        ]
        ALIEN_MESSAGE = "Ты должен привести их к нам. Это твой шанс."
        
        random.shuffle(COORD_NAMES)
        random.shuffle(HUMAN_RESOURCES)
        
        GameCoordinate.objects.filter(game=game).delete()

        for i, player in enumerate(players):
            is_alien = (player.role == 'alien')
            
            GameCoordinate.objects.create(
                game=game,
                player=player,
                coordinate_name=COORD_NAMES.pop(), 
                resource_description=ALIEN_MESSAGE if is_alien else HUMAN_RESOURCES.pop(),
                is_alien_coord=is_alien
            )
            
        game.current_stage = 'coordinates'
        game.save()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'update_game_stage'
            }
        )

    def start_voting_logic(self):
        game = GameSession.objects.get(room_code=self.room_code)
        
        game.current_stage = 'voting'
        game.save()
        
        game.players.all().update(has_voted=False)
        game.coordinates.all().update(votes=0)
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'update_game_stage'
            }
        )
    
    def process_results_logic(self):
        game = GameSession.objects.get(room_code=self.room_code)
        
        game.coordinates.all().update(was_visited=False)
        winner_coord = game.coordinates.order_by('-votes').first()
        
        if not winner_coord:
             game.current_stage = 'coordinates'
             game.save()
             async_to_sync(self.channel_layer.group_send)(
                self.room_group_name, {'type': 'update_game_stage'}
             )
             return

        winner_coord.was_visited = True
        winner_coord.save()
        
        if winner_coord.is_alien_coord:
            game.current_stage = 'game_over'
            game.game_over_status = 'aliens_win'
        else:
            game.visited_human_coords_count += 1
            
            if game.visited_human_coords_count >= game.human_coords_to_win:
                game.current_stage = 'game_over'
                game.game_over_status = 'humans_win'
            else:
                game.current_stage = 'results'
        
        game.save()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'update_game_stage'}
        )

    def update_game_stage(self, event):
        self.send(text_data=json.dumps({
            'type': 'update_stage'
        }))
    
    def chat_message(self, event):
        message = event['message']
        username = event['username']

        self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': message,
            'username': username
        }))

    def game_started(self, event):
        redirect_url = reverse('game_room', kwargs={'room_code': self.room_code})
        
        self.send(text_data=json.dumps({
            'type': 'game_started',
            'redirect_url': redirect_url
        }))

    def player_list_update(self, event):
        game = GameSession.objects.get(room_code=self.room_code)
        players = game.players.all()
        player_count = players.count()
        
        players_data = []
        for p in players:
            players_data.append({
                'nickname': p.nickname,
                'avatar_id': p.avatar_id,
                'is_host': p.user == game.host
            })

        self.send(text_data=json.dumps({
            'type': 'player_list_update',
            'players': players_data,
            'player_count': player_count
        }))

    def game_aborted(self, event):
        self.send(text_data=json.dumps({
            'type': 'game_aborted',
            'message': 'Ведущий покинул игру. Игра отменена.'
        }))