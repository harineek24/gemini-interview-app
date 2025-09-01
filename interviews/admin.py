from django.contrib import admin
from .models import Interview, LiveTranscript

@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'started_at', 'ended_at', 'duration_formatted', 'created_at']
    list_filter = ['status', 'started_at', 'created_at']
    search_fields = ['id', 'full_transcript', 'summary']
    readonly_fields = ['id', 'created_at', 'updated_at', 'duration_formatted']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'status', 'started_at', 'ended_at', 'duration_formatted')
        }),
        ('Content', {
            'fields': ('full_transcript', 'summary')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

@admin.register(LiveTranscript)
class LiveTranscriptAdmin(admin.ModelAdmin):
    list_display = ['interview', 'speaker', 'text_preview', 'timestamp', 'sequence_number']
    list_filter = ['speaker', 'timestamp']
    search_fields = ['text', 'interview__id']
    readonly_fields = ['timestamp']
    
    def text_preview(self, obj):
        return obj.text[:100] + "..." if len(obj.text) > 100 else obj.text
    text_preview.short_description = 'Text Preview'

# ===============================
# interviews/views.py
# ===============================
from django.shortcuts import render
from django.http import HttpResponse

def index(request):
    return HttpResponse("""
    <h1>Gemini Live Interview - Django Admin</h1>
    <p>Go to <a href="/admin/">Admin Panel</a> to view interview data.</p>
    <p>Go to <a href="/">Main Application</a> to start interviews.</p>
    """)