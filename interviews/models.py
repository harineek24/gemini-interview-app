from django.db import models
from django.utils import timezone
import uuid

class Interview(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    full_transcript = models.TextField(blank=True)
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Interview {self.id} - {self.status} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"
    
    @property
    def duration_formatted(self):
        if self.duration_seconds:
            minutes = self.duration_seconds // 60
            seconds = self.duration_seconds % 60
            return f"{minutes}m {seconds}s"
        return "N/A"

class LiveTranscript(models.Model):
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name='live_transcripts')
    speaker = models.CharField(max_length=20, choices=[('user', 'User'), ('ai', 'AI')])
    text = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    sequence_number = models.IntegerField()
    
    class Meta:
        ordering = ['sequence_number']
    
    def __str__(self):
        return f"{self.speaker}: {self.text[:50]}..."
