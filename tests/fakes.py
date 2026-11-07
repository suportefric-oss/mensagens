from types import SimpleNamespace
from datetime import datetime, timezone
from telethon import types, utils, errors
from telethon.tl.custom import ParticipantPermissions


def channel(id, title, broadcast=False, allowed=True, creator=False):
    return types.Channel(id=id, title=title, photo=types.ChatPhotoEmpty(), date=None,
                         broadcast=broadcast, megagroup=not broadcast, creator=creator,
                         admin_rights=types.ChatAdminRights(post_messages=allowed) if broadcast else None)


class FakeTelegram:
    def __init__(self):
        self.connected = False
        self.authenticated = True
        self.me = types.User(id=1234, first_name='Conta de teste', username='conta_teste', is_self=True)
        group = types.Chat(id=1, title='Equipe de trabalho', photo=types.ChatPhotoEmpty(), participants_count=5, date=None, version=1)
        muted = channel(4, 'Grupo sem permissão')
        muted.default_banned_rights = types.ChatBannedRights(until_date=None, send_plain=True)
        self.entities = [types.User(333, first_name='Conversa privada'), self.me, group,
                         channel(2, 'Canal de avisos', broadcast=True),
                         channel(3, 'Canal somente leitura', broadcast=True, allowed=False),
                         muted, channel(5, 'Comunidade de projetos')]
        self.sent = []
        self.failures = []
        self.require_2fa = False
        self.permissions = {}
        self.requested_code = False

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.authenticated

    async def get_me(self):
        return self.me

    async def send_code_request(self, phone):
        self.requested_code = True
        return SimpleNamespace(phone_code_hash='test-only-hash')

    async def sign_in(self, **kwargs):
        if kwargs.get('code') and self.require_2fa:
            raise errors.SessionPasswordNeededError(None)
        if kwargs.get('password') or kwargs.get('code'):
            self.authenticated = True

    async def log_out(self):
        self.authenticated = False
        return True

    async def iter_dialogs(self):
        for entity in self.entities:
            yield SimpleNamespace(entity=entity, name=getattr(entity, 'title', 'privado'))

    async def get_permissions(self, entity, who):
        if entity.id in self.permissions:
            return self.permissions[entity.id]
        if isinstance(entity, types.Chat):
            part = types.ChatParticipant(user_id=self.me.id, inviter_id=77, date=None)
        elif entity.creator:
            part = types.ChannelParticipantCreator(user_id=self.me.id, admin_rights=types.ChatAdminRights(post_messages=True))
        elif entity.broadcast:
            part = types.ChannelParticipantAdmin(user_id=self.me.id, promoted_by=77, date=None, admin_rights=entity.admin_rights)
        else:
            part = types.ChannelParticipant(user_id=self.me.id, date=None)
        return ParticipantPermissions(part, isinstance(entity, types.Chat))

    async def get_entity(self, peer_id):
        return next(e for e in self.entities if utils.get_peer_id(e) == peer_id)

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request):
        if self.failures:
            failure = self.failures.pop(0)
            if failure:
                raise failure
        self.sent.append(request)
        return True
