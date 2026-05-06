"""
ASR-2 — Tests de integridad de datos
=====================================
Ejecutar con:
    pytest asr2_integridad/tests/test_integrity.py -v
"""
import time
import json
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from asr2_integridad.app.integrity import (
    PacketIntegrityService,
    MockCloudProvider,
    IntegrityError,
)

HMAC_KEY = "test-secret-key-for-unit-tests"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    return PacketIntegrityService(HMAC_KEY)

@pytest.fixture
def provider():
    return MockCloudProvider(HMAC_KEY)

@pytest.fixture
def sample_payload():
    return {
        "project_id": "proj-001",
        "period": "2025-04",
        "currency": "USD",
        "services": [{"name": "EC2", "cost": 100.0}],
        "total": 100.0,
    }

# ---------------------------------------------------------------------------
# Tests de firma
# ---------------------------------------------------------------------------

class TestSigning:
    def test_sign_returns_signature(self, svc, sample_payload):
        packet = svc.sign_packet(sample_payload)
        assert "metadata" in packet
        assert "signature" in packet["metadata"]
        assert packet["metadata"]["algorithm"] == "HMAC-SHA256"

    def test_signature_is_deterministic_for_same_payload(self, svc, sample_payload):
        p1 = svc.sign_packet(sample_payload)
        p2 = svc.sign_packet(sample_payload)
        assert p1["metadata"]["signature"] == p2["metadata"]["signature"]

    def test_different_payloads_different_signatures(self, svc, sample_payload):
        p1 = svc.sign_packet(sample_payload)
        modified = dict(sample_payload, total=999.99)
        p2 = svc.sign_packet(modified)
        assert p1["metadata"]["signature"] != p2["metadata"]["signature"]

# ---------------------------------------------------------------------------
# Tests de verificación
# ---------------------------------------------------------------------------

class TestVerification:
    def test_valid_packet_passes(self, svc, sample_payload):
        packet = svc.sign_packet(sample_payload)
        result = svc.verify_packet(packet)
        assert result == sample_payload

    def test_tampered_payload_raises(self, svc, sample_payload):
        packet = svc.sign_packet(sample_payload)
        packet["payload"]["total"] = 0.01  # alteración MITM
        with pytest.raises(IntegrityError):
            svc.verify_packet(packet)

    def test_tampered_signature_raises(self, svc, sample_payload):
        packet = svc.sign_packet(sample_payload)
        packet["metadata"]["signature"] = "aabbcc" * 10  # firma falsa
        with pytest.raises(IntegrityError):
            svc.verify_packet(packet)

    def test_missing_signature_raises(self, svc, sample_payload):
        packet = svc.sign_packet(sample_payload)
        del packet["metadata"]["signature"]
        with pytest.raises(IntegrityError):
            svc.verify_packet(packet)

    def test_empty_payload_still_works(self, svc):
        payload = {}
        packet = svc.sign_packet(payload)
        result = svc.verify_packet(packet)
        assert result == {}

# ---------------------------------------------------------------------------
# Tests de rendimiento — ASR objetivo < 500 ms
# ---------------------------------------------------------------------------

class TestPerformance:
    """
    ASR-2 exige que la detección de alteración ocurra en < 500 ms.
    Todos los tests de este bloque deben pasar incluso en hardware lento.
    """

    DEADLINE_MS = 500

    def test_verification_time_valid_packet(self, svc, sample_payload):
        """Un paquete íntegro debe verificarse muy por debajo del límite."""
        packet = svc.sign_packet(sample_payload)
        t0 = time.perf_counter()
        svc.verify_packet(packet)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < self.DEADLINE_MS, (
            f"Verificación tardó {elapsed:.2f} ms (límite: {self.DEADLINE_MS} ms)"
        )

    def test_detection_time_tampered_packet(self, svc, sample_payload):
        """La detección de corrupción debe ocurrir en < 500 ms (ASR crítico)."""
        packet = svc.sign_packet(sample_payload)
        packet["payload"]["total"] = 0.01  # simula alteración MITM

        t0 = time.perf_counter()
        with pytest.raises(IntegrityError):
            svc.verify_packet(packet)
        elapsed = (time.perf_counter() - t0) * 1000

        print(f"\n  ⏱  Detección de paquete alterado: {elapsed:.3f} ms (límite: 500 ms)")
        assert elapsed < self.DEADLINE_MS, (
            f"Detección tardó {elapsed:.2f} ms — INCUMPLE ASR-2 (límite: 500 ms)"
        )

    def test_bulk_detection_p99_under_500ms(self, svc, sample_payload):
        """
        Verifica que el p99 de 1000 verificaciones de paquetes alterados
        sea menor a 500 ms.
        """
        times = []
        for _ in range(1000):
            packet = svc.sign_packet(sample_payload)
            packet["payload"]["total"] = 0.01
            t0 = time.perf_counter()
            try:
                svc.verify_packet(packet)
            except IntegrityError:
                pass
            times.append((time.perf_counter() - t0) * 1000)

        times.sort()
        p50 = times[int(len(times) * 0.50)]
        p95 = times[int(len(times) * 0.95)]
        p99 = times[int(len(times) * 0.99)]

        print(f"\n  📊 1000 detecciones — p50: {p50:.3f} ms | p95: {p95:.3f} ms | p99: {p99:.3f} ms")
        assert p99 < self.DEADLINE_MS, (
            f"p99 de detección = {p99:.2f} ms — INCUMPLE ASR-2 (límite: 500 ms)"
        )

# ---------------------------------------------------------------------------
# Tests del proveedor mock
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_clean_packet_passes_verification(self, provider, svc):
        packet = provider.get_monthly_cost("proj-001", tamper=False)
        payload = svc.verify_packet(packet)
        assert payload["total"] == 2100.50

    def test_tampered_packet_fails_verification(self, provider, svc):
        packet = provider.get_monthly_cost("proj-001", tamper=True)
        with pytest.raises(IntegrityError):
            svc.verify_packet(packet)
