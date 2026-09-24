from django.contrib import admin
from.models import Sistema, Plano, Assinatura, Venda, WebhookLog

class PlanoInline(admin.TabularInline):
    model = Plano
    extra = 1
    fields = ('codigo', 'nome', 'valor', 'ciclo', 'dias_trial', 'ativo')

@admin.register(Sistema)
class SistemaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nome', 'tipo', 'ativo', 'criado_em')
    list_filter = ('tipo', 'ativo')
    search_fields = ('codigo', 'nome')
    inlines = [PlanoInline]
    prepopulated_fields = {} # se quiser

@admin.register(Plano)
class PlanoAdmin(admin.ModelAdmin):
    list_display = ('sistema', 'codigo', 'nome', 'valor', 'ciclo', 'dias_trial', 'ativo')
    list_filter = ('sistema', 'ciclo', 'ativo')
    search_fields = ('codigo', 'nome')

@admin.register(Assinatura)
class AssinaturaAdmin(admin.ModelAdmin):
    list_display = ('email', 'sistema', 'plano', 'status', 'trial_fim', 'proximo_vencimento', 'inicio_em')
    list_filter = ('sistema', 'status', 'plano')
    search_fields = ('email', 'nome', 'asaas_customer_id')
    list_editable = ('status',)

@admin.register(Venda)
class VendaAdmin(admin.ModelAdmin):
    list_display = ('sistema', 'valor_total', 'valor_comissao', 'email_comprador', 'criado_em')
    list_filter = ('sistema',)
    search_fields = ('email_comprador',)

@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'processado', 'criado_em')
    readonly_fields = ('payload',)