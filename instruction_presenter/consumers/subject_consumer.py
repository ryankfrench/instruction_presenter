import json

from channels.generic.websocket import AsyncWebsocketConsumer


class SubjectConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = str(self.scope['url_route']['kwargs']['session_id'])
        self.player_key = str(self.scope['url_route']['kwargs']['player_key'])
        self.group_name = f'instructions_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        pass

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
        complete_url = event.get('complete_url') or ''
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'end_instructions',
                    'redirect_link': complete_url,
                }
            )
        )
