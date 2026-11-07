"""Telegram connection and a single, explicit, cancellable send queue."""
import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from telethon import TelegramClient, errors, functions, types, utils

ACTIVE = {'running', 'stopping'}


def eligible_type(entity):
    if isinstance(entity, types.Chat):
        return not (entity.left or entity.deactivated or entity.migrated_to)
    return isinstance(entity, types.Channel) and not entity.left and bool(entity.megagroup or entity.broadcast)


def bans_text(rights):
    if rights is None:
        return False
    until = getattr(rights, 'until_date', None)
    stamp = until.timestamp() if hasattr(until, 'timestamp') else (until or 0)
    if stamp > 0 and stamp < time.time():
        return False
    return any(bool(getattr(rights, flag, False)) for flag in ('view_messages', 'send_messages', 'send_plain'))


async def writable(client, entity):
    if not eligible_type(entity):
        return False
    rights = await client.get_permissions(entity, 'me')
    if isinstance(entity, types.Channel) and entity.broadcast:
        return bool(rights.is_creator or (rights.is_admin and rights.post_messages))
    if rights.is_creator or rights.is_admin:
        return True
    if rights.has_left:
        return False
    return not bans_text(getattr(entity, 'default_banned_rights', None)) and not bans_text(
        getattr(rights.participant, 'banned_rights', None))


class PanelError(Exception):
    pass


