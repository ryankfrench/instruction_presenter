from django.urls import path

from instruction_presenter import views

app_name = 'instruction_presenter'

urlpatterns = [
    path(
        'demo/dutch-sealed-first/staff/',
        views.demo_dutch_sealed_first_staff,
        name='demo_dutch_sealed_staff',
    ),
    path(
        'demo/dutch-sealed-first/subject/',
        views.demo_dutch_sealed_first_subject,
        name='demo_dutch_sealed_subject',
    ),
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
