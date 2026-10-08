"""Persisted instruction sessions, keyed by the staff-screen GUID."""

from __future__ import annotations

import uuid

from django.db import models


class InstructionSession(models.Model):
    """One presenter URL. Ending instructions resets the page instead of deleting the row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    base_url = models.TextField()
    files = models.JSONField()
    complete_url = models.TextField(blank=True, default='')
    staff_complete_url = models.TextField(null=True, blank=True)
    current_page = models.PositiveIntegerField(default=1)
    media_playing = models.BooleanField(default=False)
    media_position = models.FloatField(default=0)
    media_anchor = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return str(self.id)


class SubjectCompletion(models.Model):
    """Per-subject completion URL overlay for one player key."""

    session = models.ForeignKey(
        InstructionSession,
        on_delete=models.CASCADE,
        related_name='completions',
    )
    player_key = models.UUIDField()
    complete_url = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'player_key'],
                name='unique_subject_completion',
            )
        ]

    def __str__(self) -> str:
        return f'{self.session_id} {self.player_key}'
