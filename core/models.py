import secrets
from django.db import models


def gerar_token():
    return secrets.token_urlsafe(24)


class Sistema(models.Model):
    TIPO_CHOICES = [
        ('ASSINATURA', 'Assinatura - Cobra mensalidade'),
        ('VENDA', 'Venda - Só recebe comissão'),
    ]
    codigo = models.SlugField(unique=True, help_text="Ex: fisio, catalogo, petshop, loja_livros - SEM espaço")
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    token = models.CharField(max_length=64, unique=True, default=gerar_token,
                              help_text="Token que este sistema usa para chamar a central")
    notificar_url = models.URLField(blank=True,
                              help_text="URL do sistema que a central avisa quando o status da assinatura muda (opcional)")
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nome} ({self.codigo})"


class Plano(models.Model):
    CICLO_CHOICES = [
        ('MONTHLY', 'Mensal'),
        ('QUARTERLY', 'Trimestral (3 meses)'),
        ('SEMIANNUALLY', 'Semestral (6 meses)'),
        ('YEARLY', 'Anual (12 meses)'),
        ('WEEKLY', 'Semanal'),
    ]
    sistema = models.ForeignKey(Sistema, on_delete=models.CASCADE, related_name='planos')
    codigo = models.SlugField(help_text="Ex: mensal, trimestral, anual")
    nome = models.CharField(max_length=100, help_text="Ex: Plano Mensal - R$49")
    descricao = models.TextField(blank=True, help_text="Descrição exibida na página de planos do sistema")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    ciclo = models.CharField(max_length=20, choices=CICLO_CHOICES, default='MONTHLY')
    dias_trial = models.IntegerField(default=7, help_text="0 = sem trial")
    ativo = models.BooleanField(default=True)

    class Meta:
        unique_together = ('sistema', 'codigo')

    def __str__(self):
        return f"{self.sistema.codigo} - {self.nome} R${self.valor}"


class Assinatura(models.Model):
    STATUS_CHOICES = [
        ('trial', 'Em Trial'),
        ('ativa', 'Ativa'),
        ('atrasada', 'Atrasada'),
        ('cancelada', 'Cancelada'),
        ('inativa', 'Inativa'),
    ]
    sistema = models.ForeignKey(Sistema, on_delete=models.CASCADE)
    plano = models.ForeignKey(Plano, on_delete=models.SET_NULL, null=True)

    email = models.EmailField(db_index=True)
    nome = models.CharField(max_length=200, blank=True)
    cpf_cnpj = models.CharField(max_length=20, blank=True)
    referencia = models.CharField(max_length=200, blank=True,
                              help_text="Chave externa do objeto no sistema (ex: listing:12)")

    asaas_customer_id = models.CharField(max_length=100, blank=True)
    asaas_subscription_id = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trial')
    inicio_em = models.DateTimeField(auto_now_add=True)
    trial_fim = models.DateField(null=True, blank=True)
    proximo_vencimento = models.DateField(null=True, blank=True)
    ciclo_asaas = models.CharField(max_length=20, blank=True, default='MONTHLY')

    def __str__(self):
        return f"{self.email} - {self.sistema.codigo} - {self.status}"


class Venda(models.Model):
    sistema = models.ForeignKey(Sistema, on_delete=models.CASCADE, help_text="Só sistemas tipo VENDA")
    valor_total = models.DecimalField(max_digits=10, decimal_places=2)
    valor_comissao = models.DecimalField(max_digits=10, decimal_places=2)
    email_comprador = models.EmailField(blank=True)
    dados_extras = models.JSONField(default=dict, blank=True, help_text="Guarda JSON que veio da loja")
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sistema.codigo} - R${self.valor_total} em {self.criado_em.date()}"


class WebhookLog(models.Model):
    payload = models.JSONField()
    sistema = models.ForeignKey(Sistema, on_delete=models.SET_NULL, null=True, blank=True)
    tipo = models.CharField(max_length=100, blank=True, help_text="Ex: asaas:PAYMENT_CONFIRMED, evento:EVENTO_CRIADO")
    evento_id = models.CharField(max_length=100, blank=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    processado = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.tipo or 'webhook'} {self.criado_em}"