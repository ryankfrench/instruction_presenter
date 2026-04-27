import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache

from instruction_presenter.manifest import get_cached_manifest, manifest_cache_key
from instruction_presenter.session_state import adjust_page


class StaffConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = str(self.scope['url_route']['kwargs']['session_id'])
        self.group_name = f'instructions_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'previous_page':
            await self.previous_page()
        elif action == 'next_page':
            await self.next_page()
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
