import json
import threading
from datetime import date, timedelta
from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, render
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Sum, Count
from .models import Sistema, Plano, Assinatura, Venda, WebhookLog
from .services import asaas_service
from .services.asaas_service import AsaasError, parse_data_asaas


class PainelLoginView(LoginView):
    """Login da equipe. Depois de logar, vai ao painel (ou aviso, se não for staff)."""

    template_name = 'core/login.html'

    def get_success_url(self):
        if not self.request.user.is_staff:
            return reverse('painel_sem_acesso')
        return reverse('painel')


def painel_sem_acesso(request):
    """Aviso exibido quando um usuário logado não pertence à equipe."""
    return render(request, 'core/sem_acesso.html')


def sistema_autenticado(request, sistema_codigo):
    """ Confere se o token enviado bate com o token cadastrado para ESSE sistema """
    token = request.headers.get('X-Central-Token') or request.GET.get('token')
    if not sistema_codigo or not token:
        return None
    return Sistema.objects.filter(codigo=sistema_codigo, token=token, ativo=True).first()


@csrf_exempt
def criar_assinatura(request):
    """ Seus sistemas chamam essa URL quando usuário se cadastra """
    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'erro': 'JSON inválido'}, status=400)

    email = data.get('email')
    sistema_codigo = data.get('sistema')
    plano_codigo = data.get('plano')
    nome = data.get('nome', '')
    cpf_cnpj = data.get('cpf_cnpj', '')

    sistema = sistema_autenticado(request, sistema_codigo)
    if not sistema:
        return JsonResponse({'erro': 'Token inválido para este sistema'}, status=401)

    if not email or not plano_codigo:
        return JsonResponse({'erro': 'email e plano são obrigatórios'}, status=400)

    plano = get_object_or_404(Plano, sistema=sistema, codigo=plano_codigo, ativo=True)

    assinatura_existente = Assinatura.objects.filter(
        email=email, sistema=sistema, status__in=['trial', 'ativa', 'atrasada']
    ).first()
    if assinatura_existente:
        return JsonResponse({'ok': True, 'mensagem': 'Já existe', 'status': assinatura_existente.status})

    trial_fim = date.today() + timedelta(days=plano.dias_trial) if plano.dias_trial > 0 else date.today()

    assinatura = Assinatura.objects.create(
        sistema=sistema,
        plano=plano,
        email=email,
        nome=nome,
        cpf_cnpj=cpf_cnpj,
        status='trial' if plano.dias_trial > 0 else 'ativa',
        trial_fim=trial_fim,
        proximo_vencimento=trial_fim
    )

    return JsonResponse({
        'ok': True,
        'status': assinatura.status,
        'trial_fim': str(assinatura.trial_fim),
        'mensagem': f'Assinatura criada com {plano.dias_trial} dias de trial'
    })


def verificar_acesso(request):
    """ Catálogo, Caio, Fisio perguntam: esse e-mail pode entrar? """
    email = request.GET.get('email')
    sistema_codigo = request.GET.get('sistema')

    sistema = sistema_autenticado(request, sistema_codigo)
    if not sistema:
        return JsonResponse({'pode_acessar': False, 'motivo': 'token inválido ou sistema inexistente'}, status=401)

    if not email:
        return JsonResponse({'pode_acessar': False, 'motivo': 'email obrigatório'})

    assinatura = Assinatura.objects.filter(email=email, sistema=sistema).order_by('-inicio_em').first()

    if not assinatura:
        return JsonResponse({'pode_acessar': False, 'motivo': 'sem assinatura', 'status': 'sem_assinatura'})

    if assinatura.status in ['ativa', 'trial']:
        if assinatura.status == 'trial' and assinatura.trial_fim and assinatura.trial_fim < date.today():
            assinatura.status = 'inativa'
            assinatura.save()
            return JsonResponse({'pode_acessar': False, 'motivo': 'trial vencido', 'status': 'inativa'})
        return JsonResponse({'pode_acessar': True, 'status': assinatura.status, 'plano': assinatura.plano.codigo if assinatura.plano else ''})

    return JsonResponse({'pode_acessar': False, 'motivo': f'status {assinatura.status}', 'status': assinatura.status})


