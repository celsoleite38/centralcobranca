from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Assinatura


class Command(BaseCommand):
    help = 'Expira trials vencidos e marca atrasadas as assinaturas cujo vencimento passou.'

    def handle(self, *args, **options):
        hoje = timezone.now().date()

        trials_vencidos = Assinatura.objects.filter(status='trial', trial_fim__lt=hoje)
        total_trial = trials_vencidos.update(status='inativa')

        ativas_vencidas = Assinatura.objects.filter(status='ativa', proximo_vencimento__lt=hoje)
        total_atrasada = ativas_vencidas.update(status='atrasada')

        self.stdout.write(self.style.SUCCESS(
            f'{total_trial} trial(s) expirado(s) -> inativa | {total_atrasada} assinatura(s) marcada(s) atrasada(s)'
        ))