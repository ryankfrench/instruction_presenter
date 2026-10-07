from django.urls import path

from instruction_presenter.consumers import StaffConsumer, StaffStatusConsumer, SubjectConsumer

websocket_urlpatterns = [
    path('ws/instructions/<uuid:session_id>/status/', StaffStatusConsumer.as_asgi()),
    path('ws/instructions/<uuid:session_id>/', StaffConsumer.as_asgi()),
    path(
        'ws/instructions/<uuid:session_id>/<uuid:player_key>/',
        SubjectConsumer.as_asgi(),
    ),
]
