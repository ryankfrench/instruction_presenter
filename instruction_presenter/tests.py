import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

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
    PLAY_LEAD_SECONDS,
    adjust_page,
    begin_playback,
    clear_ephemeral_state,
    get_current_page_sync,
    get_media_clock,
    list_subject_groups,
    media_command_for_join,
    pause_playback,
    register_subject,
    registered_sessions,
    set_current_page,
    unregister_subject,
)
from instruction_presenter.staff_sessions import index_rows

_ws_application = URLRouter(websocket_urlpatterns)


def _player_key_from_subject_path(url: str) -> str:
    player_key = urlsplit(url).path.rstrip('/').split('/')[-1]
    UUID(player_key)
    return player_key


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
        self.assertContains(follow, 'Copy join link')
        self.assertContains(follow, f'/instructions/{created}/subject/?')

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
        self.assertContains(status, 'Subject join URL')
        self.assertContains(status, f'/instructions/{created}/subject/?')

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

    def test_subject_join_assigns_distinct_player_keys(self):
        session_id = uuid4()
        set_cached_manifest(
            str(session_id),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf',),
                complete_url='',
                staff_complete_url=None,
            ),
        )
        join_url = reverse('instruction_presenter:subject_join', args=[session_id])
        first = self.client.get(join_url)
        second = self.client.get(join_url)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        first_key = _player_key_from_subject_path(first.url)
        second_key = _player_key_from_subject_path(second.url)
        self.assertNotEqual(first_key, second_key)
        followed = self.client.get(first.url)
        self.assertEqual(followed.status_code, 200)
        self.assertContains(followed, 'Page 1')

    def test_subject_join_preserves_complete_url(self):
        session_id = uuid4()
        set_cached_manifest(
            str(session_id),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf',),
                complete_url='https://localhost/session-done',
                staff_complete_url=None,
            ),
        )
        response = self.client.get(
            reverse('instruction_presenter:subject_join', args=[session_id]),
            {'complete_url': 'https://localhost/done'},
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query['complete_url'], ['https://localhost/done'])
        followed = self.client.get(response.url)
        self.assertEqual(followed.status_code, 200)

    def test_subject_join_rejects_other_parameters_and_unknown_sessions(self):
        session_id = uuid4()
        set_cached_manifest(
            str(session_id),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf',),
                complete_url='',
                staff_complete_url=None,
            ),
        )
        rejected = self.client.get(
            reverse('instruction_presenter:subject_join', args=[session_id]),
            {'base_url': 'https://localhost/static/deck/'},
        )
        self.assertEqual(rejected.status_code, 400)
        missing = self.client.get(
            reverse('instruction_presenter:subject_join', args=[uuid4()])
        )
        self.assertEqual(missing.status_code, 400)

    def test_status_page_links_connected_subject_instructions(self):
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
        register_subject(str(session_id), str(player_key), 'chan-1')
        response = self.client.get(
            reverse('instruction_presenter:staff_status', args=[session_id])
        )
        instructions_url = reverse(
            'instruction_presenter:subject_home',
            args=[session_id, player_key],
        )
        self.assertContains(response, 'Instructions URL')
        self.assertContains(response, instructions_url)

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

    def test_create_session_from_file_list(self):
        self.client.login(username='staffer', password='secret-pass')
        response = self.client.post(
            reverse('instruction_presenter:staff_index'),
            {
                'base_url': 'https://localhost/static/deck/',
                'page_count': '',
                'files': 'intro.mp4\npage002.pdf',
                'complete_url': '',
                'staff_complete_url': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        follow = self.client.get(response.url)
        self.assertContains(follow, 'intro.mp4')
        self.assertContains(follow, 'page002.pdf')

    def test_create_session_rejects_unsupported_file(self):
        self.client.login(username='staffer', password='secret-pass')
        response = self.client.post(
            reverse('instruction_presenter:staff_index'),
            {
                'base_url': 'https://localhost/static/deck/',
                'page_count': '',
                'files': 'notes.txt',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Each file must be a .pdf or .mp4.')
        self.assertEqual(index_rows(), [])


class VideoPageTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_ephemeral_state()
        self.session_id = uuid4()
        self.player_key = uuid4()
        set_cached_manifest(
            str(self.session_id),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('page001.pdf', 'intro.mp4'),
                complete_url='',
                staff_complete_url=None,
            ),
        )

    def test_pdf_page_still_uses_pdf_viewer(self):
        staff = self.client.get(
            reverse('instruction_presenter:staff_home', args=[self.session_id])
        )
        subject = self.client.get(
            reverse(
                'instruction_presenter:subject_home',
                args=[self.session_id, self.player_key],
            )
        )
        self.assertContains(staff, 'pdf.min.js')
        self.assertNotContains(staff, 'id="instruction-video"')
        self.assertContains(subject, 'pdf.min.js')
        self.assertNotContains(subject, 'id="instruction-video"')

    def test_mp4_page_uses_video_player(self):
        async_to_sync(set_current_page)(str(self.session_id), 2)
        staff = self.client.get(
            reverse('instruction_presenter:staff_home', args=[self.session_id])
        )
        subject = self.client.get(
            reverse(
                'instruction_presenter:subject_home',
                args=[self.session_id, self.player_key],
            )
        )
        self.assertContains(staff, 'id="instruction-video"')
        self.assertContains(staff, 'intro.mp4')
        self.assertContains(staff, 'Click to enable video')
        self.assertContains(staff, 'id="play-btn"')
        self.assertNotContains(staff, 'pdf.min.js')
        self.assertNotContains(staff, 'Read Aloud')
        self.assertContains(subject, 'id="instruction-video"')
        self.assertContains(subject, 'intro.mp4')
        self.assertContains(subject, 'Click to enable video')
        self.assertNotContains(subject, 'id="play-btn"')
        self.assertNotContains(subject, 'pdf.min.js')

    def test_unsupported_extension_is_rejected(self):
        response = self.client.get(
            reverse('instruction_presenter:staff_home', args=[uuid4()]),
            {
                'base_url': 'https://localhost/static/deck/',
                'f': 'notes.txt',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Each file must be a .pdf or .mp4.',
            status_code=400,
        )

        bad_session = uuid4()
        set_cached_manifest(
            str(bad_session),
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('notes.txt',),
                complete_url='',
                staff_complete_url=None,
            ),
        )
        subject = self.client.get(
            reverse(
                'instruction_presenter:subject_home',
                args=[bad_session, self.player_key],
            )
        )
        self.assertEqual(subject.status_code, 400)


class MediaClockTests(TestCase):
    def setUp(self):
        clear_ephemeral_state()
        self.session_id = str(uuid4())

    def test_play_at_is_a_lead_after_server_time(self):
        payload = begin_playback(self.session_id, now=1_700_000_000.0)
        self.assertEqual(payload['command'], 'play')
        self.assertEqual(payload['position'], 0.0)
        self.assertEqual(payload['server_time'], 1_700_000_000.0)
        self.assertAlmostEqual(
            payload['play_at'] - payload['server_time'],
            PLAY_LEAD_SECONDS,
            places=5,
        )

    def test_pause_and_resume_keep_media_position(self):
        begin_playback(self.session_id, now=1000.0)
        paused = pause_playback(self.session_id, now=1002.0)
        self.assertEqual(paused['command'], 'pause')
        self.assertAlmostEqual(paused['position'], 1.6)
        resumed = begin_playback(self.session_id, now=1010.0)
        self.assertAlmostEqual(resumed['position'], 1.6)
        self.assertAlmostEqual(resumed['play_at'], 1010.4)

    def test_join_targets_the_shared_media_time(self):
        begin_playback(self.session_id, now=1000.0)
        joined = media_command_for_join(self.session_id, now=1005.0)
        self.assertEqual(joined['command'], 'play')
        self.assertAlmostEqual(joined['position'], 5.0)
        self.assertAlmostEqual(joined['play_at'], 1005.4)

    def test_page_change_clears_the_clock(self):
        begin_playback(self.session_id, now=1000.0)
        async_to_sync(adjust_page)(self.session_id, 1, 2)
        self.assertIsNone(get_media_clock(self.session_id))


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
        self.assertIn(
            f'/instructions/{self.session_id}/subject/{self.player_key}/',
            snapshot['subjects'][0]['instructions_url'],
        )
        self.assertIn('complete_url=', snapshot['subjects'][0]['instructions_url'])

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


class VideoSyncSocketTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_ephemeral_state()
        self.session_id = str(uuid4())
        self.player_key = str(uuid4())
        set_cached_manifest(
            self.session_id,
            InstructionManifest(
                base_url='https://localhost/static/deck/',
                files=('intro.mp4', 'page002.pdf'),
                complete_url='',
                staff_complete_url=None,
            ),
        )

    def test_play_pause_and_page_change(self):
        async_to_sync(self._exercise)()

    async def _exercise(self):
        from channels.testing import WebsocketCommunicator

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
        await status.receive_json_from()

        await staff.send_json_to({'action': 'play_media'})
        staff_play = await staff.receive_json_from()
        subject_play = await subject.receive_json_from()
        self.assertEqual(staff_play['type'], 'media_command')
        self.assertEqual(staff_play['command'], 'play')
        self.assertEqual(subject_play['command'], 'play')
        self.assertGreater(staff_play['play_at'], staff_play['server_time'])
        self.assertAlmostEqual(
            staff_play['play_at'] - staff_play['server_time'],
            PLAY_LEAD_SECONDS,
            places=5,
        )
        self.assertTrue(await status.receive_nothing(timeout=0.1))

        await subject.disconnect()
        subject = WebsocketCommunicator(
            _ws_application,
            f'/ws/instructions/{self.session_id}/{self.player_key}/',
        )
        self.assertTrue((await subject.connect())[0])
        joined = await subject.receive_json_from()
        self.assertEqual(joined['type'], 'media_command')
        self.assertEqual(joined['command'], 'play')
        self.assertGreater(joined['play_at'], joined['server_time'])

        await staff.send_json_to({'action': 'pause_media'})
        staff_pause = await staff.receive_json_from()
        subject_pause = await subject.receive_json_from()
        self.assertEqual(staff_pause['command'], 'pause')
        self.assertEqual(subject_pause['command'], 'pause')
        self.assertGreaterEqual(staff_pause['position'], 0)
        self.assertNotIn('play_at', staff_pause)

        await staff.send_json_to({'action': 'next_page'})
        staff_page = await staff.receive_json_from()
        subject_page = await subject.receive_json_from()
        self.assertEqual(staff_page['type'], 'update_page')
        self.assertEqual(subject_page['type'], 'update_page')
        self.assertIsNone(get_media_clock(self.session_id))
        self.assertEqual(get_current_page_sync(self.session_id), 2)

        await status.disconnect()
        await subject.disconnect()
        await staff.disconnect()
