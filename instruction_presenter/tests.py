import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from instruction_presenter.manifest import (
    InstructionManifest,
    get_cached_manifest,
    set_cached_manifest,
)
from instruction_presenter.routing import websocket_urlpatterns
from instruction_presenter.session_state import (
    clear_ephemeral_state,
    get_current_page_sync,
    list_subject_groups,
    register_subject,
    registered_sessions,
    set_current_page,
    unregister_subject,
)
from instruction_presenter.staff_sessions import index_rows

_ws_application = URLRouter(websocket_urlpatterns)


class StaffIndexTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_ephemeral_state()
        self.user = User.objects.create_user('staffer', password='secret-pass')

    def test_index_requires_login(self):
        response = self.client.get(reverse('instruction_presenter:staff_index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_login_page_renders(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'Staff sign in')

    def test_create_session_and_open_presenter_without_login(self):
        self.client.login(username='staffer', password='secret-pass')
        response = self.client.post(
            reverse('instruction_presenter:staff_index'),
            {
                'base_url': 'https://localhost/static/deck/',
                'page_count': '2',
                'complete_url': 'https://localhost/done',
                'staff_complete_url': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        created = parse_qs(urlsplit(response.url).query)['created'][0]
        follow = self.client.get(response.url)
        self.assertContains(follow, 'Session created')
        self.assertContains(follow, 'page001.pdf')
        self.assertContains(follow, 'page002.pdf')
        self.assertContains(follow, '{player_key}')
        self.assertContains(follow, 'complete_url=https%3A%2F%2Flocalhost%2Fdone')

        self.client.logout()
        presenter = self.client.get(
            reverse('instruction_presenter:staff_home', args=[created])
        )
        self.assertEqual(presenter.status_code, 200)
        self.assertContains(presenter, 'Previous')
        status = self.client.get(
            reverse('instruction_presenter:staff_status', args=[created])
        )
        self.assertEqual(status.status_code, 200)
        self.assertContains(status, 'Session status')
        self.assertContains(status, 'page001.pdf')

    def test_rejects_disallowed_completion_url(self):
        self.client.login(username='staffer', password='secret-pass')
        response = self.client.post(
            reverse('instruction_presenter:staff_index'),
            {
                'base_url': 'https://localhost/static/deck/',
                'page_count': '1',
                'complete_url': 'https://evil.example/phish',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid base URL')
        self.assertEqual(index_rows(), [])

    def test_subject_link_loads_without_complete_url(self):
        session_id = uuid4()
        player_key = uuid4()
        set_cached_manifest(
            str(session_id),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf',),
                complete_url='',
                staff_complete_url=None,
            ),
        )
        response = self.client.get(
            reverse(
                'instruction_presenter:subject_home',
                args=[session_id, player_key],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page 1')

    def test_direct_presenter_link_registers_session(self):
        session_id = uuid4()
        response = self.client.get(
            reverse('instruction_presenter:staff_home', args=[session_id]),
            {
                'base_url': 'https://localhost/static/deck/',
                'f': ['page001.pdf', 'page002.pdf'],
            },
        )
        self.assertEqual(response.status_code, 200)
        rows = index_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['session_id'], str(session_id))
        self.assertIn('page001.pdf', rows[0]['presenter_path'])


class SessionRegistryTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_ephemeral_state()

    def test_manifest_cache_registers_and_missing_cache_drops_row(self):
        session_id = str(uuid4())
        set_cached_manifest(
            session_id,
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf',),
                complete_url='',
                staff_complete_url=None,
            ),
        )
        self.assertEqual(len(registered_sessions()), 1)
        cache.clear()
        self.assertEqual(index_rows(), [])
        self.assertEqual(registered_sessions(), [])

    def test_subject_presence_groups_by_player_key(self):
        session_id = str(uuid4())
        player_key = str(uuid4())
        register_subject(session_id, player_key, 'chan-1')
        register_subject(session_id, player_key, 'chan-2')
        rows = list_subject_groups(session_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['connection_count'], 2)
        unregister_subject(session_id, 'chan-1')
        self.assertEqual(list_subject_groups(session_id)[0]['connection_count'], 1)


class StatusSocketTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_ephemeral_state()
        self.session_id = str(uuid4())
        self.player_key = str(uuid4())
        set_cached_manifest(
            self.session_id,
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf', 'page002.pdf'),
                complete_url='https://localhost/done',
                staff_complete_url='https://localhost/staff-done',
            ),
        )

    def test_reset_close_and_end(self):
        async_to_sync(self._exercise)()

    async def _exercise(self):
        from channels.testing import WebsocketCommunicator

        await set_current_page(self.session_id, 2)
        status = WebsocketCommunicator(
            _ws_application,
            f'/ws/instructions/{self.session_id}/status/',
        )
        subject = WebsocketCommunicator(
            _ws_application,
            f'/ws/instructions/{self.session_id}/{self.player_key}/',
        )
        staff = WebsocketCommunicator(
            _ws_application,
            f'/ws/instructions/{self.session_id}/',
        )
        self.assertTrue((await subject.connect())[0])
        self.assertTrue((await staff.connect())[0])
        self.assertTrue((await status.connect())[0])

        snapshot = await status.receive_json_from()
        self.assertEqual(snapshot['type'], 'snapshot')
        self.assertEqual(snapshot['current_page'], 2)
        self.assertEqual(snapshot['subjects'][0]['player_key'], self.player_key)

        await status.send_json_to({'action': 'reset_instructions'})
        staff_page = await staff.receive_json_from()
        subject_page = await subject.receive_json_from()
        reset_snapshot = await status.receive_json_from()
        self.assertEqual(staff_page['type'], 'update_page')
        self.assertEqual(staff_page['message'], 1)
        self.assertEqual(subject_page['message'], 1)
        self.assertEqual(reset_snapshot['current_page'], 1)
        self.assertEqual(get_current_page_sync(self.session_id), 1)

        await status.send_json_to(
            {'action': 'close_subject', 'player_key': self.player_key}
        )
        closed = await subject.receive_json_from()
        self.assertEqual(closed['type'], 'close_tab')

        await staff.send_json_to({'action': 'end_instructions'})
        ended = await status.receive_json_from()
        self.assertEqual(ended['type'], 'session_ended')
        for _ in range(20):
            if get_cached_manifest(self.session_id) is None and not registered_sessions():
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(get_cached_manifest(self.session_id))
        self.assertEqual(registered_sessions(), [])

        await status.disconnect()
        await subject.disconnect()
        await staff.disconnect()
