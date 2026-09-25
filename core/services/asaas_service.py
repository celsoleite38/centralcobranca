"""Cliente HTTP para a API do Asaas usado pela central.

Centraliza a comunicação com o gateway: customers, checkouts (recorrente e
one-off) e consultas. Padrão de erros inspirado no fisio-lab.
"""
import logging
from datetime import date, datetime, time

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

logger = logging.getLogger(__name__)

TIMEOUT = 20


class AsaasError(Exception):
    def __init__(self, mensagem, descricao=""):
        super().__init__(mensagem)
        self.descricao = descricao or mensagem


def parse_data_asaas(valor):
    """Converte datas/datetimes do Asaas em datetime consciente de timezone."""
    if not valor:
        return None
    resultado = parse_datetime(valor)
    if resultado is None:
        data = parse_date(valor)
        if data is None:
            return None
        resultado = datetime.combine(data, time.min)
    if timezone.is_naive(resultado):
        resultado = timezone.make_aware(resultado)
    return resultado


def _extrair_descricao_erro(resp):
    try:
        data = resp.json()
    except ValueError:
        return resp.text
    descricoes = [
        e.get("description") for e in (data.get("errors") or []) if e.get("description")
    ]
    if descricoes:
        return "; ".join(descricoes)
    return data.get("description") or resp.text


def _headers():
    return {
        "access_token": settings.ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "CentralCobrancas/1.0",
    }


def _base_url():
    return settings.ASAAS_BASE_URL


def verificar_token_webhook(token):
    """Valida o header 'asaas-access-token' se o token estiver configurado."""
    if not settings.ASAAS_WEBHOOK_TOKEN:
        return True
    return token == settings.ASAAS_WEBHOOK_TOKEN


def criar_ou_buscar_cliente(nome, email, cpf_cnpj, phone=None,
                            postal_code=None, address=None,
                            address_number=None, province=None, city=None):
    """Cria ou atualiza um cliente no Asaas e retorna o customer_id."""
    url = f"{_base_url()}/customers"
    payload = {
        "name": nome or email,
        "email": email,
        "cpfCnpj": cpf_cnpj,
    }
    if phone:
        payload["phone"] = phone
        payload["mobilePhone"] = phone
    if postal_code:
        payload["postalCode"] = postal_code
    if address:
        payload["address"] = address
    if address_number:
        payload["addressNumber"] = address_number
    if province:
        payload["province"] = province
    if city:
        payload["city"] = city

    try:
        resp = requests.get(url, headers=_headers(), params={"email": email}, timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                customer_id = data["data"][0]["id"]
                requests.put(
                    f"{url}/{customer_id}", headers=_headers(),
                    json=payload, timeout=TIMEOUT,
                )
                return customer_id
    except requests.RequestException:
        logger.exception("Erro ao buscar cliente no Asaas")

    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        if resp.status_code >= 400:
            descricao = _extrair_descricao_erro(resp)
            raise AsaasError(
                f"Falha ao criar cliente no Asaas [{resp.status_code}]: {descricao}",
                descricao=descricao,
            )
        return resp.json()["id"]
    except requests.RequestException as e:
        logger.exception("Erro ao criar cliente no Asaas")
        raise AsaasError(f"Falha ao criar cliente no Asaas: {e}")


def criar_checkout(customer_id, valor, descricao, url_sucesso, url_cancelamento,
                   ciclo=None, recorrente=False, external_reference=None,
                   url_expirado=None, quantidade=None):
    """Cria checkout hospedado do Asaas.

    - recorrente=True: assinatura com ciclo (MONTHLY, QUARTERLY, ...)
    - recorrente=False: cobrança única (usada p/ destaque one-off no guia)
    Retorna dict com: checkout_id, link, status.
    """
    url = f"{_base_url()}/checkouts"
    payload = {
        "billingTypes": ["CREDIT_CARD", "PIX", "BOLETO"],
        "name": descricao,
        "value": float(valor),
        "minutesToExpire": 1440,
        "customer": customer_id,
        "items": [
            {"name": descricao, "value": float(valor), "quantity": quantidade or 1}
        ],
        "externalReference": str(external_reference) if external_reference else None,
        "callback": {
            "successUrl": url_sucesso,
            "cancelUrl": url_cancelamento,
            "expiredUrl": url_expirado or url_cancelamento,
            "autoRedirect": True,
        },
    }
    if recorrente:
        payload["chargeTypes"] = ["RECURRENT"]
        payload["subscription"] = {
            "cycle": ciclo or "MONTHLY",
            "nextDueDate": date.today().isoformat(),
        }
    else:
        import datetime as dt
        payload["chargeTypes"] = ["DETACHED"]
        payload["dueDate"] = dt.date.today().isoformat()

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code >= 400:
            descricao_erro = _extrair_descricao_erro(resp)
            raise AsaasError(
                f"Falha ao criar checkout no Asaas [{resp.status_code}]: {descricao_erro}",
                descricao=descricao_erro,
            )
        data = resp.json()
        return {
            "checkout_id": data.get("id"),
            "link": data.get("link") or data.get("url"),
            "status": data.get("status", ""),
        }
    except requests.RequestException as e:
        logger.exception("Erro ao criar checkout no Asaas")
        raise AsaasError(f"Falha ao criar checkout no Asaas: {e}")


def consultar_cobranca(payment_id):
    """Consulta uma cobrança no Asaas."""
    url = f"{_base_url()}/payments/{payment_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code >= 400:
            descricao = _extrair_descricao_erro(resp)
            raise AsaasError(
                f"Falha ao consultar cobrança no Asaas [{resp.status_code}]: {descricao}",
                descricao=descricao,
            )
        return resp.json()
    except requests.RequestException as e:
        logger.exception("Erro ao consultar cobrança no Asaas")
        raise AsaasError(f"Falha ao consultar cobrança no Asaas: {e}")


def consultar_assinatura(subscription_id):
    """Consulta uma assinatura no Asaas."""
    url = f"{_base_url()}/subscriptions/{subscription_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code >= 400:
            descricao = _extrair_descricao_erro(resp)
            raise AsaasError(
                f"Falha ao consultar assinatura no Asaas [{resp.status_code}]: {descricao}",
                descricao=descricao,
            )
        return resp.json()
    except requests.RequestException as e:
        logger.exception("Erro ao consultar assinatura no Asaas")
        raise AsaasError(f"Falha ao consultar assinatura no Asaas: {e}")


def cancelar_assinatura(subscription_id):
    """Remove (cancela) uma assinatura no Asaas."""
    url = f"{_base_url()}/subscriptions/{subscription_id}"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code >= 400:
            descricao = _extrair_descricao_erro(resp)
            raise AsaasError(
                f"Falha ao cancelar assinatura no Asaas [{resp.status_code}]: {descricao}",
                descricao=descricao,
            )
        return True
    except requests.RequestException as e:
        logger.exception("Erro ao cancelar assinatura no Asaas")
        raise AsaasError(f"Falha ao cancelar assinatura no Asaas: {e}")