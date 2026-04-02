from django.contrib import admin
from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ['number', 'name']


@admin.register(MapPoint)
class MapPointAdmin(admin.ModelAdmin):
    list_display = ['floor', 'point_type', 'label', 'x', 'y', 'resource', 'company']
    list_filter = ['point_type', 'floor']


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ['request_type', 'status', 'urgency', 'user', 'floor', 'created_at']
    list_filter = ['request_type', 'status', 'urgency']
    date_hierarchy = 'created_at'


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'scope', 'category', 'is_pinned', 'author', 'created_at']
    list_filter = ['scope', 'category', 'is_pinned']


@admin.register(AnnouncementRead)
class AnnouncementReadAdmin(admin.ModelAdmin):
    list_display = ['announcement', 'user', 'created_at']
