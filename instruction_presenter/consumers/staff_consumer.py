import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache

from instruction_presenter.manifest import get_cached_manifest, manifest_cache_key
from instruction_presenter.session_state import (
    adjust_page,
    begin_playback,
    get_current_page,
    media_command_for_join,
    pause_playback,
    unregister_session,
    video_ready_counts,
)


class StaffConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = str(self.scope['url_route']['kwargs']['session_id'])
        self.group_name = f'instructions_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_join_media()
        await self.send_video_ready_counts()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'previous_page':
            await self.previous_page()
        elif action == 'next_page':
            await self.next_page()
        elif action == 'play_media':
            await self.play_media()
        elif action == 'pause_media':
            await self.pause_media()
        elif action == 'end_instructions':
            await self.send_end_instructions()

    async def previous_page(self):
        manifest = await sync_to_async(get_cached_manifest)(self.session_id)
        if not manifest or manifest.total_pages == 0:
            return
        new_page, changed = await adjust_page(self.session_id, -1, manifest.total_pages)
        if changed:
            await self.send_update_page(new_page)

    async def next_page(self):
        manifest = await sync_to_async(get_cached_manifest)(self.session_id)
        if not manifest or manifest.total_pages == 0:
            return
        new_page, changed = await adjust_page(self.session_id, 1, manifest.total_pages)
        if changed:
            await self.send_update_page(new_page)

    async def send_end_instructions(self):
        manifest = await sync_to_async(get_cached_manifest)(self.session_id)
        if not manifest:
            await self.send(
                text_data=json.dumps({'type': 'end_instructions_failed'}),
            )
            return
        complete_url = manifest.complete_url
        staff_url = manifest.staff_complete_url

        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'end_instructions',
                'complete_url': complete_url,
                'staff_complete_url': staff_url,
            },
        )

        # Drop cached manifest so session cannot be resumed accidentally
        await sync_to_async(cache.delete)(manifest_cache_key(self.session_id))
        unregister_session(self.session_id)

    async def current_page_is_video(self) -> bool:
        manifest = await sync_to_async(get_cached_manifest)(self.session_id)
        if not manifest or manifest.total_pages == 0:
            return False
        page = await get_current_page(self.session_id)
        page = min(max(page, 1), manifest.total_pages)
        return manifest.media_kind_for_page(page) == 'video'

    async def play_media(self):
        if not await self.current_page_is_video():
            return
        await self.broadcast_media(begin_playback(self.session_id))

    async def pause_media(self):
        if not await self.current_page_is_video():
            return
        await self.broadcast_media(pause_playback(self.session_id))

    async def broadcast_media(self, payload: dict):
        event = {
            'type': 'media_command',
            'command': payload['command'],
            'position': payload['position'],
            'server_time': payload['server_time'],
        }
        if 'play_at' in payload:
            event['play_at'] = payload['play_at']
        await self.channel_layer.group_send(self.group_name, event)

    async def send_join_media(self):
        if not await self.current_page_is_video():
            return
        payload = media_command_for_join(self.session_id)
        if payload:
            await self.send(text_data=json.dumps(payload))

    async def send_video_ready_counts(self):
        if not await self.current_page_is_video():
            return
        enabled, total = video_ready_counts(self.session_id)
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'video_enabled',
                    'enabled': enabled,
                    'total': total,
                }
            )
        )

    async def send_update_page(self, page_number):
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'update_page',
                'message': page_number,
            },
        )

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
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'end_instructions',
                    'complete_url': event.get('complete_url', ''),
                    'staff_complete_url': event.get('staff_complete_url'),
                }
            )
        )

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

    async def presence_changed(self, event):
        """Subject connect and disconnect update the ready count on video pages."""
        await self.send_video_ready_counts()

    async def video_enabled(self, event):
        await self.send_video_ready_counts()

    async def close_subject(self, event):
        """Close-tab commands are delivered to subject screens."""
        return
