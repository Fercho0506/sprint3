#!/usr/bin/env python3
"""
ASR-2 — Script de experimento de integridad de datos
=====================================================
Ejecuta el experimento completo y muestra los resultados en consola.

Uso:
    python scripts/run_experiment.py
    python scripts/run_experiment.py --host http://<ALB_DNS>
    python scripts/run_experiment.py --iterations 5000
"""

import argparse
import time
import json
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from asr2_integridad.app.integrity import (
    PacketIntegrityService,
    MockCloudProvider,
    IntegrityError,
)


def run_local_experiment(iterations: int = 1000, tamper_rate: float = 0.3):
    """
    Ejecuta el experimento directamente sin Django.
    tamper_rate: fracción de paquetes que serán alterados (0.0 - 1.0)
    """
    HMAC_KEY = os.environ.get("HMAC_SECRET_KEY", "dev-hmac-key-change-in-production")
    svc = PacketIntegrityService(HMAC_KEY)
    provider = MockCloudProvider(HMAC_KEY)

    print("=" * 60)
    print("ASR-2 — EXPERIMENTO DE INTEGRIDAD DE DATOS")
    print("=" * 60)
    print(f"  Iteraciones    : {iterations}")
    print(f"  Tasa de tamper : {tamper_rate * 100:.0f}% de paquetes alterados")
    print(f"  Deadline SLA   : 500 ms\n")

    results = {
        "valid_times_ms": [],
        "tamper_times_ms": [],
        "false_negatives": 0,   # paquetes alterados que pasaron (NO debe ocurrir nunca)
        "false_positives": 0,   # paquetes válidos rechazados (NO debe ocurrir nunca)
    }

    import random
    random.seed(42)

    for i in range(iterations):
        tamper = random.random() < tamper_rate
        packet = provider.get_monthly_cost(f"proj-{i % 10}", tamper=tamper)

        t0 = time.perf_counter()
        try:
            svc.verify_packet(packet)
            elapsed = (time.perf_counter() - t0) * 1000
            if tamper:
                results["false_negatives"] += 1
                print(f"  ⚠️  FALSO NEGATIVO en iteración {i} — paquete alterado no detectado!")
            else:
                results["valid_times_ms"].append(elapsed)
        except IntegrityError:
            elapsed = (time.perf_counter() - t0) * 1000
            if not tamper:
                results["false_positives"] += 1
                print(f"  ⚠️  FALSO POSITIVO en iteración {i} — paquete válido rechazado!")
            else:
                results["tamper_times_ms"].append(elapsed)

    # Análisis de resultados
    print("\n" + "=" * 60)
    print("RESULTADOS")
    print("=" * 60)

    total_valid = len(results["valid_times_ms"])
    total_tamper = len(results["tamper_times_ms"])

    print(f"\n📦 Paquetes válidos procesados   : {total_valid}")
    print(f"🔴 Paquetes alterados detectados : {total_tamper}")
    print(f"❌ Falsos negativos              : {results['false_negatives']}")
    print(f"❌ Falsos positivos              : {results['false_positives']}")

    if results["tamper_times_ms"]:
        t = results["tamper_times_ms"]
        t.sort()
        p50 = statistics.median(t)
        p95 = t[int(len(t) * 0.95)]
        p99 = t[int(len(t) * 0.99)]
        max_t = max(t)
        within_500 = sum(1 for x in t if x <= 500)

        print(f"\n⏱  Tiempos de detección (ms):")
        print(f"   p50 = {p50:.3f} ms")
        print(f"   p95 = {p95:.3f} ms")
        print(f"   p99 = {p99:.3f} ms")
        print(f"   max = {max_t:.3f} ms")
        print(f"   Detecciones <= 500ms: {within_500}/{total_tamper} ({within_500/total_tamper*100:.1f}%)")

    print("\n" + "=" * 60)
    print("VEREDICTO ASR-2")
    print("=" * 60)

    sla_integrity = results["false_negatives"] == 0 and results["false_positives"] == 0
    sla_timing = all(t <= 500 for t in results["tamper_times_ms"]) if results["tamper_times_ms"] else True

    print(f"  100% integridad (0 false negatives/positives) : {'✅ CUMPLE' if sla_integrity else '❌ INCUMPLE'}")
    print(f"  Detección < 500 ms (todos los paquetes)       : {'✅ CUMPLE' if sla_timing else '❌ INCUMPLE'}")
    print(f"  ASR-2 CUMPLIDO                                : {'✅ SÍ' if (sla_integrity and sla_timing) else '❌ NO'}")
    print("=" * 60)

    # Guardar resultados
    os.makedirs("results", exist_ok=True)
    with open("results/asr2_experiment.json", "w") as f:
        json.dump({
            "iterations": iterations,
            "tamper_rate": tamper_rate,
            "false_negatives": results["false_negatives"],
            "false_positives": results["false_positives"],
            "sla_integrity_ok": sla_integrity,
            "sla_timing_ok": sla_timing,
            "asr2_compliant": sla_integrity and sla_timing,
            "detection_times_ms": results["tamper_times_ms"][:100],  # muestra de 100
        }, indent=2)
    print(f"\n  📄 Resultados guardados en results/asr2_experiment.json")


def main():
    parser = argparse.ArgumentParser(description="ASR-2 Integrity Experiment")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--tamper-rate", type=float, default=0.3,
                        help="Fracción de paquetes a alterar (0.0-1.0)")
    args = parser.parse_args()
    run_local_experiment(iterations=args.iterations, tamper_rate=args.tamper_rate)


if __name__ == "__main__":
    main()
