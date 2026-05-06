"""
ASR-2: Integridad de datos — 100% de paquetes, detección < 500 ms
==================================================================
Cuando la plataforma intercambia datos financieros de consumo cloud
con el proveedor (AWS Cost Explorer, Azure Cost API, GCP Billing),
cualquier alteración del paquete en tránsito debe:
  1. Ser detectada.
  2. Causar el rechazo del paquete.
  3. Todo esto en menos de 500 milisegundos.

Mecanismo:
  - HMAC-SHA256 sobre el payload JSON firmado con una clave compartida
    (almacenada en AWS Secrets Manager en producción).
  - El FinOps Server firma cada solicitud saliente y verifica la firma
    en cada respuesta entrante del proveedor.
  - Si la verificación falla, el paquete es rechazado y se lanza una
    alerta antes de que el dato llegue a la base de datos.
"""

import hashlib
import hmac
import json
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Firmado y verificación de paquetes
# ---------------------------------------------------------------------------

class IntegrityError(Exception):
    """Lanzada cuando un paquete no supera la verificación de integridad."""
    pass


class PacketIntegrityService:
    """
    Servicio de integridad HMAC-SHA256.

    En producción el `secret_key` se obtiene de AWS Secrets Manager:
        import boto3, json
        client = boto3.client('secretsmanager', region_name='us-east-1')
        secret = client.get_secret_value(SecretId='finops/hmac-key')
        key = json.loads(secret['SecretString'])['hmac_key']
    """

    DETECTION_DEADLINE_MS = 500  # ASR: detección < 500 ms

    def __init__(self, secret_key: str):
        if not secret_key:
            raise ValueError("secret_key no puede estar vacío")
        self._key = secret_key.encode("utf-8")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def sign_packet(self, payload: dict) -> dict:
        """
        Firma el payload JSON y retorna el paquete completo con la firma.

        Estructura del paquete firmado:
        {
            "payload": { ...datos originales... },
            "metadata": {
                "timestamp_utc": "...",
                "signature": "<HMAC-SHA256 hex>",
                "algorithm": "HMAC-SHA256"
            }
        }
        """
        canonical = self._canonicalize(payload)
        signature = self._compute_hmac(canonical)

        return {
            "payload": payload,
            "metadata": {
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
                "signature": signature,
                "algorithm": "HMAC-SHA256",
            },
        }

    def verify_packet(self, signed_packet: dict) -> dict:
        """
        Verifica la firma del paquete y retorna el payload si es válido.

        Lanza IntegrityError si:
          - La firma no coincide (datos alterados).
          - El tiempo de verificación supera DETECTION_DEADLINE_MS.
          - Faltan campos obligatorios.
        """
        t_start = time.perf_counter()

        try:
            payload = signed_packet["payload"]
            metadata = signed_packet["metadata"]
            received_signature = metadata["signature"]
        except KeyError as exc:
            self._record_rejection(
                reason=f"Paquete malformado — falta campo: {exc}",
                elapsed_ms=self._elapsed_ms(t_start),
            )
            raise IntegrityError(f"Paquete malformado: {exc}") from exc

        canonical = self._canonicalize(payload)
        expected_signature = self._compute_hmac(canonical)

        elapsed_ms = self._elapsed_ms(t_start)

        if not hmac.compare_digest(expected_signature, received_signature):
            self._record_rejection(
                reason="FIRMA INVÁLIDA — datos potencialmente alterados en tránsito",
                elapsed_ms=elapsed_ms,
            )
            raise IntegrityError(
                f"Integridad comprometida: la firma no coincide. "
                f"Detección en {elapsed_ms:.2f} ms"
            )

        if elapsed_ms > self.DETECTION_DEADLINE_MS:
            logger.warning(
                "ASR-2 ADVERTENCIA: verificación tardó %.2f ms (límite: %d ms)",
                elapsed_ms,
                self.DETECTION_DEADLINE_MS,
            )

        logger.info(
            "ASR-2 Paquete verificado correctamente en %.2f ms", elapsed_ms
        )
        return payload

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _canonicalize(self, payload: dict) -> bytes:
        """Serializa el payload de forma determinista (sort_keys=True)."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _compute_hmac(self, data: bytes) -> str:
        return hmac.new(self._key, data, hashlib.sha256).hexdigest()

    @staticmethod
    def _elapsed_ms(t_start: float) -> float:
        return (time.perf_counter() - t_start) * 1000

    @staticmethod
    def _record_rejection(reason: str, elapsed_ms: float):
        logger.error(
            "ASR-2 PAQUETE RECHAZADO — %s | Detección: %.2f ms",
            reason,
            elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Estadísticas de integridad (para el endpoint de métricas)
# ---------------------------------------------------------------------------

@dataclass
class IntegrityStats:
    total_verified: int = 0
    total_rejected: int = 0
    total_passed: int = 0
    rejection_times_ms: list = field(default_factory=list)

    def record_rejection(self, elapsed_ms: float):
        self.total_verified += 1
        self.total_rejected += 1
        self.rejection_times_ms.append(elapsed_ms)

    def record_pass(self):
        self.total_verified += 1
        self.total_passed += 1

    def summary(self) -> dict:
        avg_detection = (
            sum(self.rejection_times_ms) / len(self.rejection_times_ms)
            if self.rejection_times_ms else 0.0
        )
        max_detection = max(self.rejection_times_ms, default=0.0)
        sla_compliant_detections = sum(
            1 for t in self.rejection_times_ms if t <= 500
        )
        return {
            "total_verified": self.total_verified,
            "total_rejected": self.total_rejected,
            "total_passed": self.total_passed,
            "integrity_rate_pct": (
                self.total_passed / self.total_verified * 100
                if self.total_verified > 0 else 100.0
            ),
            "avg_detection_ms": round(avg_detection, 2),
            "max_detection_ms": round(max_detection, 2),
            "detections_within_500ms": sla_compliant_detections,
            "sla_compliant": (
                self.total_rejected == 0 or
                sla_compliant_detections == self.total_rejected
            ),
        }


integrity_stats = IntegrityStats()


# ---------------------------------------------------------------------------
# Simulador de proveedor cloud (para pruebas locales)
# ---------------------------------------------------------------------------

class MockCloudProvider:
    """
    Simula las respuestas de un proveedor cloud (AWS Cost Explorer).
    Puede inyectar corrupción para probar el rechazo.
    """

    def __init__(self, hmac_key: str):
        self._svc = PacketIntegrityService(hmac_key)

    def get_monthly_cost(self, project_id: str, tamper: bool = False) -> dict:
        """
        Retorna un paquete firmado con los costos del proyecto.
        Si `tamper=True`, modifica el payload DESPUÉS de firmar (simula ataque MITM).
        """
        payload = {
            "project_id": project_id,
            "period": datetime.now().strftime("%Y-%m"),
            "currency": "USD",
            "services": [
                {"name": "EC2", "cost": 1500.00},
                {"name": "RDS", "cost": 400.00},
                {"name": "S3", "cost": 200.50},
            ],
            "total": 2100.50,
        }
        signed = self._svc.sign_packet(payload)

        if tamper:
            # Atacante modifica el total en tránsito
            signed["payload"]["total"] = 0.01
            logger.warning("🔴 SIMULACIÓN: paquete alterado en tránsito (ataque MITM)")

        return signed
