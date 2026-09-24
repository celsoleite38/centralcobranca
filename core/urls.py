from django.urls import path
from . import views

urlpatterns = [
    path('criar-assinatura/', views.criar_assinatura),
    path('verificar-acesso/', views.verificar_acesso),
    path('registrar-venda/', views.registrar_venda),
    path('webhook/asaas/', views.webhook_asaas),
]