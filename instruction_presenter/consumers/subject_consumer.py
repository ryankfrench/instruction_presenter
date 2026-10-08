import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from instruction_presenter.manifest import (
    clear_subject_completion_url,
    get_cached_manifest,
    get_subject_completion_url,
)
from instruction_presenter.session_state import (
    get_current_page,
    mark_subject_video_enabled,
    media_command_for_join,
    register_subject,
    unregister_subject,
)


class SubjectConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = str(self.scope['url_route']['kwargs']['session_id'])
        self.player_key = str(self.scope['url_route']['kwargs']['player_key'])
        self.group_name = f'instructions_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        register_subject(self.session_id, self.player_key, self.channel_name)
        await self.channel_layer.group_send(
            self.group_name,
            {'type': 'presence_changed'},
        )
        await self.send_join_media()

    async def disconnect(self, close_code):
        unregister_subject(self.session_id, self.channel_name)
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await self.channel_layer.group_send(
            self.group_name,
            {'type': 'presence_changed'},
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return
        if data.get('action') != 'video_enabled':
            return
        if not mark_subject_video_enabled(self.session_id, self.channel_name):
            return
        await self.channel_layer.group_send(
            self.group_name,
            {'type': 'video_enabled'},
        )

    async def presence_changed(self, event):
        return

    async def video_enabled(self, event):
        """Ready counts are delivered to the presenter."""
        return

    async def close_subject(self, event):
        target = event.get('player_key') or ''
        if target and target != self.player_key:
            return
        await self.send(text_data=json.dumps({'type': 'close_tab'}))

    async def send_join_media(self):
        manifest = await sync_to_async(get_cached_manifest)(self.session_id)
        if not manifest or manifest.total_pages == 0:
            return
        page = await get_current_page(self.session_id)
        page = min(max(page, 1), manifest.total_pages)
        if manifest.media_kind_for_page(page) != 'video':
            return
        payload = media_command_for_join(self.session_id)
        if payload:
            await self.send(text_data=json.dumps(payload))

    async def media_command(self, event):
        message = {
            'type': 'media_command',
            'command': event['command'],
            'position': event['position'],
            'server_time': event['server_time'],
        }
        if event.get('play_at') is not None:
            message['play_at'] = event['play_at']
        await self.send(text_data=json.dumps(message))

    async def update_page(self, event):
        page_number = event['message']
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'update_page',
                    'message': page_number,
                }
            )
        )

    async def end_instructions(self, event):
        overlay = await sync_to_async(get_subject_completion_url)(
            self.session_id, self.player_key
        )
        complete_url = overlay or event.get('complete_url') or ''
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'end_instructions',
                    'redirect_link': complete_url,
                }
            )
        )
        await sync_to_async(clear_subject_completion_url)(
            self.session_id, self.player_key
        )
