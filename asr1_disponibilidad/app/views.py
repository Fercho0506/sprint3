"""
ASR-1: Disponibilidad >= 99.9% mensual
======================================
Componentes demostrados:
  - Health check endpoint con verificación de dependencias (DB, Redis, RDS)
  - Circuit Breaker para el servicio de reportes
  - Middleware que mide uptime y registra downtime
  - Endpoint de métricas de disponibilidad
"""

import time
import hmac
import hashlib
import threading
from datetime import datetime, timedelta
from enum import Enum

import redis
from django.db import connections
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "CLOSED"       # Normal — requests pass through
    OPEN = "OPEN"           # Falla — requests rechazados de inmediato
    HALF_OPEN = "HALF_OPEN" # Probando si el servicio se recuperó


class CircuitBreaker:
    """
    Implementación de Circuit Breaker para el servicio de reportes FinOps.
    Parámetros configurables desde settings/env.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # ¿Ya pasó el timeout de recuperación?
                if (
                    self._last_failure_time
                    and time.time() - self._last_failure_time >= self.recovery_timeout
                ):
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
            return self._state

    def call(self, func, *args, **kwargs):
        """Ejecuta `func` a través del circuit breaker."""
        state = self.state

        if state == CircuitState.OPEN:
            raise CircuitOpenError("Circuit OPEN — servicio de reportes no disponible")

        if state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError("Circuit HALF_OPEN — límite de pruebas alcanzado")
                self._half_open_calls += 1

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise exc

    def _on_success(self):
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def _on_failure(self):
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN


class CircuitOpenError(Exception):
    pass


# Instancia global del circuit breaker para el servicio de reportes
report_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    half_open_max_calls=2,
)


# ---------------------------------------------------------------------------
# Uptime Tracker (en memoria — en producción usar Redis o CloudWatch)
# ---------------------------------------------------------------------------

class UptimeTracker:
    """
    Rastrea el tiempo de operación y downtime no planificado.
    Objetivo: <= 8h 46s de downtime por mes (99.9% SLA).
    """

    MONTHLY_ALLOWED_DOWNTIME_SECONDS = 8 * 3600 + 46  # 26_446 segundos

    def __init__(self):
        self._start_time = time.time()
        self._downtime_events: list[dict] = []
        self._current_downtime_start: float | None = None
        self._lock = threading.Lock()

    def mark_down(self, reason: str = ""):
        with self._lock:
            if self._current_downtime_start is None:
                self._current_downtime_start = time.time()
                self._downtime_events.append(
                    {"start": self._current_downtime_start, "end": None, "reason": reason}
                )

    def mark_up(self):
        with self._lock:
            if self._current_downtime_start is not None:
                now = time.time()
                self._downtime_events[-1]["end"] = now
                self._current_downtime_start = None

    def monthly_downtime_seconds(self) -> float:
        now = time.time()
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        total = 0.0
        for event in self._downtime_events:
            start = max(event["start"], month_start)
            end = event["end"] or now
            if end > month_start:
                total += end - start
        return total

    def availability_percentage(self) -> float:
        now = time.time()
        total_seconds = now - self._start_time
        if total_seconds == 0:
            return 100.0
        downtime = self.monthly_downtime_seconds()
        return max(0.0, (1 - downtime / total_seconds) * 100)

    def sla_status(self) -> dict:
        downtime = self.monthly_downtime_seconds()
        return {
            "availability_pct": round(self.availability_percentage(), 4),
            "monthly_downtime_seconds": round(downtime, 2),
            "monthly_downtime_limit_seconds": self.MONTHLY_ALLOWED_DOWNTIME_SECONDS,
            "sla_compliant": downtime <= self.MONTHLY_ALLOWED_DOWNTIME_SECONDS,
            "downtime_events_this_month": len(
                [e for e in self._downtime_events if e["start"] >= (
                    datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
                )]
            ),
        }


uptime_tracker = UptimeTracker()


# ---------------------------------------------------------------------------
# Health check helpers
# ---------------------------------------------------------------------------

def check_database() -> tuple[bool, str]:
    """Verifica conexión a PostgreSQL (Amazon RDS)."""
    try:
        conn = connections["default"]
        conn.cursor().execute("SELECT 1")
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def check_redis() -> tuple[bool, str]:
    """Verifica conexión a ElastiCache Redis."""
    try:
        from django.conf import settings
        r = redis.Redis.from_url(getattr(settings, "REDIS_URL", "redis://localhost:6379/0"))
        r.ping()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def check_finops_service() -> tuple[bool, str]:
    """Simula verificación del servicio FinOps interno."""
    import requests as req
    from django.conf import settings
    url = getattr(settings, "FINOPS_INTERNAL_URL", "http://localhost:8080/health/")
    try:
        resp = req.get(url, timeout=2)
        if resp.status_code == 200:
            return True, "ok"
        return False, f"status {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class HealthCheckView(View):
    """
    GET /health/
    Retorna el estado de todos los subsistemas.
    Usado por el ALB de AWS para determinar si la instancia está sana.
    """

    def get(self, request):
        db_ok, db_msg = check_database()
        redis_ok, redis_msg = check_redis()

        all_ok = db_ok and redis_ok

        if all_ok:
            uptime_tracker.mark_up()
            status_code = 200
        else:
            uptime_tracker.mark_down(
                reason=f"db={db_msg}, redis={redis_msg}"
            )
            status_code = 503

        payload = {
            "status": "healthy" if all_ok else "unhealthy",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": {
                "database": {"ok": db_ok, "detail": db_msg},
                "redis": {"ok": redis_ok, "detail": redis_msg},
            },
            "circuit_breaker": {
                "reports_service": report_circuit_breaker.state.value,
            },
        }
        return JsonResponse(payload, status=status_code)


class AvailabilityMetricsView(View):
    """
    GET /metrics/availability/
    Muestra métricas de SLA en tiempo real.
    """

    def get(self, request):
        sla = uptime_tracker.sla_status()
        return JsonResponse(
            {
                "sla_target_pct": 99.9,
                "sla_target_max_downtime_seconds": UptimeTracker.MONTHLY_ALLOWED_DOWNTIME_SECONDS,
                **sla,
                "circuit_breaker_state": report_circuit_breaker.state.value,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class MonthlyReportView(View):
    """
    GET /reports/monthly/?project=<id>
    Retorna el reporte mensual de gasto cloud con circuit breaker.
    """

    def get(self, request):
        project_id = request.GET.get("project", "demo")

        def fetch_report():
            # En producción, aquí iría la lógica real de consulta a BD/FinOps
            return {
                "project": project_id,
                "period": datetime.now().strftime("%Y-%m"),
                "total_cost_usd": 1234.56,
                "services": [
                    {"name": "EC2", "cost": 500.00},
                    {"name": "RDS", "cost": 300.00},
                    {"name": "ElastiCache", "cost": 100.00},
                    {"name": "S3", "cost": 334.56},
                ],
            }

        try:
            report = report_circuit_breaker.call(fetch_report)
            return JsonResponse({"ok": True, "report": report})
        except CircuitOpenError as exc:
            return JsonResponse(
                {"ok": False, "error": str(exc), "retry_after_seconds": 30},
                status=503,
            )
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
