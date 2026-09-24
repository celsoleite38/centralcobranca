import json
from datetime import date, timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.shortcuts import get_object_or_404
from .models import Sistema, Plano, Assinatura, Venda, WebhookLog

TOKEN_ESPERADO = getattr(settings, 'CENTRAL_TOKEN', 'innosoft_123')

def check_token(request):
    token = request.headers.get('X-Central-Token') or request.GET.get('token')
    return token == TOKEN_ESPERADO

@csrf_exempt
def criar_assinatura(request):
    """ Seus sistemas chamam essa URL quando usuário se cadastra """
    if not check_token(request):
        return JsonResponse({'erro': 'Token inválido'}, status=401)
    
    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    data = json.loads(request.body)
    email = data.get('email')
    sistema_codigo = data.get('sistema') # ex: catalogo
    plano_codigo = data.get('plano') # ex: mensal
    nome = data.get('nome', '')

    if not email or not sistema_codigo or not plano_codigo:
        return JsonResponse({'erro': 'email, sistema e plano são obrigatórios'}, status=400)

    sistema = get_object_or_404(Sistema, codigo=sistema_codigo, ativo=True)
    plano = get_object_or_404(Plano, sistema=sistema, codigo=plano_codigo, ativo=True)

    # Já existe assinatura ativa? Retorna ela
    assinatura_existente = Assinatura.objects.filter(email=email, sistema=sistema, status__in=['trial', 'ativa', 'atrasada']).first()
    if assinatura_existente:
        return JsonResponse({'ok': True, 'mensagem': 'Já existe', 'status': assinatura_existente.status})

    # Calcula fim do trial
    trial_fim = date.today() + timedelta(days=plano.dias_trial) if plano.dias_trial > 0 else date.today()

    assinatura = Assinatura.objects.create(
        sistema=sistema,
        plano=plano,
        email=email,
        nome=nome,
        status='trial' if plano.dias_trial > 0 else 'ativa',
        trial_fim=trial_fim,
        proximo_vencimento=trial_fim
    )

    # TODO: Aqui depois criamos no Asaas - por enquanto só cria local
    # criar_no_asaas(assinatura)

    return JsonResponse({
        'ok': True, 
        'status': assinatura.status,
        'trial_fim': str(assinatura.trial_fim),
        'mensagem': f'Assinatura criada com {plano.dias_trial} dias de trial'
    })

def verificar_acesso(request):
    """ Catálogo, Caio, Fisio perguntam: esse e-mail pode entrar? """
    if not check_token(request):
        return JsonResponse({'erro': 'Token inválido'}, status=401)

    email = request.GET.get('email')
    sistema_codigo = request.GET.get('sistema')

    if not email or not sistema_codigo:
        return JsonResponse({'pode_acessar': False, 'motivo': 'email e sistema obrigatórios'})

    sistema = Sistema.objects.filter(codigo=sistema_codigo).first()
    if not sistema:
        return JsonResponse({'pode_acessar': False, 'motivo': 'sistema não existe'})

    assinatura = Assinatura.objects.filter(email=email, sistema=sistema).order_by('-inicio_em').first()
    
    if not assinatura:
        return JsonResponse({'pode_acessar': False, 'motivo': 'sem assinatura', 'status': 'sem_assinatura'})

    # Regra de bloqueio
    if assinatura.status in ['ativa', 'trial']:
        # Se está em trial, verifica se venceu
        if assinatura.status == 'trial' and assinatura.trial_fim and assinatura.trial_fim < date.today():
            assinatura.status = 'inativa'
            assinatura.save()
            return JsonResponse({'pode_acessar': False, 'motivo': 'trial vencido', 'status': 'inativa'})
        return JsonResponse({'pode_acessar': True, 'status': assinatura.status, 'plano': assinatura.plano.codigo if assinatura.plano else ''})
    
    return JsonResponse({'pode_acessar': False, 'motivo': f'status {assinatura.status}', 'status': assinatura.status})

@csrf_exempt
def registrar_venda(request):
    """ Loja de Livros e Ingressos avisam que venderam """
    if not check_token(request):
        return JsonResponse({'erro': 'Token inválido'}, status=401)

    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    data = json.loads(request.body)
    sistema_codigo = data.get('sistema')
    valor_total = data.get('valor_total')
    valor_comissao = data.get('valor_comissao', 0)
    
    sistema = get_object_or_404(Sistema, codigo=sistema_codigo, tipo='VENDA')

    venda = Venda.objects.create(
        sistema=sistema,
        valor_total=valor_total,
        valor_comissao=valor_comissao,
        email_comprador=data.get('email', ''),
        dados_extras=data
    )

    return JsonResponse({'ok': True, 'id': venda.id})

@csrf_exempt
def webhook_asaas(request):
    """ Asaas avisa que pagou / atrasou / cancelou """
    try:
        payload = json.loads(request.body)
    except:
        payload = {'raw': request.body.decode()}
    
    log = WebhookLog.objects.create(payload=payload)
    
    # Exemplo: evento PAYMENT_CONFIRMED
    # Você depois mapeia o asaas_subscription_id pra Assinatura
    # Aqui é só o esqueleto por enquanto
    
    return JsonResponse({'ok': True})