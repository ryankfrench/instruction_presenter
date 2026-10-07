import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from instruction_presenter.session_state import reset_page
from instruction_presenter.staff_sessions import build_status_payload


class StaffStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = str(self.scope['url_route']['kwargs']['session_id'])
        self.group_name = f'instructions_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_snapshot()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return
        action = data.get('action')
        if action == 'reset_instructions':
            await self.reset_instructions()
        elif action == 'close_subject':
            await self.close_subject_tabs(data.get('player_key') or '')

    async def reset_instructions(self):
        payload = await sync_to_async(build_status_payload)(self.session_id)
        if payload is None:
            await self.send_snapshot()
            return
        await reset_page(self.session_id)
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'update_page',
                'message': 1,
            },
        )

    async def close_subject_tabs(self, player_key: str):
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'close_subject',
                'player_key': player_key,
            },
        )

    async def send_snapshot(self):
        payload = await sync_to_async(build_status_payload)(self.session_id)
        if payload is None:
            await self.send(text_data=json.dumps({'type': 'session_ended'}))
            return
        payload['type'] = 'snapshot'
        await self.send(text_data=json.dumps(payload))

    async def update_page(self, event):
        await self.send_snapshot()

    async def presence_changed(self, event):
        await self.send_snapshot()

    async def end_instructions(self, event):
        await self.send(text_data=json.dumps({'type': 'session_ended'}))

    async def close_subject(self, event):
        return

    async def media_command(self, event):
        """Play and pause commands are for presenter and subject screens."""
        return
