from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Apenas superusuários têm acesso ao Django Admin (/admin/).
        # Staff usa somente o painel interno (/painel/).
        from django.contrib import admin

        def has_permission(self, request):
            return (
                request.user.is_active
                and request.user.is_staff
                and request.user.is_superuser
            )

        admin.site.has_permission = has_permission.__get__(admin.site, type(admin.site))
