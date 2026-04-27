from django.urls import path

from instruction_presenter import views

app_name = 'instruction_presenter'

urlpatterns = [
    path(
        'instructions/<uuid:session_id>/staff/',
        views.staff_home,
        name='staff_home',
    ),
    path(
        'instructions/<uuid:session_id>/subject/<uuid:player_key>/',
        views.subject_home,
        name='subject_home',
    ),
]
