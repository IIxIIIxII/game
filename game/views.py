from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from .models import GameSession, PlayerInGame, GameCoordinate
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

@login_required
def index(request):
    active_game_exists = PlayerInGame.objects.filter(user=request.user).exclude(game__current_stage='game_over').exists()
    
    if active_game_exists:
        player_entry = PlayerInGame.objects.filter(user=request.user).exclude(game__current_stage='game_over').first()
        
        if player_entry.game.current_stage == 'lobby':
            if request.method == "POST":
                nickname = request.POST.get('nickname')
                avatar_id = request.POST.get('avatar_id')
                
                if nickname and avatar_id:
                    player_entry.nickname = nickname
                    player_entry.avatar_id = avatar_id
                    player_entry.save()
                    
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'game_{player_entry.game.room_code}',
                        {'type': 'player_list_update'}
                    )
                    
                    messages.success(request, "Данные успешно обновлены!")
                    return redirect('game_lobby', room_code=player_entry.game.room_code)
            
            context = {
                'current_nickname': player_entry.nickname,
                'current_avatar_id': player_entry.avatar_id,
                'in_lobby': True
            }
            return render(request, 'game/index.html', context)
        else:
            return redirect('game_room', room_code=player_entry.game.room_code)
        
    return render(request, 'game/index.html', {'in_lobby': False})

@login_required
def create_game(request):
    if request.method == "POST":
        nickname = request.POST.get('nickname')
        avatar_id = request.POST.get('avatar_id')

        if not nickname or not avatar_id:
            messages.error(request, "Нужно выбрать ник и аватар!")
            return redirect('index')

        game = GameSession.objects.create(host=request.user, current_stage='lobby')
        
        PlayerInGame.objects.create(
            user=request.user, 
            game=game, 
            nickname=nickname, 
            avatar_id=avatar_id
        )
        
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{game.room_code}',
            {'type': 'player_list_update'}
        )

        return redirect('game_lobby', room_code=game.room_code)
    
    return redirect('index')

@login_required
def join_game(request):
    if request.method == "POST":
        nickname = request.POST.get('nickname')
        avatar_id = request.POST.get('avatar_id')
        room_code = request.POST.get('room_code', '').upper()

        if not nickname or not avatar_id or not room_code:
            messages.error(request, "Нужно выбрать ник, аватар и ввести код комнаты!")
            return redirect('index')

        try:
            game = GameSession.objects.get(room_code=room_code)
            
            if game.players.count() >= 7:
                messages.error(request, "Комната переполнена!")
                return redirect('index')
            
            if game.current_stage != 'lobby':
                messages.error(request, "Игра уже началась!")
                return redirect('index')

            PlayerInGame.objects.get_or_create(
                user=request.user,
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
            
    return redirect('index')

@login_required
def game_lobby(request, room_code):
    game = get_object_or_404(GameSession, room_code=room_code)
    players = game.players.all()
    
    is_host = (request.user == game.host)
    current_player_entry = players.filter(user=request.user).first()
    
    if not current_player_entry and not is_host:
        messages.error(request, "Вы не состоите в этой игре.")
        return redirect('index')

    context = {
        'game': game,
        'players': players,
        'is_host': is_host,
        'player_count': players.count(),
    }
    return render(request, 'game/lobby.html', context)

@login_required
def game_room(request, room_code):
    game = get_object_or_404(GameSession, room_code=room_code)
    
    is_host = (request.user == game.host)
    player = PlayerInGame.objects.filter(user=request.user, game=game).first()
    
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
    return render(request, 'game/game_room.html', context)

@login_required
def continue_without_player(request, player_id):
    player = get_object_or_404(PlayerInGame, id=player_id)
    game = player.game
    
    if request.user != game.host:
        return JsonResponse({'error': 'Только ведущий может это сделать'}, status=403)
    
    player.is_alive = False
    player.save()
    
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'game_{game.room_code}',
        {
            'type': 'chat_message',
            'message': f'Игра продолжается без игрока {player.nickname}',
            'username': 'Система'
        }
    )
    
    async_to_sync(channel_layer.group_send)(
        f'game_{game.room_code}',
        {'type': 'update_game_stage'}
    )
    
    return JsonResponse({'success': True})