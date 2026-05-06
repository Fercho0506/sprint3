"""
ASR-3: Detección y bloqueo de comunicaciones no cifradas < 1 segundo
=====================================================================
Cuando un hacker intente un ataque Information Disclosure sobre la
comunicación entre microservicios, el sistema debe:
  1. Detectar cualquier intento de transmisión por canal no cifrado.
  2. Bloquear la comunicación.
  3. Todo en menos de 1 segundo desde la detección.

Componentes:
  - TLSEnforcementMiddleware: middleware Django que rechaza HTTP plano.
  - ServiceMeshClient: wrapper para llamadas entre microservicios
    que fuerza TLS y verifica el certificado.
  - NonEncryptedTrafficDetector: detector activo con hilo de monitoreo.
  - Endpoint /security/tls-audit/ para revisión del estado de TLS.
"""

import ssl
import socket
import time
import threading
import logging
import urllib.parse
from datetime import datetime
from typing import Optional

from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Middleware Django — rechazo de HTTP plano en todos los endpoints
# ---------------------------------------------------------------------------

class TLSEnforcementMiddleware:
    """
    Middleware que garantiza que TODAS las peticiones entre microservicios
    lleguen por HTTPS/TLS.

    Instalación en settings.py:
        MIDDLEWARE = [
            'asr3_cifrado.app.tls_enforcement.TLSEnforcementMiddleware',
            ...
        ]

    Comportamiento:
      - En producción (DEBUG=False): rechaza cualquier petición HTTP plana con 403.
      - Registra el intento con nivel ERROR para alerta en CloudWatch.
      - El tiempo de detección + rechazo es < 1 ms (mucho menor al límite de 1 s).
    """

    DETECTION_DEADLINE_MS = 1000  # ASR: < 1 segundo

    def __init__(self, get_response):
        self.get_response = get_response
        self._blocked_attempts: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, request):
        t0 = time.perf_counter()

        is_secure = self._is_secure_request(request)

        if not is_secure:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return self._block_request(request, elapsed_ms)

        response = self.get_response(request)
        return response

    def _is_secure_request(self, request) -> bool:
        """
        Determina si la petición llegó por un canal cifrado.
        Considera múltiples escenarios de despliegue en AWS:
          - Detrás de ALB: X-Forwarded-Proto: https
          - Directo: request.is_secure()
          - Kong gateway: X-Forwarded-Proto o cabecera personalizada
        """
        # Detrás de ALB / Kong
        forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", "")
        if forwarded_proto:
            return forwarded_proto.lower() == "https"

        # Conexión directa
        return request.is_secure()

    def _block_request(self, request, elapsed_ms: float) -> JsonResponse:
        path = request.path
        client_ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "unknown"))
        svc_source = request.META.get("HTTP_X_SERVICE_NAME", "unknown-service")

        event = {
            "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            "event": "NON_ENCRYPTED_CHANNEL_DETECTED",
            "source_ip": client_ip,
            "source_service": svc_source,
            "path": path,
            "method": request.method,
            "detection_ms": round(elapsed_ms, 3),
            "sla_1s_compliant": elapsed_ms <= self.DETECTION_DEADLINE_MS,
        }

        with self._lock:
            self._blocked_attempts.append(event)

        logger.error(
            "ASR-3 BLOQUEO — Canal no cifrado detectado | "
            "IP=%s | Servicio=%s | Path=%s | %.2f ms",
            client_ip, svc_source, path, elapsed_ms,
        )

        return JsonResponse(
            {
                "error": "Comunicación no cifrada bloqueada",
                "detail": "Todos los microservicios deben comunicarse exclusivamente por HTTPS/TLS.",
                "asr": "ASR-3",
                "detection_ms": round(elapsed_ms, 3),
            },
            status=403,
        )

    def get_blocked_attempts(self) -> list:
        with self._lock:
            return list(self._blocked_attempts)


# ---------------------------------------------------------------------------
# Cliente de malla de servicios con TLS obligatorio
# ---------------------------------------------------------------------------

