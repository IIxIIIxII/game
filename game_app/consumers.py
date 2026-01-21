import json
import random
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from .models import GameSession, Player, GameCoordinate
from django.urls import reverse
from django.db.models import F

class GameConsumer(WebsocketConsumer):
    
    # --- МЕТОДЫ ПОДКЛЮЧЕНИЯ/ОТКЛЮЧЕНИЯ ---
    
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
        try:
            game = GameSession.objects.get(room_code=self.room_code)
        except GameSession.DoesNotExist:
            async_to_sync(self.channel_layer.group_discard)(self.room_group_name, self.channel_name)
            return

        is_host = (self.session_id == game.host_session)

        if not is_host and game.current_stage != 'lobby':
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                { 'type': 'chat_message', 'message': f'{self.nickname} покинул комнату.', 'username': 'Система' }
            )

        if is_host:
            if game.current_stage == 'lobby':
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    { 'type': 'game_aborted', 'message': 'Ведущий покинул игру. Комната распущена.'}
                )
                try: game.delete()
                except GameSession.DoesNotExist: pass
            else:
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    { 'type': 'game_aborted', 'message': 'Ведущий отключился. Игра окончена.'}
                )
        elif not is_host and game.current_stage == 'lobby':
             try:
                 Player.objects.get(session_id=self.session_id, game=game).delete()
             except Player.DoesNotExist:
                 pass
             async_to_sync(self.channel_layer.group_send)(
                 self.room_group_name,
                 {'type': 'player_list_update'} 
             )

        async_to_sync(self.channel_layer.group_discard)(
            self.room_group_name,
            self.channel_name
        )

    # --- МЕТОД ПРИЕМА СООБЩЕНИЙ ---
    
    def receive(self, text_data):
        try:
            text_data_json = json.loads(text_data)
        except json.JSONDecodeError:
            return

        message_type = text_data_json.get('type')
        game = GameSession.objects.filter(room_code=self.room_code).first()

        if not game: return

        # 1. Чат
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
        
        # 2. Обновление лобби
        elif message_type == 'refresh_players':
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                {'type': 'player_list_update'}
            )

        # 3. Обновление профиля
        elif message_type == 'update_profile':
            new_nickname = text_data_json.get('nickname')
            new_avatar_id = text_data_json.get('avatar_id')
            if new_nickname and new_avatar_id:
                player = Player.objects.filter(session_id=self.session_id, game=game).first()
                if player:
                    player.nickname = new_nickname
                    player.avatar_id = new_avatar_id
                    player.save()
                    self.nickname = new_nickname 
                    async_to_sync(self.channel_layer.group_send)(
                        self.room_group_name,
                        {'type': 'player_list_update'}
                    )

        # --- СПЕЦОСОБЕННОСТЬ: СКАНИРОВАНИЕ УЧЕНОГО ---
        elif message_type == 'use_ability':
            player = Player.objects.filter(session_id=self.session_id, game=game).first()
            if player and player.role == 'scientist' and not player.special_used:
                # Проверка условия (от 6 игроков и 2 шпионов)
                alien_count = Player.objects.filter(game=game, role='alien').count()
                if alien_count >= 2:
                    target_id = text_data_json.get('target_id')
                    target = Player.objects.filter(id=target_id, game=game).first()
                    if target and target.session_id != self.session_id:
                        player.special_used = True
                        player.save()
                        
                        # Отправляем результат только Ученому
                        self.send(text_data=json.dumps({
                            'type': 'ability_result',
                            'target_nickname': target.nickname,
                            'is_alien': target.role == 'alien'
                        }))

        # 4. Команды Ведущего
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

        # 5. Команды Игрока (Голосование)
        elif message_type == 'submit_vote' and game.current_stage == 'voting':
            try:
                player = Player.objects.get(session_id=self.session_id, game=game)
                if not player.has_voted:
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

    # --- МЕТОДЫ ИГРОВОЙ ЛОГИКИ ---
    
    def start_game_logic(self, game):
        players = list(game.players.all())
        player_count = len(players)

        if player_count < 3 or game.current_stage != 'lobby':
            return

        random.shuffle(players)
        
        # Расчет ролей
        alien_count = 2 if player_count >= 5 else 1
        
        # Обнуляем статусы
        game.players.all().update(special_used=False)

        for i, player in enumerate(players):
            if i < alien_count:
                player.role = 'alien'
            # Ученый появляется только если игроков 6 или больше
            elif i == alien_count and player_count >= 6:
                player.role = 'scientist'
            else:
                player.role = 'human'
            player.save()

        human_count = player_count - alien_count
        game.human_coords_to_win = max(1, human_count - 1)
        game.visited_human_coords_count = 0
        game.current_stage = 'roles'
        game.save()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'game_started'}
        )
        
    def generate_coordinates_logic(self, game):
        players = list(game.players.all())
        COORD_NAMES = ["Сектор Альфа-9", "Туманность Ориона", "Пояс Койпера", "Галактика M-31", "Звезда Кеплера-186f", "Астероид Оумуамуа", "Система Траппист-1", "Планета Глизе 581c", "Черная дыра Стрелец A*"]
        HUMAN_RESOURCES = ["Запасы Топлива", "Партия Репликаторов", "Медицинский отсек", "Запчасти для Двигателя", "Запасы Кислорода", "Гидропонные Фермы"]
        ALIEN_MESSAGE = "Ты должен привести их к нам. Это твой шанс."
        
        random.shuffle(COORD_NAMES)
        random.shuffle(HUMAN_RESOURCES)
        
        GameCoordinate.objects.filter(game=game).delete()

        for i, player in enumerate(players):
            is_alien = (player.role == 'alien')
            GameCoordinate.objects.create(
                game=game,
                player=player,
                coordinate_name=COORD_NAMES.pop(0) if COORD_NAMES else f"Сектор {i}",
                resource_description=ALIEN_MESSAGE if is_alien else (HUMAN_RESOURCES.pop(0) if HUMAN_RESOURCES else "Пустые контейнеры"),
                is_alien_coord=is_alien
            )
            
        game.current_stage = 'coordinates'
        game.save()
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    def start_voting_logic(self, game):
        game.current_stage = 'voting'
        game.save()
        game.players.all().update(has_voted=False)
        game.coordinates.all().update(votes=0)
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    def process_results_logic(self, game):
        game.coordinates.all().update(was_visited=False)
        winner_coord = game.coordinates.order_by('-votes').first()
        
        if not winner_coord:
            game.current_stage = 'coordinates'
            game.save()
            async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})
            return

        winner_coord.was_visited = True
        winner_coord.save()
        
        if winner_coord.is_alien_coord:
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
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, {'type': 'update_game_stage'})

    # --- МЕТОДЫ-ОБРАБОТЧИКИ ---

    def chat_message(self, event):
        self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': event['message'],
            'username': event['username']
        }))

    def game_started(self, event):
        redirect_url = reverse('game_room', kwargs={'room_code': self.room_code})
        self.send(text_data=json.dumps({
            'type': 'game_started',
            'redirect_url': redirect_url
        }))

    def update_game_stage(self, event):
        self.send(text_data=json.dumps({'type': 'update_stage'}))

    def player_list_update(self, event):
        game = GameSession.objects.filter(room_code=self.room_code).first()
        if not game: return
        players = game.players.all()
        players_data = [{'nickname': p.nickname, 'avatar_id': p.avatar_id} for p in players]
        self.send(text_data=json.dumps({
            'type': 'player_list_update',
            'players': players_data,
            'player_count': players.count()
        }))