from django.contrib import admin

from instruction_presenter.models import InstructionSession, SubjectCompletion


class SubjectCompletionInline(admin.TabularInline):
    model = SubjectCompletion
    extra = 0


@admin.register(InstructionSession)
class InstructionSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'current_page', 'base_url', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [SubjectCompletionInline]


@admin.register(SubjectCompletion)
class SubjectCompletionAdmin(admin.ModelAdmin):
    list_display = ('session', 'player_key', 'complete_url')