class ServiceMeshClient:
    """
    Wrapper para llamadas HTTP entre microservicios.
    SIEMPRE usa HTTPS y verifica el certificado del servidor destino.

    Uso:
        client = ServiceMeshClient()
        response = client.get("http://finops-server/internal/reports/")
        # ↑ Aunque se pase http://, el cliente lo eleva a https://
    """

    def __init__(
        self,
        verify_cert: bool = True,
        ca_bundle: Optional[str] = None,
        timeout: float = 5.0,
    ):
        self.verify_cert = verify_cert
        self.ca_bundle = ca_bundle or True  # True = usar bundle del sistema
        self.timeout = timeout

    def get(self, url: str, **kwargs):
        import requests
        url = self._enforce_https(url)
        return requests.get(url, verify=self.ca_bundle, timeout=self.timeout, **kwargs)

    def post(self, url: str, **kwargs):
        import requests
        url = self._enforce_https(url)
        return requests.post(url, verify=self.ca_bundle, timeout=self.timeout, **kwargs)

    def _enforce_https(self, url: str) -> str:
        """Si la URL usa http://, la reemplaza por https:// y lo registra."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "http":
            logger.warning(
                "ASR-3 ADVERTENCIA: URL sin TLS detectada y corregida: %s → %s",
                url, url.replace("http://", "https://", 1),
            )
            url = url.replace("http://", "https://", 1)
        return url

    @staticmethod
    def verify_endpoint_tls(host: str, port: int = 443, timeout: float = 3.0) -> dict:
        """
        Verifica que el endpoint de un microservicio realmente soporte TLS.
        Retorna información del certificado.
        """
        t0 = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    return {
                        "host": host,
                        "port": port,
                        "tls_ok": True,
                        "tls_version": ssock.version(),
                        "cert_subject": dict(x[0] for x in cert.get("subject", [])),
                        "cert_expires": cert.get("notAfter"),
                        "check_ms": round(elapsed_ms, 2),
                    }
        except ssl.SSLError as exc:
            return {"host": host, "port": port, "tls_ok": False, "error": str(exc)}
        except Exception as exc:
            return {"host": host, "port": port, "tls_ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Detector activo de tráfico no cifrado (escucha en puerto 8080 HTTP)
# ---------------------------------------------------------------------------

class NonEncryptedTrafficDetector:
    """
    Servidor TCP ligero que escucha en el puerto HTTP (8080) de los microservicios.
    Si recibe cualquier conexión, la rechaza inmediatamente y la registra.

    En producción este rol lo cumple Kong configurado con:
        plugins:
          - name: request-termination
            config:
              status_code: 403
              message: "TLS required"

    Este detector sirve para el experimento local.
    """

    def __init__(self, listen_port: int = 8080, deadline_ms: float = 1000):
        self.listen_port = listen_port
        self.deadline_ms = deadline_ms
        self._detections: list[dict] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Inicia el detector en un hilo de fondo."""
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        logger.info("ASR-3 Detector iniciado en puerto %d", self.listen_port)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _listen(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.listen_port))
            server.listen(5)
            server.settimeout(1.0)

            while self._running:
                try:
                    conn, addr = server.accept()
                    t_detected = time.perf_counter()
                    threading.Thread(
                        target=self._handle_non_tls_connection,
                        args=(conn, addr, t_detected),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
        except Exception as exc:
            logger.error("ASR-3 Detector error: %s", exc)

    def _handle_non_tls_connection(self, conn, addr, t_detected: float):
        """Rechaza la conexión no cifrada y mide el tiempo de respuesta."""
        try:
            # Leer los primeros bytes para confirmar que no es TLS
            conn.settimeout(0.5)
            try:
                first_bytes = conn.recv(16)
            except socket.timeout:
                first_bytes = b""

            elapsed_ms = (time.perf_counter() - t_detected) * 1000

            # TLS ClientHello comienza con 0x16 0x03
            is_tls = len(first_bytes) >= 2 and first_bytes[0] == 0x16 and first_bytes[1] == 0x03

            if not is_tls:
                # Enviar respuesta HTTP 403
                response = (
                    b"HTTP/1.1 403 Forbidden\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Connection: close\r\n\r\n"
                    b'{"error":"Non-encrypted channel blocked","asr":"ASR-3"}\r\n'
                )
                conn.sendall(response)

                event = {
                    "timestamp_utc": datetime.utcnow().isoformat() + "Z",
                    "source_ip": addr[0],
                    "source_port": addr[1],
                    "detection_ms": round(elapsed_ms, 3),
                    "sla_compliant": elapsed_ms <= self.deadline_ms,
                    "first_bytes_hex": first_bytes.hex() if first_bytes else "",
                }

                with self._lock:
                    self._detections.append(event)

                logger.error(
                    "ASR-3 CANAL NO CIFRADO DETECTADO Y BLOQUEADO | IP=%s:%d | %.2f ms | SLA: %s",
                    addr[0], addr[1], elapsed_ms,
                    "✅" if event["sla_compliant"] else "❌",
                )
        finally:
            conn.close()

    def get_stats(self) -> dict:
        with self._lock:
            detections = list(self._detections)

        if not detections:
            return {
                "total_blocked": 0,
                "avg_detection_ms": 0,
                "max_detection_ms": 0,
                "all_within_1s": True,
                "asr3_compliant": True,
            }

        times = [d["detection_ms"] for d in detections]
        return {
            "total_blocked": len(detections),
            "avg_detection_ms": round(sum(times) / len(times), 3),
            "max_detection_ms": round(max(times), 3),
            "all_within_1s": all(t <= 1000 for t in times),
            "asr3_compliant": all(t <= 1000 for t in times),
            "recent_events": detections[-5:],  # últimos 5 eventos
        }