@csrf_exempt
def registrar_venda(request):
    """ Loja de Livros e Ingressos avisam que venderam """
    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    data = json.loads(request.body)
    sistema_codigo = data.get('sistema')

    sistema = sistema_autenticado(request, sistema_codigo)
    if not sistema:
        return JsonResponse({'erro': 'Token inválido para este sistema'}, status=401)

    valor_total = data.get('valor_total')
    valor_comissao = data.get('valor_comissao', 0)

    if sistema.tipo != 'VENDA':
        return JsonResponse({'erro': 'Este sistema não é do tipo VENDA'}, status=400)

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
    if not asaas_service.verificar_token_webhook(request.headers.get('asaas-access-token', '')):
        return JsonResponse({'erro': 'token de assinatura inválido'}, status=401)

    try:
        payload = json.loads(request.body)
    except Exception:
        payload = {'raw': request.body.decode()}

    evento = payload.get('event', '')
    evento_id = str(payload.get('id') or '')
    payment = payload.get('payment') or {}
    subscription = payload.get('subscription') or {}

    if evento_id:
        ja_processado = WebhookLog.objects.filter(
            tipo=f'asaas:{evento}', evento_id=evento_id, processado=True
        ).exists()
        if ja_processado:
            return JsonResponse({'ok': True, 'resultado': 'duplicado'})

    try:
        assinatura = _localizar_assinatura(payment, subscription)
        resultado = _processar_evento_asaas(evento, payment, subscription, assinatura)
    except AsaasError as e:
        return JsonResponse({'erro': str(e)}, status=500)

    WebhookLog.objects.create(
        payload=payload,
        tipo=f'asaas:{evento}',
        evento_id=evento_id,
        sistema=assinatura.sistema if assinatura else None,
        processado=True,
    )

    if assinatura:
        _notificar_sistema(assinatura, resultado, payload)

    return JsonResponse({'ok': True, 'resultado': resultado})


def _localizar_assinatura(payment, subscription):
    """Descobre a assinatura local a partir dos dados do webhook (idempotente a consulta)."""
    for referencia in (payment.get('externalReference'), subscription.get('externalReference')):
        if referencia:
            try:
                assinatura = Assinatura.objects.select_related('sistema', 'plano').filter(pk=int(referencia)).first()
                if assinatura:
                    return assinatura
            except (TypeError, ValueError):
                pass

    sub_id = payment.get('subscription') or subscription.get('id')
    if sub_id:
        return Assinatura.objects.select_related('sistema', 'plano').filter(asaas_subscription_id=sub_id).first()

    checkout_id = subscription.get('checkoutSession') or payment.get('checkoutSession')
    if checkout_id:
        return Assinatura.objects.select_related('sistema', 'plano').filter(asaas_checkout_id=checkout_id).first()

    if sub_id:
        try:
            dados = asaas_service.consultar_assinatura(sub_id)
        except AsaasError:
            return None
        checkout_id = dados.get('checkoutSession')
        if checkout_id:
            return Assinatura.objects.select_related('sistema', 'plano').filter(asaas_checkout_id=checkout_id).first()
        referencia = dados.get('externalReference')
        if referencia:
            try:
                return Assinatura.objects.select_related('sistema', 'plano').filter(pk=int(referencia)).first()
            except (TypeError, ValueError):
                return None
    return None


