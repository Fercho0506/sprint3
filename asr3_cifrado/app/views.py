"""
ASR-3 — Vistas para el experimento de canales no cifrados.
"""
import time
from django.http import JsonResponse
from django.views import View

from .tls_enforcement import ServiceMeshClient, NonEncryptedTrafficDetector

# Detector global (arrancar con el servidor)
detector = NonEncryptedTrafficDetector(listen_port=8080, deadline_ms=1000)

# Iniciar al importar el módulo (en producción, usar AppConfig.ready())
try:
    detector.start()
except OSError:
    pass  # Puerto ya en uso (tests múltiples)


class TLSAuditView(View):
    """
    GET /security/tls-audit/
    Verifica el estado TLS de todos los microservicios internos.
    """

    # Lista de microservicios a auditar (configurar con los DNS reales en AWS)
    SERVICES = [
        {"name": "API Server",    "host": "api-server.internal",    "port": 443},
        {"name": "FinOps Server", "host": "finops-server.internal", "port": 8080},
        {"name": "CRON Worker",   "host": "cron-worker.internal",   "port": 8080},
        {"name": "Kong Gateway",  "host": "kong.internal",          "port": 443},
    ]

    def get(self, request):
        client = ServiceMeshClient()
        results = []

        for svc in self.SERVICES:
            result = ServiceMeshClient.verify_endpoint_tls(
                svc["host"], svc["port"]
            )
            result["service_name"] = svc["name"]
            results.append(result)

        all_ok = all(r["tls_ok"] for r in results)

        return JsonResponse({
            "asr": "ASR-3 — Canales cifrados",
            "timestamp_utc": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "all_services_tls_ok": all_ok,
            "services": results,
        })


class NonEncryptedStatsView(View):
    """
    GET /security/non-encrypted-stats/
    Estadísticas del detector de tráfico no cifrado.
    """

    def get(self, request):
        stats = detector.get_stats()
        return JsonResponse({
            "asr": "ASR-3 — Detección < 1 segundo",
            "target_detection_ms": 1000,
            **stats,
        })


class ServiceHealthView(View):
    """
    GET /health/
    Health check básico para el experimento ASR-3.
    """
    def get(self, request):
        return JsonResponse({"status": "healthy", "tls": True})
