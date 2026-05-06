"""
ASR-3 — Tests de detección de canales no cifrados
==================================================
Ejecutar con:
    pytest asr3_cifrado/tests/test_tls_enforcement.py -v
"""
import socket
import time
import threading
import pytest
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from asr3_cifrado.app.tls_enforcement import (
    ServiceMeshClient,
    NonEncryptedTrafficDetector,
    TLSEnforcementMiddleware,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def detector():
    """Inicia el detector en un puerto libre para los tests."""
    det = NonEncryptedTrafficDetector(listen_port=19080, deadline_ms=1000)
    det.start()
    time.sleep(0.2)  # esperar que el socket esté listo
    yield det
    det.stop()


# ---------------------------------------------------------------------------
# Tests del ServiceMeshClient
# ---------------------------------------------------------------------------

class TestServiceMeshClient:
    def test_http_url_is_upgraded_to_https(self):
        client = ServiceMeshClient()
        url = client._enforce_https("http://finops-server.internal/api/reports/")
        assert url.startswith("https://"), "URL debe ser elevada a HTTPS"

    def test_https_url_unchanged(self):
        client = ServiceMeshClient()
        url = client._enforce_https("https://finops-server.internal/api/reports/")
        assert url == "https://finops-server.internal/api/reports/"

    def test_http_to_https_replacement_is_prefix_only(self):
        """No debe reemplazar 'http' dentro del path."""
        client = ServiceMeshClient()
        url = client._enforce_https("http://server/path/http-resource")
        assert url == "https://server/path/http-resource"


# ---------------------------------------------------------------------------
# Tests del TLSEnforcementMiddleware
# ---------------------------------------------------------------------------

class FakeRequest:
    def __init__(self, secure: bool, forwarded_proto: str = ""):
        self._secure = secure
        self.META = {}
        self.path = "/test/"
        self.method = "GET"
        if forwarded_proto:
            self.META["HTTP_X_FORWARDED_PROTO"] = forwarded_proto

    def is_secure(self):
        return self._secure


class TestTLSMiddleware:
    def _make_middleware(self):
        responses = []

        def get_response(request):
            responses.append("passed")
            return "ok"

        mw = TLSEnforcementMiddleware(get_response)
        return mw, responses

    def test_https_request_passes(self):
        mw, responses = self._make_middleware()
        request = FakeRequest(secure=True)
        result = mw(request)
        assert result == "ok"
        assert responses == ["passed"]

    def test_http_request_blocked(self):
        mw, responses = self._make_middleware()
        request = FakeRequest(secure=False)
        result = mw(request)
        assert result.status_code == 403
        assert responses == []

    def test_alb_forwarded_proto_https_passes(self):
        mw, responses = self._make_middleware()
        request = FakeRequest(secure=False, forwarded_proto="https")
        result = mw(request)
        assert result == "ok"

    def test_alb_forwarded_proto_http_blocked(self):
        mw, responses = self._make_middleware()
        request = FakeRequest(secure=False, forwarded_proto="http")
        result = mw(request)
        assert result.status_code == 403

    def test_blocked_request_recorded_in_audit_log(self):
        mw, _ = self._make_middleware()
        request = FakeRequest(secure=False)
        request.META["HTTP_X_SERVICE_NAME"] = "cron-worker"
        mw(request)
        attempts = mw.get_blocked_attempts()
        assert len(attempts) >= 1
        assert attempts[-1]["source_service"] == "cron-worker"

    def test_detection_time_under_1_second(self):
        """ASR-3: la detección + rechazo deben ocurrir en < 1000 ms."""
        mw, _ = self._make_middleware()
        request = FakeRequest(secure=False)
        t0 = time.perf_counter()
        mw(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 1000, (
            f"Detección tardó {elapsed_ms:.2f} ms — INCUMPLE ASR-3"
        )


# ---------------------------------------------------------------------------
# Tests del NonEncryptedTrafficDetector
# ---------------------------------------------------------------------------

class TestNonEncryptedDetector:
    def test_detector_starts_and_listens(self, detector):
        """Verificar que el socket está activo."""
        sock = socket.socket()
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 19080))
        sock.close()
        assert result == 0, "El detector no está escuchando"

    def test_plain_http_request_is_blocked(self, detector):
        """Enviar HTTP plano debe recibir 403."""
        sock = socket.socket()
        sock.settimeout(3)
        sock.connect(("127.0.0.1", 19080))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response = sock.recv(4096)
        sock.close()
        assert b"403" in response, f"Respuesta inesperada: {response[:100]}"

    def test_detection_recorded_in_stats(self, detector):
        """Después de un intento, las estadísticas deben reflejarlo."""
        initial = detector.get_stats()["total_blocked"]

        sock = socket.socket()
        sock.settimeout(2)
        sock.connect(("127.0.0.1", 19080))
        sock.sendall(b"GET /secret HTTP/1.1\r\nHost: localhost\r\n\r\n")
        sock.recv(1024)
        sock.close()

        time.sleep(0.1)  # esperar que el hilo procese
        stats = detector.get_stats()
        assert stats["total_blocked"] > initial

    def test_detection_within_1_second(self, detector):
        """ASR-3 crítico: la detección y bloqueo debe ser < 1000 ms."""
        t0 = time.perf_counter()
        sock = socket.socket()
        sock.settimeout(3)
        sock.connect(("127.0.0.1", 19080))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        sock.recv(4096)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        sock.close()
        assert elapsed_ms < 1000, (
            f"Respuesta de bloqueo tardó {elapsed_ms:.2f} ms — INCUMPLE ASR-3"
        )

    def test_stats_show_sla_compliant(self, detector):
        """Las estadísticas deben mostrar cumplimiento del ASR."""
        stats = detector.get_stats()
        if stats["total_blocked"] > 0:
            assert stats["asr3_compliant"] is True, (
                "ASR-3 incumplido: alguna detección tardó > 1000 ms"
            )