class TelegramService:
    def __init__(self, data_dir, api_id, api_hash, interval=10, client=None):
        self.folder = Path(data_dir)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = self.folder / 'last-job.json'
        self.api_id, self.api_hash = api_id, api_hash
        self.injected_client = client is not None
        self.client = client or self.new_client()
        self.interval = max(5, interval)
        self.destinations = {}
        self.scan_time = None
        self.phone = None
        self.code_hash = None
        self.pending_since = 0
        self.login_stage = 'phone'
        self.ticket = None
        self.job = json.loads(self.file.read_text()) if self.file.exists() else None
        if self.job and self.job['status'] in ACTIVE:
            self.job['status'] = 'interrupted'
            self.job['note'] = 'Servidor reiniciado. Confira o histórico antes de continuar os pendentes.'
            for item in self.job['items']:
                if item['status'] == 'sending':
                    item['status'] = 'uncertain'
                    item['detail'] = 'Confirme no Telegram se foi enviado. Não será repetido automaticamente.'
            self.save()
        self.task = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.me = None

    def new_client(self):
        return TelegramClient(str(self.folder / 'telegram'), self.api_id, self.api_hash,
                              flood_sleep_threshold=0, request_retries=0,
                              connection_retries=2, timeout=20)

    def save(self):
        if self.job:
            temp = self.file.with_suffix('.tmp')
            temp.write_text(json.dumps(self.job, ensure_ascii=False))
            os.chmod(temp, 0o600)
            temp.replace(self.file)

    async def connect(self):
        if not self.client.is_connected():
            await asyncio.wait_for(self.client.connect(), 30)

    async def authorized(self):
        await self.connect()
        return await self.client.is_user_authorized()

    async def require_account(self):
        if not await self.authorized():
            self.me = None
            self.login_stage = 'phone'
            raise PanelError('Conecte sua conta do Telegram primeiro.')
        if self.me is None:
            self.me = await self.client.get_me()
        return self.me

    async def status(self):
        # Avoid opening a remote connection merely because the web panel was opened.
        connected = self.me is not None
        return {'connected': connected,
                'account': {'name': utils.get_display_name(self.me), 'username': self.me.username} if connected else None,
                'login_stage': self.login_stage,
                'destinations': list(self.destinations.values()), 'scan_time': self.scan_time,
                'interval': self.interval, 'job': self.job}

    async def restore(self):
        async with self.lock:
            if await self.authorized():
                self.me = await self.client.get_me()
                self.login_stage = 'connected'
            return await self.status()

    async def request_code(self, phone):
        async with self.lock:
            if self.job and self.job['status'] in ACTIVE:
                raise PanelError('Pare o envio antes de alterar a conexão.')
            await self.connect()
            if await self.client.is_user_authorized():
                self.me = await self.client.get_me()
                self.login_stage = 'connected'
                return {'stage': 'connected'}
            if self.pending_since and time.time() - self.pending_since < 60:
                raise PanelError('Aguarde um minuto antes de pedir outro código.')
            sent = await self.client.send_code_request(phone)
            self.phone, self.code_hash = phone, sent.phone_code_hash
            self.pending_since = time.time()
            self.login_stage = 'code'
            return {'stage': 'code'}

    async def verify(self, code=None, password=None):
        async with self.lock:
            if not self.phone or time.time() - self.pending_since > 600:
                self.phone = self.code_hash = None
                self.login_stage = 'phone'
                raise PanelError('Login expirado. Solicite um novo código.')
            try:
                if password is not None:
                    if self.login_stage != 'password':
                        raise PanelError('Confirme o código antes da senha de duas etapas.')
                    await self.client.sign_in(password=password)
                else:
                    await self.client.sign_in(phone=self.phone, code=code, phone_code_hash=self.code_hash)
            except errors.SessionPasswordNeededError:
                self.login_stage = 'password'
                return {'stage': 'password'}
            self.phone = self.code_hash = None
            self.pending_since = 0
            self.me = await self.client.get_me()
            self.login_stage = 'connected'
            return {'stage': 'connected'}

    async def scan(self):
        async with self.lock:
            await self.require_account()
            if self.job and self.job['status'] in ACTIVE:
                raise PanelError('Aguarde ou pare o envio antes de atualizar os destinos.')
            found = {}
            async for dialog in self.client.iter_dialogs():
                entity = dialog.entity
                if not eligible_type(entity):
                    continue
                try:
                    allowed = await writable(self.client, entity)
                except errors.FloodWaitError:
                    raise
                except errors.RPCError:
                    continue
                if allowed:
                    peer_id = str(utils.get_peer_id(entity))
                    found[peer_id] = {'id': peer_id, 'title': dialog.name,
                                     'kind': 'channel' if getattr(entity, 'broadcast', False) else 'group',
                                     'visibility': 'Público' if getattr(entity, 'username', None) else 'Privado'}
            self.destinations = found
            self.scan_time = time.time()
            self.ticket = None
            return {'destinations': list(found.values()), 'scan_time': self.scan_time}

    def check_wait(self):
        if self.job and self.job.get('wait_until', 0) > time.time():
            raise PanelError('Aguarde o prazo de pausa informado pelo Telegram antes de enviar novamente.')

    async def prepare(self, message, ids):
        async with self.lock:
            me = await self.require_account()
            self.check_wait()
            if self.job and self.job['status'] in ACTIVE:
                raise PanelError('Já existe um envio em andamento.')
            if not isinstance(message, str) or not message.strip() or len(message.encode('utf-16-le')) // 2 > 4096:
                raise PanelError('Escreva uma mensagem de até 4.096 caracteres Telegram.')
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                raise PanelError('Selecione pelo menos um destino.')
            ids = list(dict.fromkeys(ids))
            if any(i not in self.destinations for i in ids):
                raise PanelError('Destino inválido. Atualize a lista e selecione novamente.')
            self.ticket = {'token': secrets.token_urlsafe(32), 'expires': time.time()+300,
                           'message': message, 'items': [dict(self.destinations[i]) for i in ids],
                           'account_id': me.id}
            return {'ticket': self.ticket['token'], 'message': message,
                    'destinations': self.ticket['items'], 'interval': self.interval}

    async def start(self, ticket):
        async with self.lock:
            me = await self.require_account()
            if self.job and self.job.get('ticket') == ticket:
                return self.public_job()
            self.check_wait()
            if self.job and self.job['status'] in ACTIVE:
                raise PanelError('Já existe um envio em andamento.')
            draft = self.ticket
            if not draft or draft['expires'] < time.time() or not secrets.compare_digest(draft['token'], ticket):
                raise PanelError('Confirmação expirada. Revise a mensagem novamente.')
            if me.id != draft['account_id']:
                raise PanelError('A conta mudou. Atualize os destinos.')
            self.job = {'id': secrets.token_hex(12), 'ticket': ticket, 'account_id': me.id,
                        'created_at': time.time(), 'message': draft['message'], 'status': 'running',
                        'note': '', 'wait_until': 0,
                        'items': [{**d, 'status': 'pending', 'detail': ''} for d in draft['items']]}
            self.ticket = None
            self.stop.clear()
            self.save()
            self.task = asyncio.create_task(self.run())
            return self.public_job()

    def public_job(self):
        return self.job

    async def wait(self, seconds):
        try:
            await asyncio.wait_for(self.stop.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def run(self):
        try:
            for item in self.job['items']:
                if self.stop.is_set():
                    break
                if item['status'] != 'pending':
                    continue
                try:
                    entity = await self.client.get_entity(int(item['id']))
                    if not await writable(self.client, entity):
                        item.update(status='skipped', detail='Sem permissão para publicar neste destino.')
                        self.save()
                        continue
                    # A second type check prevents any user peer from entering the send path.
                    if not eligible_type(entity):
                        raise PanelError('Conversa individual bloqueada.')
                    if self.stop.is_set():
                        break
                    item['status'] = 'sending'
                    self.save()
                    # One request per destination. A stable random ID protects against network retransmissions.
                    rid = int.from_bytes(secrets.token_bytes(8), 'little', signed=True)
                    await self.client(functions.messages.SendMessageRequest(
                        peer=await self.client.get_input_entity(entity), message=self.job['message'],
                        random_id=rid, no_webpage=True))
                    item.update(status='sent', detail='Enviado', sent_at=time.time())
                except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                    item.update(status='pending', detail='Telegram pediu uma pausa.')
                    self.job.update(status='paused', wait_until=time.time()+exc.seconds,
                                    note=f'Telegram pediu uma pausa de {exc.seconds}s. Aguarde e depois continue os pendentes.')
                    self.save()
                    return
                except errors.PeerFloodError:
                    item.update(status='blocked', detail='Telegram restringiu envios desta conta.')
                    self.job.update(status='restricted', note='Restrição do Telegram. O envio foi encerrado; não há tentativa de contornar o bloqueio.')
                    self.save()
                    return
                except errors.RPCError as exc:
                    item.update(status='failed', detail=f'Telegram recusou: {type(exc).__name__}')
                except (ValueError, PanelError):
                    item.update(status='skipped', detail='Destino não está mais disponível para publicação.')
                except Exception:
                    uncertain = item['status'] == 'sending'
                    item.update(status='uncertain' if uncertain else 'failed',
                                detail='Falha de conexão. Confira no Telegram antes de tentar outro envio.' if uncertain else 'Não foi possível verificar este destino.')
                    self.job.update(status='interrupted', note='Envio interrompido por falha de conexão. Destinos de resultado incerto não serão repetidos.')
                    self.save()
                    return
                self.save()
                if any(i['status'] == 'pending' for i in self.job['items']):
                    if await self.wait(self.interval):
                        break
            self.job['status'] = 'cancelled' if self.stop.is_set() else 'completed'
            self.job['note'] = 'Envio parado. Mensagens já enviadas permanecem no Telegram.' if self.stop.is_set() else 'Processamento concluído. Confira os resultados de cada destino.'
            self.save()
        except asyncio.CancelledError:
            self.job.update(status='interrupted', note='Servidor encerrado. Confira os resultados antes de continuar.')
            for item in self.job['items']:
                if item['status'] == 'sending':
                    item.update(status='uncertain', detail='Resultado incerto: confirme diretamente no Telegram.')
            self.save()
            raise

    async def cancel(self):
        self.stop.set()
        if self.job and self.job['status'] in ACTIVE:
            self.job['status'] = 'stopping'
            self.job['note'] = 'Parando após a solicitação em andamento. Ela ainda pode ser enviada.'
        elif self.job and self.job['status'] in {'paused', 'interrupted'}:
            self.job['status'] = 'cancelled'
        self.save()
        return {'ok': True}

    async def resume(self):
        async with self.lock:
            me = await self.require_account()
            if not self.job or self.job['status'] not in {'paused', 'interrupted'}:
                raise PanelError('Não existe envio pausado para continuar.')
            if me.id != self.job['account_id']:
                raise PanelError('Conecte a mesma conta utilizada neste envio.')
            if time.time() < self.job.get('wait_until', 0):
                raise PanelError('Aguarde o prazo de pausa informado pelo Telegram.')
            self.stop.clear()
            self.job.update(status='running', note='', wait_until=0)
            self.save()
            self.task = asyncio.create_task(self.run())
            return self.public_job()

    async def disconnect(self):
        async with self.lock:
            await self.cancel()
            if self.task and not self.task.done():
                await asyncio.wait_for(asyncio.shield(self.task), 45)
            await self.connect()
            if not await self.client.log_out():
                raise PanelError('Telegram não confirmou a desconexão. Tente novamente ou encerre em Configurações → Dispositivos.')
            if not self.injected_client:
                self.client = self.new_client()
            self.me = None
            self.destinations = {}
            self.scan_time = None
            self.phone = self.code_hash = None
            self.pending_since = 0
            self.login_stage = 'phone'
            self.ticket = None
            return {'ok': True}

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.client.disconnect()