def _processar_evento_asaas(evento, payment, subscription, assinatura):
    """Aplica o evento do Asaas no status da assinatura. Retorna a descrição do resultado."""
    if not assinatura:
        return 'sem_assinatura_local'

    if evento in ('PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED'):
        campos = ['status']
        assinatura.status = 'ativa'
        if payment.get('id'):
            assinatura.asaas_payment_id = payment['id']
            campos.append('asaas_payment_id')
        sub_id = payment.get('subscription') or subscription.get('id')
        if sub_id:
            assinatura.asaas_subscription_id = sub_id
            campos.append('asaas_subscription_id')
        if subscription.get('cycle'):
            assinatura.ciclo_asaas = subscription['cycle']
            campos.append('ciclo_asaas')
        if subscription.get('nextDueDate'):
            assinatura.proximo_vencimento = parse_data_asaas(subscription['nextDueDate']).date()
            campos.append('proximo_vencimento')
        assinatura.save(update_fields=campos)
        return 'ativado'

    if evento == 'PAYMENT_OVERDUE':
        if assinatura.status in ('ativa', 'trial'):
            assinatura.status = 'atrasada'
            assinatura.save(update_fields=['status'])
        return 'vencido'

    if evento in ('PAYMENT_REFUNDED', 'PAYMENT_CHARGEBACK_REQUESTED',
                  'PAYMENT_CHARGEBACK_DISPUTE', 'PAYMENT_DELETED',
                  'SUBSCRIPTION_INACTIVATED', 'SUBSCRIPTION_DELETED'):
        assinatura.status = 'cancelada'
        assinatura.save(update_fields=['status'])
        return 'cancelado'

    if evento == 'SUBSCRIPTION_CREATED':
        campos = []
        sub_id = subscription.get('id')
        if sub_id:
            assinatura.asaas_subscription_id = sub_id
            campos.append('asaas_subscription_id')
        if subscription.get('cycle'):
            assinatura.ciclo_asaas = subscription['cycle']
            campos.append('ciclo_asaas')
        if subscription.get('nextDueDate'):
            assinatura.proximo_vencimento = parse_data_asaas(subscription['nextDueDate']).date()
            campos.append('proximo_vencimento')
        if campos:
            assinatura.save(update_fields=campos)
        return 'assinatura_criada'

    if evento == 'SUBSCRIPTION_EXPIRED':
        assinatura.status = 'inativa'
        assinatura.save(update_fields=['status'])
        return 'expirado'

    return 'ignorado'


def _notificar_sistema(assinatura, resultado, payload):
    """Avisa o sistema via notificar_url quando o status da assinatura muda (assíncrono)."""
    sistema = assinatura.sistema
    if not sistema or not sistema.notificar_url:
        return

    dados = {
        'evento': 'status_assinatura',
        'resultado': resultado,
        'assinatura': {
            'id': assinatura.id,
            'email': assinatura.email,
            'status': assinatura.status,
            'plano': assinatura.plano.codigo if assinatura.plano else '',
            'referencia': assinatura.referencia,
            'proximo_vencimento': str(assinatura.proximo_vencimento) if assinatura.proximo_vencimento else None,
        },
        'payload': payload,
    }

    def _post():
        try:
            import requests as rq
            rq.post(sistema.notificar_url, json=dados, timeout=8)
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


@csrf_exempt
def planos_api(request):
    """GET: lista os planos ativos do sistema autenticado.
    POST: cria/atualiza os planos do sistema (usado pelos sistemas p/ registrar seus planos)."""
    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        return JsonResponse({'erro': 'JSON inválido'}, status=400)

    sistema_codigo = data.get('sistema') or request.GET.get('sistema')
    sistema = sistema_autenticado(request, sistema_codigo)
    if not sistema:
        return JsonResponse({'erro': 'Token inválido para este sistema'}, status=401)

    if request.method == 'POST':
        registrados = _upsert_planos(sistema, data)
        return JsonResponse({'ok': True, 'registrados': registrados})

    planos = Plano.objects.filter(sistema=sistema, ativo=True).order_by('valor')
    return JsonResponse({
        'ok': True,
        'sistema': sistema.codigo,
        'planos': [
            {
                'codigo': p.codigo,
                'nome': p.nome,
                'descricao': p.descricao,
                'valor': str(p.valor),
                'ciclo': p.ciclo,
                'dias_trial': p.dias_trial,
            }
            for p in planos
        ],
    })


def _upsert_planos(sistema, data):
    lista = data.get('planos') or ([data] if data.get('codigo') else [])
    registrados = []
    for item in lista:
        if not item.get('codigo'):
            continue
        plano, _ = Plano.objects.update_or_create(
            sistema=sistema,
            codigo=item['codigo'],
            defaults={
                'nome': item.get('nome', item['codigo']),
                'descricao': item.get('descricao', ''),
                'valor': item.get('valor', 0),
                'ciclo': item.get('ciclo', 'MONTHLY'),
                'dias_trial': item.get('dias_trial', 0),
                'ativo': item.get('ativo', True),
            },
        )
        registrados.append(plano.codigo)
    return registrados


