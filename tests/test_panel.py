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
from service import TelegramService, PanelError, eligible_type, writable, excluded_destination
from fakes import FakeTelegram, channel, topic


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

    async def test_restore_starts_scan_without_manual_destinations(self):
        await self.svc.restore()
        self.assertIsNotNone(self.svc.scan_task)
        await self.svc.scan_task
        self.assertEqual(self.svc.scan_info['status'], 'completed')
        self.assertEqual(self.client.dialog_folders, [0, 1])
        self.assertEqual(len(self.svc.destinations), 3)
        self.assertEqual(self.client.sent, [])

    async def test_archived_groups_channels_topics_and_links(self):
        public = channel(81, 'Canal arquivado', broadcast=True)
        public.username = 'canal_teste'
        forum = channel(80, 'Comunidade arquivada')
        forum.forum = True
        self.client.archived = [public, forum, self.client.entities[2]]
        self.client.topics[80] = [topic(1, 'Geral'), topic(10, 'Boas-vindas'),
                                  topic(20, 'Fechado', closed=True), topic(30, 'Oculto', hidden=True)]
        self.client.topic_page_size = 2
        result = await self.svc.scan()
        entries = result['destinations']
        self.assertEqual(len(entries), 6)  # Existing 3 + archived channel + 2 open topics.
        self.assertEqual(self.client.dialog_folders, [0, 1])
        targets = {x['title']: x for x in entries}
        self.assertTrue(targets['Comunidade arquivada / Geral']['archived'])
        self.assertEqual(targets['Comunidade arquivada / Boas-vindas']['topic_id'], 10)
        self.assertEqual(targets['Comunidade arquivada / Boas-vindas']['link'], 'https://t.me/c/80/10')
        self.assertEqual(targets['Canal arquivado']['link'], 'https://t.me/canal_teste')
        self.assertEqual(len(self.client.topic_requests), 2)
        self.assertEqual(result['scan']['checked_topics'], 4)
        self.assertNotIn('Comunidade arquivada / Fechado', targets)
        self.assertNotIn('Comunidade arquivada / Oculto', targets)
        self.assertEqual(self.client.sent, [])

    async def test_topic_pagination_over_one_hundred(self):
        forum = channel(80, 'Forum grande')
        forum.forum = True
        self.client.archived = [forum]
        self.client.topics[80] = [topic(i, 'Topico '+str(i)) for i in range(1, 106)]
        result = await self.svc.scan()
        self.assertEqual(result['scan']['checked_topics'], 105)
        self.assertEqual(len(result['destinations']), 108)
        self.assertEqual(len(self.client.topic_requests), 2)
        self.assertEqual(self.client.topic_requests[1].offset_topic, 100)

    async def test_repeated_topic_page_is_not_reported_as_complete(self):
        forum = channel(80, 'Forum repetido')
        forum.forum = True
        self.client.archived = [forum]
        self.client.topics[80] = [topic(i, 'Topico '+str(i)) for i in range(1, 4)]
        original = type(self.client).__call__
        from telethon import functions
        class RepeatingClient(FakeTelegram):
            async def __call__(client, request):
                if isinstance(request, functions.messages.GetForumTopicsRequest):
                    request.offset_topic = 0
                    client.topic_page_size = 2
                return await original(client, request)
        self.client.__class__ = RepeatingClient
        result = await self.svc.scan()
        self.assertEqual(result['scan']['status'], 'partial')
        self.assertEqual(len(result['scan']['warnings']), 1)
        self.assertEqual(result['scan']['checked_topics'], 2)


    async def test_topic_reply_routing_and_closed_topic_recheck(self):
        forum = channel(80, 'Forum')
        forum.forum = True
        self.client.archived = [forum]
        self.client.topics[80] = [topic(1, 'Geral'), topic(10, 'Boas-vindas')]
        await self.svc.scan()
        ids = [d['id'] for d in self.svc.destinations.values() if d['topic_id']]
        draft = await self.svc.prepare('No topico certo', ids)
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(len(self.client.sent), 2)
        self.assertIsNone(self.client.sent[0].reply_to)
        self.assertEqual(self.client.sent[1].reply_to.top_msg_id, 10)
        self.client.sent.clear()
        self.client.topics[80][1].closed = True
        draft = await self.svc.prepare('Depois de fechar', ids)
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.svc.job['items'][1]['status'], 'skipped')

    async def test_existing_invite_used_without_creating_one(self):
        invite = types.ChatInviteExported(link='https://t.me/+convite_de_teste', admin_id=1234,
                                          date=datetime.now(timezone.utc))
        self.client.invites[1] = invite
        result = await self.svc.scan()
        entry = next(d for d in result['destinations'] if d['peer_id'] == '-1')
        self.assertEqual(entry['link'], invite.link)
        self.assertEqual(entry['link_kind'], 'invite')
        self.assertEqual(self.client.sent, [])

    async def test_flood_during_scan_reports_partial_and_blocks_send(self):
        self.client.scan_failure = errors.FloodWaitError(None, 120)
        result = await self.svc.scan()
        self.assertEqual(result['scan']['status'], 'partial')
        self.assertEqual(len(result['destinations']), 3)
        with self.assertRaises(PanelError):
            await self.svc.prepare('Nao enviar durante pausa', list(self.svc.destinations))


    async def test_zenitrylogs_excluded_from_main_archive_topics_and_links(self):
        named = channel(91, 'Zenitry Logs - avisos', broadcast=True)
        alias = channel(92, 'Outro nome')
        alias.username = 'ZENITRYLOGS'
        forum = channel(93, 'Forum permitido')
        forum.forum = True
        self.client.entities.append(named)
        self.client.archived.extend([alias, forum])
        self.client.topics[93] = [topic(1, 'Geral'), topic(10, 'ZenitryLogs novidades')]
        self.client.invites[1] = types.ChatInviteExported(link='https://t.me/zenitrylogs', admin_id=1234, date=None)
        result = await self.svc.scan()
        self.assertEqual(result['scan']['status'], 'completed')
        self.assertTrue(all(not excluded_destination(entry=d) for d in result['destinations']))
        self.assertEqual(len(result['destinations']), 3)  # 2 allowed originals + forum general.
        self.assertFalse(eligible_type(named))
        self.assertFalse(eligible_type(alias))
        self.assertNotIn('-1000000000091', self.svc.destinations)
        self.assertEqual(self.client.sent, [])

    async def test_renamed_destination_blocked_before_send(self):
        draft = await self.prepare_all()
        self.client.entities[2].title = 'ZENITRYLOGS'
        await self.svc.start(draft['ticket'])
        await self.svc.task
        self.assertEqual(self.svc.job['items'][0]['status'], 'skipped')
        self.assertEqual(len(self.client.sent), 2)

    async def test_persisted_excluded_records_removed_on_restart(self):
        self.svc.file.write_text(json.dumps({'status': 'paused', 'items': [
            {'title': 'ZenitryLogs / Geral', 'status': 'pending'},
            {'title': 'Equipe permitida', 'status': 'pending'}]}))
        restored = TelegramService(self.tmp.name, 1, 'a'*32, client=self.client)
        self.assertEqual(len(restored.job['items']), 1)
        self.assertEqual(restored.job['items'][0]['title'], 'Equipe permitida')
        self.assertNotIn('ZenitryLogs', self.svc.file.read_text())



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
        await post('scan', {})
        await self.service.scan_task
        scanned = await self.service.status()
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
