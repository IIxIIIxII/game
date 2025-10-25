from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from .models import GameSession, Player, GameCoordinate
import random
import string
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

def generate_session_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=20))

def index(request):
    if 'session_id' not in request.session:
        request.session['session_id'] = generate_session_id()
        request.session['nickname'] = f'Игрок{random.randint(1000, 9999)}'
        request.session['avatar_id'] = '1'
    
    session_id = request.session['session_id']
    
    # Проверяем, не в игре ли пользователь уже
    active_game = Player.objects.filter(session_id=session_id).exclude(game__current_stage='game_over').first()
    if active_game:
        return redirect('game_lobby', room_code=active_game.game.room_code)
    
    if request.method == "POST":
        nickname = request.POST.get('nickname')
        avatar_id = request.POST.get('avatar_id')
        
        if nickname and avatar_id:
            request.session['nickname'] = nickname
            request.session['avatar_id'] = avatar_id
            
            # Определяем действие
            action = request.POST.get('action')
            if action == 'create':
                return create_game(request)
            elif action == 'join':
                return join_game(request)
    
    context = {
        'current_nickname': request.session.get('nickname', ''),
        'current_avatar_id': request.session.get('avatar_id', '1'),
    }
    return render(request, 'game_app/index.html', context)

def create_game(request):
    session_id = request.session.get('session_id')
    nickname = request.session.get('nickname')
    avatar_id = request.session.get('avatar_id')

    if not nickname or not avatar_id:
        messages.error(request, "Нужно выбрать ник и аватар!")
        return redirect('index')

    # Создаем игру
    game = GameSession.objects.create(host_session=session_id)
    
    # ВЕДУЩИЙ НЕ СОЗДАЕТСЯ КАК ИГРОК - только как хост сессии

    return redirect('game_lobby', room_code=game.room_code)

def join_game(request):
    session_id = request.session.get('session_id')
    nickname = request.session.get('nickname')
    avatar_id = request.session.get('avatar_id')
    room_code = request.POST.get('room_code', '').upper()

    if not nickname or not avatar_id or not room_code:
        messages.error(request, "Нужно выбрать ник, аватар и ввести код комнаты!")
        return redirect('index')

    try:
        game = GameSession.objects.get(room_code=room_code)
        
        # Проверяем, не является ли пользователь ведущим этой игры
        if session_id == game.host_session:
            messages.error(request, "Вы уже являетесь ведущим этой игры!")
            return redirect('index')
        
        if game.players.count() >= 7:
            messages.error(request, "Комната переполнена!")
            return redirect('index')
        
        if game.current_stage != 'lobby':
            messages.error(request, "Игра уже началась!")
            return redirect('index')

        # Создаем игрока (только для обычных игроков, не для ведущего)
        Player.objects.get_or_create(
            session_id=session_id,
            game=game,
            defaults={'nickname': nickname, 'avatar_id': avatar_id}
        )
        
        # Уведомляем через WebSocket об обновлении списка игроков
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{game.room_code}',
            {'type': 'player_list_update'}
        )

        return redirect('game_lobby', room_code=game.room_code)

    except GameSession.DoesNotExist:
        messages.error(request, "Комната с таким кодом не найдена!")
        return redirect('index')

# game_app/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
# Убедись, что все нужные импорты есть вверху файла
from .models import GameSession, Player
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

# ... (остальные твои view: index, create_game, join_game) ...

def game_lobby(request, room_code):
    session_id = request.session.get('session_id')
    game = get_object_or_404(GameSession, room_code=room_code)

    # --- ВОТ ДОБАВЛЕННАЯ ПРОВЕРКА ---
    # Если игра уже НЕ в лобби, сразу перекидываем в игровую комнату
    if game.current_stage != 'lobby':
        # Неважно, хост ты или игрок, если игра идет - тебе в game_room
        return redirect('game_room', room_code=game.room_code)
    # --- КОНЕЦ ПРОВЕРКИ ---

    players = game.players.all()  # Только обычные игроки, без ведущего
    
    is_host = (session_id == game.host_session)
    current_player = players.filter(session_id=session_id).first()
    
    # Если пользователь не ведущий и не игрок, отправляем на главную
    if not is_host and not current_player:
        messages.error(request, "Вы не состоите в этой игре.")
        return redirect('index')
    
    # Обработка изменения данных в лобби (POST-запросы)
    if request.method == "POST":
        action = request.POST.get('action')
        
        if action == 'update_profile':
            new_nickname = request.POST.get('nickname')
            new_avatar_id = request.POST.get('avatar_id')
            
            if new_nickname and new_avatar_id:
                # Обновляем данные в сессии
                request.session['nickname'] = new_nickname
                request.session['avatar_id'] = new_avatar_id
                
                # Если это игрок (не ведущий), обновляем данные в базе
                if current_player:
                    current_player.nickname = new_nickname
                    current_player.avatar_id = new_avatar_id
                    current_player.save()
                    
                    # Уведомляем всех об обновлении списка игроков
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'game_{game.room_code}',
                        {'type': 'player_list_update'}
                    )
                
                messages.success(request, "Данные успешно обновлены!")
                # Перезагружаем страницу, чтобы показать обновленные данные
                return redirect('game_lobby', room_code=room_code)
        
        elif action == 'delete_room' and is_host:
            # Удаляем комнату (игру)
            
            # Сначала уведомляем всех игроков, что комната удалена (опционально)
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                 f'game_{game.room_code}',
                 {
                      'type': 'game_aborted', # Используем тот же тип, что и при дисконнекте хоста
                      'message': 'Ведущий удалил комнату.'
                 }
            )
            
            game.delete()
            messages.success(request, "Комната успешно удалена!")
            return redirect('index')

    # Готовим данные для отображения страницы (GET-запрос или после POST без редиректа)
    context = {
        'game': game,
        'players': players,
        'is_host': is_host,
        'player_count': players.count(),
        'current_nickname': request.session.get('nickname', ''),
        'current_avatar_id': request.session.get('avatar_id', '1'),
    }
    return render(request, 'game_app/lobby.html', context)


def game_room(request, room_code):
    session_id = request.session.get('session_id')
    game = get_object_or_404(GameSession, room_code=room_code)
    
    is_host = (session_id == game.host_session)
    player = Player.objects.filter(session_id=session_id, game=game).first()
    
    if not is_host and not player:
        messages.error(request, "Вы не состоите в этой игре.")
        return redirect('index')

    last_visited_coord = None
    coords_needed_to_win = 0

    if game.current_stage == 'results' or game.current_stage == 'game_over':
        last_visited_coord = GameCoordinate.objects.filter(game=game, was_visited=True).first()
    
    if game.current_stage == 'results':
        coords_needed_to_win = game.human_coords_to_win - game.visited_human_coords_count

    context = {
        'game': game,
        'is_host': is_host,
        'player': player,
        'last_visited_coord': last_visited_coord,
        'coords_needed_to_win': coords_needed_to_win,
    }
    return render(request, 'game_app/game_room.html', context)