@csrf_exempt
def criar_checkout(request):
    """Gera o pagamento no Asaas e devolve o link pro cliente.

    Body: {sistema, plano, email, nome, cpf_cnpj, recorrente(bool),
           url_sucesso, url_cancelamento, dias_vigor(int, p/ one-off)}
    """
    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'erro': 'JSON inválido'}, status=400)

    sistema = sistema_autenticado(request, data.get('sistema'))
    if not sistema:
        return JsonResponse({'erro': 'Token inválido para este sistema'}, status=401)

    email = data.get('email', '').strip()
    plano_codigo = data.get('plano', '').strip()
    if not email or not plano_codigo:
        return JsonResponse({'erro': 'email e plano são obrigatórios'}, status=400)

    plano = Plano.objects.filter(sistema=sistema, codigo=plano_codigo, ativo=True).first()
    if not plano:
        return JsonResponse({'erro': 'Plano não encontrado ou inativo'}, status=404)

    recorrente = bool(data.get('recorrente'))
    dias_vigor = data.get('dias_vigor') or plano.dias_trial or 30

    try:
        customer_id = asaas_service.criar_ou_buscar_cliente(
            nome=data.get('nome', '') or email,
            email=email,
            cpf_cnpj=data.get('cpf_cnpj', ''),
        )
    except AsaasError as e:
        return JsonResponse({'erro': f'erro ao criar cliente: {e.descricao}'}, status=502)

    assinatura = Assinatura.objects.filter(
        email=email, sistema=sistema, status='pendente'
    ).order_by('-inicio_em').first()
    if not assinatura:
        assinatura = Assinatura.objects.create(
            sistema=sistema,
            plano=plano,
            email=email,
            nome=data.get('nome', ''),
            cpf_cnpj=data.get('cpf_cnpj', ''),
            referencia=data.get('referencia', ''),
            status='pendente',
            proximo_vencimento=date.today() + timedelta(days=dias_vigor) if not recorrente else None,
        )

    url_sucesso = data.get('url_sucesso') or ''
    url_cancelamento = data.get('url_cancelamento') or ''

    try:
        resultado = asaas_service.criar_checkout(
            customer_id=customer_id,
            valor=plano.valor,
            descricao=plano.nome,
            url_sucesso=url_sucesso,
            url_cancelamento=url_cancelamento,
            ciclo=plano.ciclo,
            recorrente=recorrente,
            external_reference=assinatura.id,
        )
    except AsaasError as e:
        return JsonResponse({'erro': f'erro ao gerar checkout: {e.descricao}'}, status=502)

    assinatura.asaas_customer_id = customer_id
    assinatura.asaas_checkout_id = resultado.get('checkout_id') or ''
    assinatura.asaas_payment_id = assinatura.asaas_checkout_id or ''
    assinatura.save(update_fields=['asaas_customer_id', 'asaas_checkout_id', 'asaas_payment_id'])

    if not resultado.get('link'):
        return JsonResponse({'erro': 'Asaas não retornou link de pagamento'}, status=502)

    return JsonResponse({
        'ok': True,
        'link': resultado['link'],
        'checkout_id': resultado.get('checkout_id'),
        'assinatura_id': assinatura.id,
        'status': assinatura.status,
    })


