from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Profile, Role, Tenant, TenantMembership


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin interface for tenant records."""

    list_display = ['name', 'slug', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['name', 'slug']


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    """Admin interface for tenant memberships."""

    list_display = ['user', 'tenant', 'role', 'status', 'joined_at']
    list_filter = ['role', 'status', 'tenant']
    search_fields = ['user__username', 'user__email', 'tenant__name']
    raw_id_fields = ['user', 'tenant']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    """Admin interface for role-based visibility configuration."""

    list_display = [
        'name',
        'preferred_platform',
        'is_active',
    ]
    list_filter = [
        'is_active',
        'preferred_platform',
    ]
    search_fields = ['name']
    filter_horizontal = ['users', 'groups']


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """
    Admin interface for Profile model.
    """
    list_display = [
        'user',
        'platform_role',
        'registration_completed',
        'language',
        'timezone',
        'nickname',
    ]
    list_filter = [
        'platform_role',
        'registration_completed',
        'language',
        'timezone',
    ]
    search_fields = [
        'user__username',
        'user__email',
        'nickname'
    ]
    readonly_fields = [
        'registration_token',
        'registration_token_expires'
    ]
    fieldsets = (
        ('User Information', {
            'fields': ('user',)
        }),
        ('Registration', {
            'fields': (
                'registration_completed',
                'registration_token',
                'registration_token_expires'
            )
        }),
        ('Profile Information', {
            'fields': (
                'nickname',
                'avatar_url',
                'bio',
                'language',
                'timezone',
                'preferred_platform',
                'platform_role',
            )
        }),
    )


class ProfileInline(admin.StackedInline):
    """
    Inline admin for Profile in User admin.
    """
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'
    fk_name = 'user'


class UserAdmin(BaseUserAdmin):
    """
    Custom User admin that includes Profile inline.
    """
    inlines = (ProfileInline,)

    def get_inline_instances(self, request, obj=None):
        """
        Only show Profile inline when editing existing user.
        """
        if not obj:
            return list()
        return super().get_inline_instances(request, obj)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
