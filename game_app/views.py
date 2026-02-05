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

    game = GameSession.objects.create(host_session=session_id)
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
        
        if session_id == game.host_session:
            messages.error(request, "Вы уже являетесь ведущим этой игры!")
            return redirect('index')
        
        if game.players.count() >= 7:
            messages.error(request, "Комната переполнена!")
            return redirect('index')
        
        if game.current_stage != 'lobby':
            messages.error(request, "Игра уже началась!")
            return redirect('index')

        Player.objects.get_or_create(
            session_id=session_id,
            game=game,
            defaults={'nickname': nickname, 'avatar_id': avatar_id}
        )
        
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{game.room_code}',
            {'type': 'player_list_update'}
        )

        return redirect('game_lobby', room_code=game.room_code)

    except GameSession.DoesNotExist:
        messages.error(request, "Комната с таким кодом не найдена!")
        return redirect('index')

def game_lobby(request, room_code):
    session_id = request.session.get('session_id')
    game = get_object_or_404(GameSession, room_code=room_code)

    if game.current_stage != 'lobby':
        return redirect('game_room', room_code=game.room_code)

    players = game.players.all()
    
    is_host = (session_id == game.host_session)
    current_player = players.filter(session_id=session_id).first()
    
    if not is_host and not current_player:
        messages.error(request, "Вы не состоите в этой игре.")
        return redirect('index')
    
    if request.method == "POST":
        action = request.POST.get('action')
        
        if action == 'update_profile':
            new_nickname = request.POST.get('nickname')
            new_avatar_id = request.POST.get('avatar_id')
            
            if new_nickname and new_avatar_id:
                request.session['nickname'] = new_nickname
                request.session['avatar_id'] = new_avatar_id
                
                if current_player:
                    current_player.nickname = new_nickname
                    current_player.avatar_id = new_avatar_id
                    current_player.save()
                    
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'game_{game.room_code}',
                        {'type': 'player_list_update'}
                    )
                
                messages.success(request, "Данные успешно обновлены!")
                return redirect('game_lobby', room_code=room_code)
        
        elif action == 'delete_room' and is_host:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                 f'game_{game.room_code}',
                 {
                      'type': 'game_aborted',
                      'message': 'Ведущий удалил комнату.'
                 }
            )
            
            game.delete()
            messages.success(request, "Комната успешно удалена!")
            return redirect('index')

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

def leave_game(request, room_code):
    session_id = request.session.get('session_id')
    try:
        game = GameSession.objects.get(room_code=room_code)
        player = Player.objects.filter(session_id=session_id, game=game).first()

        if player:
            if game.current_stage == 'lobby':
                player.delete()
            else:
                # ВАЖНО: Формат "LEFT-ID" для надежной фильтрации
                player.session_id = f"LEFT-{player.id}"
                player.save()

        # Очищаем только привязку к комнате, ник и аву оставляем
        keys_to_remove = ['room_code', 'is_host']
        for key in keys_to_remove:
            if key in request.session:
                del request.session[key]

        request.session.modified = True

    except (GameSession.DoesNotExist, Player.DoesNotExist):
        pass

    return redirect('index')