@csrf_exempt
def registrar_evento(request):
    """Sistemas avisam a central de eventos genéricos (ex: evento criado no ticket)."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Use POST'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'erro': 'JSON inválido'}, status=400)

    sistema = sistema_autenticado(request, data.get('sistema'))
    if not sistema:
        return JsonResponse({'erro': 'Token inválido para este sistema'}, status=401)

    tipo = data.get('tipo', '').strip() or 'EVENTO'
    WebhookLog.objects.create(
        payload=data.get('dados', data),
        sistema=sistema,
        tipo=f'evento:{tipo}',
        evento_id=str(data.get('evento_id', '')),
        processado=True,
    )
    return JsonResponse({'ok': True})


def filtrar_assinaturas(queryset, request):
    """ Aplica os filtros de nome, data de início, trial, vencimento e status """
    params = request.GET
    q = params.get('q', '').strip()
    status = params.get('status', '').strip()
    sistema_codigo = params.get('sistema', '').strip()

    if sistema_codigo:
        queryset = queryset.filter(sistema__codigo=sistema_codigo)

    datas = {
        'inicio_de': ('inicio_em__date__gte', params.get('inicio_de', '').strip()),
        'inicio_ate': ('inicio_em__date__lte', params.get('inicio_ate', '').strip()),
        'trial_de': ('trial_fim__gte', params.get('trial_de', '').strip()),
        'trial_ate': ('trial_fim__lte', params.get('trial_ate', '').strip()),
        'venc_de': ('proximo_vencimento__gte', params.get('venc_de', '').strip()),
        'venc_ate': ('proximo_vencimento__lte', params.get('venc_ate', '').strip()),
        'status': ('status', status),
    }

    if q:
        queryset = queryset.filter(nome__icontains=q)

    for filtro, valor in datas.items():
        if valor[1]:
            queryset = queryset.filter(**{valor[0]: valor[1]})

    return queryset


def listagem_usuarios(request, queryset):
    """ Pagina a listagem de usuários mantendo os filtros no GET """
    queryset = filtrar_assinaturas(queryset, request).select_related('sistema', 'plano').order_by('-inicio_em')
    paginator = Paginator(queryset, 25)
    return paginator.get_page(request.GET.get('page'))


@staff_member_required(login_url='/login/')
def painel_geral(request):
    """ Painel interno: visão geral de sistemas, assinaturas e vendas """
    sistemas = Sistema.objects.all().order_by('nome')

    assinaturas_por_status = (
        Assinatura.objects.values('status').annotate(total=Count('id')).order_by('status')
    )

    vendas_totais = Venda.objects.aggregate(
        soma_total=Sum('valor_total'),
        soma_comissao=Sum('valor_comissao'),
    )

    total_assinaturas = Assinatura.objects.count()
    usuarios = listagem_usuarios(request, Assinatura.objects.all())
    eventos = WebhookLog.objects.select_related('sistema').order_by('-criado_em')[:25]

    context = {
        'sistemas': sistemas,
        'total_sistemas': sistemas.count(),
        'total_sistemas_ativos': sistemas.filter(ativo=True).count(),
        'assinaturas_por_status': assinaturas_por_status,
        'total_assinaturas': total_assinaturas,
        'total_vendas': Venda.objects.count(),
        'soma_vendas_total': vendas_totais['soma_total'] or 0,
        'soma_vendas_comissao': vendas_totais['soma_comissao'] or 0,
        'webhooks_recebidos': WebhookLog.objects.count(),
        'ultimas_assinaturas': Assinatura.objects.select_related('sistema', 'plano').order_by('-inicio_em')[:10],
        'ultimas_vendas': Venda.objects.select_related('sistema').order_by('-criado_em')[:10],
        'ultimos_eventos': eventos,
        'usuarios': usuarios,
        'mostrar_filtro_sistema': True,
        'status_choices': Assinatura.STATUS_CHOICES,
    }
    return render(request, 'core/painel.html', context)


@staff_member_required(login_url='/login/')
def painel_sistema(request, sistema_codigo):
    """ Página de UM sistema: dados dele + usuários (assinaturas) com filtros """
    sistema = get_object_or_404(Sistema, codigo=sistema_codigo)

    usuarios = listagem_usuarios(
        request,
        Assinatura.objects.filter(sistema=sistema),
    )

    assinaturas_por_status = (
        Assinatura.objects.filter(sistema=sistema).values('status').annotate(total=Count('id')).order_by('status')
    )

    vendas = Venda.objects.filter(sistema=sistema).aggregate(
        soma_total=Sum('valor_total'),
        soma_comissao=Sum('valor_comissao'),
        total=Count('id'),
    )

    context = {
        'sistema': sistema,
        'planos': sistema.planos.all().order_by('nome'),
        'usuarios': usuarios,
        'assinaturas_por_status': assinaturas_por_status,
        'total_assinaturas': Assinatura.objects.filter(sistema=sistema).count(),
        'vendas': vendas,
        'mostrar_filtro_sistema': False,
        'status_choices': Assinatura.STATUS_CHOICES,
    }
    return render(request, 'core/painel_sistema.html', context)