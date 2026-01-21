import json
import random
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from .models import GameSession, Player, GameCoordinate
from django.urls import reverse
from django.db.models import F # <-- Убедись, что этот импорт есть

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
        # Отправляем сообщение в чат о входе (только для Игроков)
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
        print(f"--- Disconnect: User {self.nickname} disconnecting (code: {close_code})...")
        
        try:
            # Используем .get() для быстрой проверки, существует ли игра
            game = GameSession.objects.get(room_code=self.room_code)
        except GameSession.DoesNotExist:
            print(f"--- Disconnect: Game {self.room_code} not found or already deleted.")
            async_to_sync(self.channel_layer.group_discard)(self.room_group_name, self.channel_name)
            return

        is_host = (self.session_id == game.host_session)

        # Отправляем сообщение в чат о выходе (если не хост и игра не в лобби)
        if not is_host and game.current_stage != 'lobby':
            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                { 'type': 'chat_message', 'message': f'{self.nickname} покинул комнату.', 'username': 'Система' }
            )

        # --- НАЧАЛО ИСПРАВЛЕННОЙ ЛОГИКИ ---

        # Если хост выходит
        if is_host:
            # ВАЖНО: Мы удаляем игру, только если хост вышел из ЛОББИ.
            if game.current_stage == 'lobby':
                print(f"--- Disconnect: Host {self.nickname} disconnected from LOBBY. Deleting game {self.room_code}.")
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    { 'type': 'game_aborted', 'message': 'Ведущий покинул игру. Комната распущена.'}
                )
                try: game.delete()
                except GameSession.DoesNotExist: pass
            
            # Если игра уже НАЧАЛАСЬ, мы НЕ удаляем игру,
            # потому что это может быть просто перезагрузка страницы (reload).
            else:
                print(f"--- Disconnect: Host {self.nickname} disconnected from ACTIVE game ({game.current_stage}). NOT deleting game.")
                # Мы все равно разошлем 'game_aborted', чтобы другие игроки вышли
                # (на случай, если хост действительно закрыл вкладку)
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    { 'type': 'game_aborted', 'message': 'Ведущий отключился. Игра окончена.'}
                )

        # Если игрок (не хост) выходит из лобби, мы его удаляем из БД
        elif not is_host and game.current_stage == 'lobby':
             print(f"--- Disconnect: Player {self.nickname} disconnected from lobby. Removing from DB.")
             try:
                 Player.objects.get(session_id=self.session_id, game=game).delete()
             except Player.DoesNotExist:
                 pass
             
             # Обновляем список игроков у всех в лобби
             async_to_sync(self.channel_layer.group_send)(
                 self.room_group_name,
                 {'type': 'player_list_update'} 
             )
        
        # Если игрок (не хост) выходит из АКТИВНОЙ ИГРЫ, мы его НЕ удаляем
        # (он может просто перезагружать страницу)
        elif not is_host and game.current_stage != 'lobby':
            print(f"--- Disconnect: Player {self.nickname} disconnected from ACTIVE game. NOT removing from DB.")

        # --- КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ ---

        # Отсоединяемся от группы
        async_to_sync(self.channel_layer.group_discard)(
            self.room_group_name,
            self.channel_name
        )
        print(f"--- Disconnect: User {self.nickname} fully disconnected.")
    # --- МЕТОД ПРИЕМА СООБЩЕНИЙ (ГЛАВНЫЙ МАРШРУТИЗАТОР) ---
    
    def receive(self, text_data):
        try:
            text_data_json = json.loads(text_data)
        except json.JSONDecodeError:
            print("--- Receive: Invalid JSON received")
            return

        message_type = text_data_json.get('type')
        game = GameSession.objects.filter(room_code=self.room_code).first()

        if not game:
            print(f"--- Receive: Game {self.room_code} not found.")
            return

        print(f"--- Receive: Got message type '{message_type}' from {self.session_id}")

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

        # 3. Обновление профиля (ник и аватар)
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

        # 4. Команды Ведущего (Host)
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
            else:
                 print(f"--- Receive: Unknown host command or stage mismatch: {message_type}")

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
                print(f"--- Receive: Vote error for player {self.nickname}")

        # 6. Обработка исключений (если ни одно условие не подошло)
        else:
            if message_type == 'start_game' and self.session_id != game.host_session:
                print(f"--- Receive: Non-host tried to start game.")
            else:
                print(f"--- Receive: Message '{message_type}' ignored.")
    # --- МЕТОДЫ ИГРОВОЙ ЛОГИКИ (ВЫЗЫВАЮТСЯ ИЗ RECEIVE) ---
    
    def start_game_logic(self, game):
        players = list(game.players.all())
        player_count = len(players)
        print(f"--- Проверка start_game_logic: player_count = {player_count}, current_stage = {game.current_stage}") # <-- ДОБАВИМ ЭТО

        if player_count < 3 or game.current_stage != 'lobby':
            print(f"--- Условие НЕ выполнено! Выход из start_game_logic.") # <-- И ЭТО
            return # <-- Функция молча выходит здесь!
        
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
            {'type': 'game_started'} # Вызовет game_started
        )
        
    def generate_coordinates_logic(self, game):
        players = list(game.players.all())
        
        COORD_NAMES = ["Сектор Альфа-9", "Туманность Ориона", "Пояс Койпера", "Галактика M-31", "Звезда Кеплера-186f", "Астероид Оумуамуа", "Система Траппист-1", "Планета Глизе 581c", "Черная дыра Стрелец A*"]
        HUMAN_RESOURCES = ["Запасы Топлива", "Партия Репликаторов", "Медицинский отсек", "Запчасти для Двигателя", "Запасы Кислорода", "Гидропонные Фермы"]
        ALIEN_MESSAGE = "Ты должен привести их к нам. Это твой шанс."
        
        random.shuffle(COORD_NAMES)
        random.shuffle(HUMAN_RESOURCES)
        
        GameCoordinate.objects.filter(game=game).delete() # Очищаем старые

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
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'update_game_stage'} # Вызовет update_game_stage
        )

    def start_voting_logic(self, game):
        game.current_stage = 'voting'
        game.save()
        # Сбрасываем голоса
        game.players.all().update(has_voted=False)
        game.coordinates.all().update(votes=0)
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'update_game_stage'} # Вызовет update_game_stage
        )

    def process_results_logic(self, game):
        # Сбрасываем "посещение"
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
            game.visited_human_coords_count = F('visited_human_coords_count') + 1
            game.save() # Сохраняем F-выражение
            game.refresh_from_db() # Получаем актуальное значение из БД
            
            if game.visited_human_coords_count >= game.human_coords_to_win:
                game.current_stage = 'game_over'
                game.game_over_status = 'humans_win'
            else:
                game.current_stage = 'results'
        
        game.save()
        
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {'type': 'update_game_stage'} # Вызовет update_game_stage
        )

    # --- МЕТОДЫ-ОБРАБОТЧИКИ (ОТПРАВЛЯЮТ В JS) ---

    def chat_message(self, event):
        # Отправляем сообщение чата в WebSocket
        self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': event['message'],
            'username': event['username']
        }))

    def game_started(self, event):
        # Отправляем команду на редирект в игровую комнату
        redirect_url = reverse('game_room', kwargs={'room_code': self.room_code})
        self.send(text_data=json.dumps({
            'type': 'game_started',
            'redirect_url': redirect_url
        }))

    def update_game_stage(self, event):
        # Отправляем команду "просто обнови страницу"
        self.send(text_data=json.dumps({
            'type': 'update_stage' 
        }))

    def player_list_update(self, event):
        # Отправляем обновленный список игроков
        game = GameSession.objects.filter(room_code=self.room_code).first()
        if not game:
            return
            
        players = game.players.all()
        players_data = []
        for p in players:
            players_data.append({
                'nickname': p.nickname,
                'avatar_id': p.avatar_id,
                # 'is_host': False (подразумевается, т.к. ведущий не в этом списке)
            })

        self.send(text_data=json.dumps({
            'type': 'player_list_update',
            'players': players_data,
            'player_count': players.count()
        }))