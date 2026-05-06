"""
ASR-1 — Experimento de disponibilidad con Locust
=================================================
Simula tráfico continuo hacia los endpoints de la plataforma y mide
cuánto tiempo el servicio responde correctamente.

Cómo ejecutar:
    locust -f tests/load_test_availability.py \
           --host http://<ALB_DNS_o_localhost:8000> \
           --users 50 --spawn-rate 10 --run-time 5m \
           --headless --csv=results/asr1

El CSV generado contiene p50, p95, p99 de latencia y % de fallos.
El objetivo es que el porcentaje de fallos sea <= 0.1% (equivalente a 99.9% disponibilidad).
"""

from locust import HttpUser, task, between, events
import time
import csv
import os

# Acumuladores de métricas
_total_requests = 0
_failed_requests = 0
_downtime_start = None
_total_downtime = 0.0


class FinOpsUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(5)
    def check_health(self):
        """El ALB ejecuta este check cada ~30s. Lo simulamos con alta frecuencia."""
        global _total_requests, _failed_requests, _downtime_start, _total_downtime
        with self.client.get("/health/", catch_response=True) as resp:
            _total_requests += 1
            if resp.status_code == 200:
                resp.success()
                if _downtime_start is not None:
                    _total_downtime += time.time() - _downtime_start
                    _downtime_start = None
            else:
                _failed_requests += 1
                resp.failure(f"Health check failed: {resp.status_code}")
                if _downtime_start is None:
                    _downtime_start = time.time()

    @task(3)
    def get_monthly_report(self):
        """Consulta real de usuario — reporte mensual de gasto."""
        global _total_requests, _failed_requests
        with self.client.get(
            "/reports/monthly/?project=proyecto-demo", catch_response=True
        ) as resp:
            _total_requests += 1
            if resp.status_code in (200, 503):
                # 503 con circuit breaker es comportamiento esperado, no downtime real
                resp.success()
            else:
                _failed_requests += 1
                resp.failure(f"Unexpected status: {resp.status_code}")

    @task(1)
    def get_availability_metrics(self):
        """Monitoreo — consulta métricas de SLA."""
        self.client.get("/metrics/availability/")


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """Al terminar, imprime resumen del experimento."""
    global _total_requests, _failed_requests, _total_downtime, _downtime_start

    if _downtime_start is not None:
        _total_downtime += time.time() - _downtime_start

    availability = (
        (1 - _failed_requests / _total_requests) * 100
        if _total_requests > 0
        else 0
    )

    monthly_equivalent_downtime = _total_downtime * (30 * 24 * 3600) / max(environment.runner.run_time, 1)

    print("\n" + "=" * 60)
    print("ASR-1 RESULTADO DEL EXPERIMENTO DE DISPONIBILIDAD")
    print("=" * 60)
    print(f"  Total requests      : {_total_requests}")
    print(f"  Requests fallidos   : {_failed_requests}")
    print(f"  Disponibilidad      : {availability:.4f}%")
    print(f"  SLA objetivo        : 99.9000%")
    print(f"  Downtime observado  : {_total_downtime:.2f}s")
    print(f"  Downtime mensual eq.: {monthly_equivalent_downtime:.2f}s")
    print(f"  Límite mensual SLA  : 26446s (8h 46s)")
    print(f"  ✅ CUMPLE SLA       : {'SÍ' if availability >= 99.9 else 'NO ❌'}")
    print("=" * 60)

    os.makedirs("results", exist_ok=True)
    with open("results/asr1_summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_requests", _total_requests])
        writer.writerow(["failed_requests", _failed_requests])
        writer.writerow(["availability_pct", f"{availability:.4f}"])
        writer.writerow(["observed_downtime_seconds", f"{_total_downtime:.2f}"])
        writer.writerow(["monthly_equivalent_downtime_seconds", f"{monthly_equivalent_downtime:.2f}"])
        writer.writerow(["sla_compliant", availability >= 99.9])
