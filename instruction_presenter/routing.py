from django.urls import path

from instruction_presenter.consumers import StaffConsumer, SubjectConsumer

websocket_urlpatterns = [
    path('ws/instructions/<uuid:session_id>/', StaffConsumer.as_asgi()),
    path(
        'ws/instructions/<uuid:session_id>/<uuid:player_key>/',
        SubjectConsumer.as_asgi(),
    ),
]
