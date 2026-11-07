"""Private web control panel. Never logs Telegram codes, passwords or session contents."""
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from telethon import errors
from service import TelegramService, PanelError

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
os.umask(0o077)
logging.getLogger('telethon').setLevel(logging.CRITICAL)


def create_app(service, password, secure=True):
    sessions = {}
    attempts = {}

    def session_for(req):
        token = req.cookies.get('panel_session', '')
        session = sessions.get(token)
        if session and session['until'] > time.time():
            return session
        sessions.pop(token, None)
        return None

    @web.middleware
    async def security(req, handler):
        try:
            if req.path.startswith('/api/'):
                if req.path != '/api/login':
                    session = session_for(req)
                    if session is None:
                        raise web.HTTPUnauthorized(text='Entre com a senha do painel.')
                    if req.method != 'GET' and not hmac.compare_digest(req.headers.get('X-CSRF-Token', ''), session['csrf']):
                        raise web.HTTPForbidden(text='Sessão inválida. Recarregue a página.')
                elif req.method == 'POST' and req.headers.get('X-Panel-Request') != '1':
                    raise web.HTTPForbidden(text='Solicitação inválida.')
                if req.method != 'GET' and req.content_type != 'application/json':
                    raise web.HTTPBadRequest(text='Envie JSON.')
            response = await handler(req)
        except web.HTTPException as exc:
            response = web.json_response({'error': exc.text}, status=exc.status)
        except PanelError as exc:
            response = web.json_response({'error': str(exc)}, status=400)
        except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
            response = web.json_response({'error': f'Telegram pediu que você aguarde {exc.seconds} segundos.'}, status=429)
        except errors.PhoneCodeInvalidError:
            response = web.json_response({'error': 'Código incorreto. Tente novamente.'}, status=400)
        except errors.PhoneCodeExpiredError:
            service.pending_since = 0
            service.login_stage = 'phone'
            response = web.json_response({'error': 'Código expirou. Solicite outro código.'}, status=400)
        except errors.PasswordHashInvalidError:
            response = web.json_response({'error': 'Senha de duas etapas incorreta.'}, status=400)
        except errors.RPCError as exc:
            response = web.json_response({'error': f'Telegram recusou a operação: {type(exc).__name__}.'}, status=400)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            response = web.json_response({'error': 'Confira os dados informados.'}, status=400)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            response = web.json_response({'error': 'Não foi possível conectar ao Telegram. Verifique a rede e tente novamente.'}, status=503)
        except Exception:
            response = web.json_response({'error': 'Operação não concluída. Recarregue o painel e confira a conexão.'}, status=500)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    app = web.Application(middlewares=[security], client_max_size=128*1024)

    async def login(req):
        now = time.time()
        # Use the socket peer, not an arbitrary X-Forwarded-For header.
        ip = req.remote or 'unknown'
        for key in list(attempts):
            if attempts[key]['until'] <= now:
                del attempts[key]
        for key in list(sessions):
            if sessions[key]['until'] <= now:
                del sessions[key]
        item = attempts.setdefault(ip, {'count': 0, 'until': now+600})
        if item['count'] >= 10:
            raise web.HTTPTooManyRequests(text='Muitas tentativas. Aguarde dez minutos.')
        item['count'] += 1
        body = await req.json()
        entered = body.get('password', '')
        if not isinstance(entered, str) or not hmac.compare_digest(entered.encode(), password.encode()):
            raise web.HTTPUnauthorized(text='Senha do painel incorreta.')
        attempts.pop(ip, None)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[token] = {'csrf': csrf, 'until': now+8*3600}
        response = web.json_response({'csrf': csrf})
        response.set_cookie('panel_session', token, httponly=True, secure=secure, samesite='Strict', max_age=8*3600, path='/')
        return response

    async def status(req):
        data = await service.status()
        data['csrf'] = session_for(req)['csrf']
        return web.json_response(data)

    async def action(req):
        name = req.match_info['name']
        body = await req.json()
        if not isinstance(body, dict):
            raise PanelError('Envie um objeto JSON.')
        if name == 'restore':
            return web.json_response(await service.restore())
        if name == 'request-code':
            phone = body.get('phone', '')
            if not isinstance(phone, str) or not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
                raise PanelError('Digite o telefone com + e DDI, somente números.')
            result = await service.request_code(phone)
        elif name == 'verify-code':
            code = body.get('code', '')
            if not isinstance(code, str) or not re.fullmatch(r'\d{4,8}', code):
                raise PanelError('Digite somente os números do código recebido.')
            result = await service.verify(code=code)
        elif name == 'verify-password':
            pwd = body.get('password', '')
            if not isinstance(pwd, str) or not pwd or len(pwd) > 1024:
                raise PanelError('Digite sua senha de duas etapas.')
            result = await service.verify(password=pwd)
        elif name == 'scan':
            result = await service.start_scan()
        elif name == 'cancel-scan':
            result = await service.cancel_scan()
        elif name == 'prepare':
            result = await service.prepare(body.get('message'), body.get('ids'))
        elif name == 'start':
            if body.get('confirmed') is not True:
                raise PanelError('Confirme o envio antes de continuar.')
            ticket = body.get('ticket', '')
            if not isinstance(ticket, str) or not ticket:
                raise PanelError('Confirmação inválida.')
            result = await service.start(ticket)
        elif name == 'cancel':
            result = await service.cancel()
        elif name == 'resume':
            result = await service.resume()
        elif name == 'disconnect':
            result = await service.disconnect()
        elif name == 'logout':
            sessions.pop(req.cookies.get('panel_session', ''), None)
            response = web.json_response({'ok': True})
            response.del_cookie('panel_session', path='/')
            return response
        else:
            raise web.HTTPNotFound(text='Rota não encontrada.')
        return web.json_response(result)

    async def static(req):
        names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        file, content_type = names[req.path]
        return web.Response(body=(ROOT / 'public' / file).read_bytes(), content_type=content_type, charset='utf-8')

    app.router.add_post('/api/login', login)
    app.router.add_get('/api/status', status)
    app.router.add_post('/api/{name}', action)
    for route in ['/', '/app.js', '/style.css']:
        app.router.add_get(route, static)

    async def cleanup(app):
        await service.close()
    app.on_cleanup.append(cleanup)
    return app


if __name__ == '__main__':
    owner_password = os.getenv('OWNER_PASSWORD', '')
    api_id, api_hash = os.getenv('TELEGRAM_API_ID', ''), os.getenv('TELEGRAM_API_HASH', '')
    if len(owner_password) < 12 or owner_password.startswith('TROQUE_'):
        raise SystemExit('Configure OWNER_PASSWORD com uma senha exclusiva de 12+ caracteres.')
    if not api_id.isdigit() or not re.fullmatch('[0-9a-fA-F]{32}', api_hash):
        raise SystemExit('Configure TELEGRAM_API_ID e TELEGRAM_API_HASH do seu aplicativo Telegram.')
    try:
        interval = max(5, int(os.getenv('SEND_INTERVAL_SECONDS', '10')))
        port = int(os.getenv('PORT', '80'))
    except ValueError:
        raise SystemExit('PORT e SEND_INTERVAL_SECONDS precisam ser números inteiros.')

    async def factory():
        service = TelegramService(os.getenv('DATA_DIR', str(ROOT / 'data')), int(api_id), api_hash, interval)
        return create_app(service, owner_password, secure=os.getenv('COOKIE_SECURE', 'true').lower() == 'true')

    web.run_app(factory(), host='0.0.0.0', port=port, access_log=None, print=lambda _: print('Painel Telegram iniciado.'))
