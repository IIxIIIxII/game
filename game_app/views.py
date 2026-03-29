import os
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from .models import GameSession, Player, GameCoordinate
import random
import string
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
from django.core.mail import send_mail
from django.conf import settings

# Загружаем переменные из .env файла
from dotenv import load_dotenv
load_dotenv()

def generate_session_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=20))

def index(request):
    if 'session_id' not in request.session:
        request.session['session_id'] = generate_session_id()
        request.session['nickname'] = f'Игрок{random.randint(1000, 9999)}'
        request.session['avatar_id'] = '1'
    
    session_id = request.session['session_id']
    
    # 1. Проверяем, не в игре ли пользователь как игрок
    active_game = Player.objects.filter(session_id=session_id).exclude(game__current_stage='game_over').first()
    if active_game:
        request.session['room_code'] = active_game.game.room_code
        return redirect('game_lobby')
        
    # 2. ПРОВЕРКА ДЛЯ ХОСТА
    hosted_game = GameSession.objects.filter(host_session=session_id).exclude(current_stage='game_over').first()
    if hosted_game:
        request.session['room_code'] = hosted_game.room_code
        return redirect('game_lobby')
    
    if request.method == "POST":
        # --- ПРОВЕРКА reCAPTCHA ЧЕРЕЗ .ENV ---
        recaptcha_response = request.POST.get('g-recaptcha-response')
        google_data = {
            'secret': os.getenv('RECAPTCHA_SECRET_KEY'),  # <-- КЛЮЧ БЕРЕТСЯ ИЗ .ENV
            'response': recaptcha_response
        }
        
        try:
            r = requests.post('https://www.google.com/recaptcha/api/siteverify', data=google_data)
            result = r.json()
            if not result.get('success'):
                messages.error(request, 'Пожалуйста, подтвердите, что вы не робот.')
                return redirect('index')
        except Exception:
            messages.error(request, 'Ошибка сервиса проверки капчи. Попробуйте еще раз.')
            return redirect('index')
        # --------------------------

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
    request.session['room_code'] = game.room_code
    return redirect('game_lobby')

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
        
        request.session['room_code'] = game.room_code
        return redirect('game_lobby')

    except GameSession.DoesNotExist:
        messages.error(request, "Комната с таким кодом не найдена!")
        return redirect('index')

def game_lobby(request):
    room_code = request.session.get('room_code')
    if not room_code:
        return redirect('index')
        
    game = get_object_or_404(GameSession, room_code=room_code)
    session_id = request.session.get('session_id')

    if game.current_stage != 'lobby':
        return redirect('game_room')

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
                return redirect('game_lobby')
        
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

def game_room(request):
    room_code = request.session.get('room_code')
    if not room_code:
        return redirect('index')
        
    game = get_object_or_404(GameSession, room_code=room_code)
    session_id = request.session.get('session_id')
    
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
    
    if room_code:
        try:
            game = GameSession.objects.get(room_code=room_code)
            player = Player.objects.filter(session_id=session_id, game=game).first()

            if player:
                if game.current_stage == 'lobby':
                    player.delete()
                else:
                    player.session_id = f"LEFT-{player.id}"
                    player.save()
            
            # Сообщаем остальным, что список игроков изменился
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'game_{room_code}',
                {'type': 'player_list_update'}
            )

        except (GameSession.DoesNotExist, Player.DoesNotExist):
            pass

    keys_to_remove = ['room_code', 'is_host']
    for key in keys_to_remove:
        if key in request.session:
            del request.session[key]

    request.session.modified = True
    return redirect('index')

def create_rematch(request, old_room_code):
    session_id = request.session.get('session_id')
    old_game = get_object_or_404(GameSession, room_code=old_room_code)
    
    if session_id != old_game.host_session:
        return redirect('index')

    new_game = GameSession.objects.create(host_session=session_id)
    
    active_players = old_game.players.exclude(session_id__startswith='LEFT-')
    
    for player in active_players:
        player.game = new_game
        player.has_voted = False
        player.special_used = False
        player.save()
    
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'game_{old_room_code}',
        {
            'type': 'game_migration',
            'new_url': '/' 
        }
    )
    
    old_game.delete()
    
    request.session['room_code'] = new_game.room_code
    return redirect('game_lobby')

def send_support_email(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_email = data.get('email')
            message = data.get('text')

            if not user_email or not message:
                return JsonResponse({'success': False, 'error': 'Пустые поля'})

            subject = f'CrewFall: Новое обращение в техподдержку от {user_email}'
            body = f'Отправитель: {user_email}\n\nСообщение:\n{message}'
            
            # Почта, на которую ты хочешь получать письма (берем из .env)
            receiving_email = os.getenv('SUPPORT_RECEIVER_EMAIL')

            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [receiving_email],
                fail_silently=False,
            )
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Метод не поддерживается'})