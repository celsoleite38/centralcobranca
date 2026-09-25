from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.views.generic import RedirectView
from core.views import (
    painel_geral, painel_sistema, PainelLoginView, painel_sem_acesso,
)

urlpatterns = [
    path('', RedirectView.as_view(url='/painel/', permanent=False), name='home'),
    path('admin/', admin.site.urls),
    path('login/', PainelLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('acesso-restrito/', painel_sem_acesso, name='painel_sem_acesso'),
    path('painel/', painel_geral, name='painel'),
    path('painel/<slug:sistema_codigo>/', painel_sistema, name='painel_sistema'),
    path('api/', include('core.urls')),
]