"""
ASR-2 — Vistas Django para el experimento de integridad de datos.
"""
import time
import logging
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings

from .integrity import (
    PacketIntegrityService,
    MockCloudProvider,
    IntegrityError,
    integrity_stats,
)

logger = logging.getLogger(__name__)

# Inicializar con la clave de settings (en prod viene de Secrets Manager)
HMAC_KEY = getattr(settings, "HMAC_SECRET_KEY", "dev-hmac-key-change-in-production")
integrity_svc = PacketIntegrityService(HMAC_KEY)
cloud_provider = MockCloudProvider(HMAC_KEY)


@method_decorator(csrf_exempt, name="dispatch")
class CloudDataIngestView(View):
    """
    POST /cloud-data/ingest/
    Simula la recepción e ingesta de datos de costo desde el proveedor cloud.
    El cuerpo debe ser un paquete firmado (generado por `MockCloudProvider` o equivalente real).

    Query params:
      ?tamper=1  → el mock provider alterará el paquete (simula MITM)
    """

    def post(self, request):
        import json

        tamper = request.GET.get("tamper") == "1"
        project_id = request.GET.get("project", "demo-project")

        # 1. Obtener paquete del proveedor (firmado)
        signed_packet = cloud_provider.get_monthly_cost(project_id, tamper=tamper)

        # 2. Verificar integridad — aquí se mide el tiempo
        t0 = time.perf_counter()
        try:
            payload = integrity_svc.verify_packet(signed_packet)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            integrity_stats.record_pass()

            return JsonResponse({
                "ok": True,
                "message": "Paquete íntegro — datos ingresados correctamente",
                "project_id": project_id,
                "total_cost_usd": payload["total"],
                "detection_ms": round(elapsed_ms, 3),
                "integrity_check": "PASSED",
            })

        except IntegrityError as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            integrity_stats.record_rejection(elapsed_ms)

            logger.error(
                "ASR-2 RECHAZO | proyecto=%s | tamper=%s | %.2f ms | %s",
                project_id, tamper, elapsed_ms, str(exc)
            )

            sla_ok = elapsed_ms <= 500
            return JsonResponse({
                "ok": False,
                "message": "Paquete rechazado — integridad comprometida",
                "project_id": project_id,
                "error": str(exc),
                "detection_ms": round(elapsed_ms, 3),
                "integrity_check": "FAILED",
                "sla_500ms_compliant": sla_ok,
            }, status=422)


class IntegrityMetricsView(View):
    """
    GET /metrics/integrity/
    Retorna estadísticas del ASR-2 en tiempo real.
    """

    def get(self, request):
        return JsonResponse({
            "asr": "ASR-2 Integridad de datos",
            "target": "100% paquetes verificados, detección < 500ms",
            **integrity_stats.summary(),
        })
