import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from aiohttp.test_utils import TestClient, TestServer
from telethon import types, errors, utils
from telethon.tl.custom import ParticipantPermissions

from app import create_app
from service import TelegramService, PanelError, eligible_type, writable
from fakes import FakeTelegram, channel


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = FakeTelegram()
        self.svc = TelegramService(self.tmp.name, 1, 'a'*32, client=self.client)
        self.svc.interval = 0.001

    async def asyncTearDown(self):
        await self.svc.close()
        self.tmp.cleanup()

    async def prepare_all(self):
        await self.svc.scan()
        return await self.svc.prepare('Olá! Publicação de teste.', list(self.svc.destinations))

    async def test_scan_excludes_private_and_unwritable(self):
        result = await self.svc.scan()
        self.assertEqual({x['title'] for x in result['destinations']}, {'Equipe de trabalho', 'Canal de avisos', 'Comunidade de projetos'})
        self.assertFalse(eligible_type(self.client.me))
        self.assertEqual(self.client.sent, [])

    async def test_send_and_duplicate_confirmation(self):
        draft = await self.prepare_all()
        self.assertEqual(self.client.sent, [])
        job = await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(len(self.client.sent), 3)
        self.assertTrue(all(eligible_type(r.peer) for r in self.client.sent))
        self.assertTrue(all(r.message == draft['message'] for r in self.client.sent))
        await self.svc.start(draft['ticket'])
        self.assertEqual(len(self.client.sent), 3)

    async def test_arbitrary_private_peer_rejected(self):
        await self.svc.scan()
        with self.assertRaises(PanelError):
            await self.svc.prepare('Não deve ir para privado', ['333'])
        self.assertEqual(self.client.sent, [])

    async def test_worker_blocks_tampered_private_peer(self):
        draft = await self.prepare_all()
        self.svc.ticket['items'] = [{'id': '333', 'title': 'Privado', 'kind': 'group', 'visibility': 'Privado'}]
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(self.svc.job['items'][0]['status'], 'skipped')
        self.assertEqual(self.client.sent, [])

    async def test_permissions_rechecked(self):
        draft = await self.prepare_all()
        for entity in self.client.entities:
            if isinstance(entity, types.Chat):
                entity.default_banned_rights = types.ChatBannedRights(until_date=None, send_messages=True)
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(self.svc.job['items'][0]['status'], 'skipped')
        self.assertEqual(len(self.client.sent), 2)

    async def test_flood_wait_pauses_and_cannot_resume_early(self):
        draft = await self.prepare_all()
        self.client.failures = [errors.FloodWaitError(None, 60)]
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(self.svc.job['status'], 'paused')
        self.assertEqual(self.client.sent, [])
        with self.assertRaises(PanelError):
            await self.svc.resume()
        with self.assertRaises(PanelError):
            await self.svc.prepare('Outro envio', list(self.svc.destinations))
        self.svc.job['wait_until'] = 0
        await self.svc.resume()
        await self.svc.task
        self.assertEqual(len(self.client.sent), 3)

    async def test_uncertain_send_not_repeated(self):
        draft = await self.prepare_all()
        self.client.failures = [ConnectionError('test')]
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(self.svc.job['status'], 'interrupted')
        self.assertEqual(self.svc.job['items'][0]['status'], 'uncertain')
        await self.svc.resume()
        await self.svc.task
        self.assertEqual(len(self.client.sent), 2)

    async def test_cancel_stops_remaining(self):
        draft = await self.prepare_all()
        original = self.client.__class__.__call__
        async def send_and_stop(request):
            result = await original(self.client, request)
            self.svc.stop.set()
            return result
        class StopClient(FakeTelegram):
            async def __call__(this, request):
                return await send_and_stop(request)
        self.client.__class__ = StopClient
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.svc.job['status'], 'cancelled')

    async def test_restart_never_repeats_in_flight_item(self):
        job = {'status': 'running', 'items': [{'status': 'sending'}]}
        self.svc.file.write_text(json.dumps(job))
        restored = TelegramService(self.tmp.name, 1, 'a'*32, client=self.client)
        self.assertEqual(restored.job['status'], 'interrupted')
        self.assertEqual(restored.job['items'][0]['status'], 'uncertain')

    async def test_two_factor_flow_and_disconnect(self):
        self.client.authenticated = False
        self.client.require_2fa = True
        self.assertEqual((await self.svc.request_code('+5521999999999'))['stage'], 'code')
        self.assertEqual((await self.svc.verify(code='12345'))['stage'], 'password')
        self.assertEqual((await self.svc.verify(password='test-only-secret'))['stage'], 'connected')
        self.assertIsNone(self.svc.phone)
        await self.svc.disconnect()
        self.assertIsNone(self.svc.me)
        self.assertEqual(self.svc.destinations, {})

    async def test_group_restrictions_and_channel_post_right(self):
        # Real Telethon permission objects, not a made-up send_messages property.
        entity = self.client.entities[-1]
        banned = types.ChannelParticipantBanned(peer=types.PeerUser(1234), kicked_by=77, date=None,
                                                banned_rights=types.ChatBannedRights(until_date=None, send_plain=True))
        self.client.permissions[entity.id] = ParticipantPermissions(banned, False)
        self.assertFalse(await writable(self.client, entity))
        creator = channel(22, 'Canal do dono', broadcast=True, allowed=False, creator=True)
        self.assertTrue(await writable(self.client, creator))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = TelegramService(self.tmp.name, 1, 'a'*32, client=FakeTelegram())
        self.service.interval = .001
        self.client = TestClient(TestServer(create_app(self.service, 'test-password-1234', secure=False)))
        await self.client.start_server()
        self.headers = {'X-Panel-Request': '1'}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def login(self):
        response = await self.client.post('/api/login', json={'password': 'test-password-1234'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.headers['X-CSRF-Token'] = (await response.json())['csrf']

    async def test_auth_csrf_static_and_no_secret_exposure(self):
        self.assertEqual((await self.client.get('/api/status')).status, 401)
        self.assertEqual((await self.client.get('/.env')).status, 404)
        self.assertEqual((await self.client.get('/data/telegram.session')).status, 404)
        await self.login()
        self.assertEqual((await self.client.post('/api/scan', json={})).status, 403)
        response = await self.client.get('/api/status')
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertNotIn('api_hash', body)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual((await self.client.post('/api/logout', json={}, headers=self.headers)).status, 200)
        self.assertEqual((await self.client.get('/api/status')).status, 401)

    async def test_entire_api_publication(self):
        await self.login()
        async def post(path, body):
            response = await self.client.post('/api/'+path, json=body, headers=self.headers)
            self.assertEqual(response.status, 200, await response.text())
            return await response.json()
        await post('restore', {})
        scanned = await post('scan', {})
        draft = await post('prepare', {'message': 'Teste de ponta a ponta', 'ids': [x['id'] for x in scanned['destinations']]})
        forbidden = await self.client.post('/api/start', json={'ticket': draft['ticket']}, headers=self.headers)
        self.assertEqual(forbidden.status, 400)
        await post('start', {'ticket': draft['ticket'], 'confirmed': True})
        await self.service.task
        status = await (await self.client.get('/api/status')).json()
        self.assertEqual(status['job']['status'], 'completed')
        self.assertEqual(len(self.service.client.sent), 3)

    async def test_login_throttling(self):
        for _ in range(10):
            response = await self.client.post('/api/login', json={'password': 'wrong'}, headers=self.headers)
            self.assertEqual(response.status, 401)
        response = await self.client.post('/api/login', json={'password': 'wrong'}, headers=self.headers)
        self.assertEqual(response.status, 429)
