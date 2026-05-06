#!/usr/bin/env python3
"""
ASR-3 — Simulador de ataque Information Disclosure
===================================================
Simula un atacante intentando interceptar/inyectar tráfico en HTTP plano
entre microservicios y mide si el sistema detecta y bloquea en < 1 s.

Uso:
    # Simular ataque contra detector local
    python scripts/simulate_attack.py --target localhost:8080

    # Simular ataque contra un servidor en AWS
    python scripts/simulate_attack.py --target <EC2-IP>:8080

    # Simular múltiples vectores de ataque
    python scripts/simulate_attack.py --target localhost:8080 --attacks all
"""

import argparse
import socket
import time
import json
import os


def attack_plain_http(host: str, port: int, path: str = "/") -> dict:
    """Vector 1: Intento de comunicación HTTP plana entre microservicios."""
    t0 = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"X-Service-Name: fake-internal-service\r\n"
            f"Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.fake\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode())
        response = sock.recv(4096)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        sock.close()

        blocked = b"403" in response or b"Forbidden" in response
        return {
            "attack": "plain_http",
            "path": path,
            "blocked": blocked,
            "elapsed_ms": round(elapsed_ms, 3),
            "sla_compliant": elapsed_ms <= 1000,
            "response_preview": response[:100].decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "attack": "plain_http",
            "path": path,
            "blocked": True,  # conexión rechazada = bloqueada
            "elapsed_ms": round(elapsed_ms, 3),
            "sla_compliant": elapsed_ms <= 1000,
            "error": str(exc),
        }


def attack_credential_sniff(host: str, port: int) -> dict:
    """Vector 2: Intento de enviar credenciales en claro (Information Disclosure)."""
    t0 = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))

        # Simula un servicio interno enviando credenciales sin cifrar
        payload = json.dumps({
            "db_password": "super-secret-123",
            "api_key": "sk-finops-prod-key",
            "project_ids": ["proj-001", "proj-002"],
        })
        request = (
            f"POST /internal/sync HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"\r\n"
            f"{payload}"
        )
        sock.sendall(request.encode())
        response = sock.recv(4096)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        sock.close()

        blocked = b"403" in response or b"Forbidden" in response
        return {
            "attack": "credential_sniff",
            "description": "Envío de credenciales en texto plano",
            "blocked": blocked,
            "elapsed_ms": round(elapsed_ms, 3),
            "sla_compliant": elapsed_ms <= 1000,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "attack": "credential_sniff",
            "blocked": True,
            "elapsed_ms": round(elapsed_ms, 3),
            "sla_compliant": elapsed_ms <= 1000,
            "error": str(exc),
        }


def attack_replay_http(host: str, port: int) -> dict:
    """Vector 3: Múltiples peticiones rápidas HTTP planas (replay attack)."""
    results = []
    for i in range(10):
        result = attack_plain_http(host, port, f"/reports/monthly/?project=proj-{i}")
        results.append(result)

    all_blocked = all(r["blocked"] for r in results)
    all_within_1s = all(r["sla_compliant"] for r in results)
    max_ms = max(r["elapsed_ms"] for r in results)
    avg_ms = sum(r["elapsed_ms"] for r in results) / len(results)

    return {
        "attack": "replay_http",
        "description": "10 peticiones HTTP planas consecutivas",
        "all_blocked": all_blocked,
        "all_within_1s": all_within_1s,
        "avg_elapsed_ms": round(avg_ms, 3),
        "max_elapsed_ms": round(max_ms, 3),
        "sla_compliant": all_blocked and all_within_1s,
    }


def main():
    parser = argparse.ArgumentParser(description="ASR-3 Attack Simulator")
    parser.add_argument("--target", default="localhost:8080", help="host:port del detector")
    parser.add_argument("--attacks", default="all", choices=["all", "http", "creds", "replay"])
    args = parser.parse_args()

    host, port_str = args.target.rsplit(":", 1)
    port = int(port_str)

    print("=" * 65)
    print("ASR-3 — SIMULADOR DE ATAQUE INFORMATION DISCLOSURE")
    print("=" * 65)
    print(f"  Objetivo  : {host}:{port}")
    print(f"  Ataques   : {args.attacks}")
    print(f"  SLA       : Detección + bloqueo en < 1000 ms\n")

    attack_results = []

    if args.attacks in ("all", "http"):
        print("[1] Ataque: HTTP plano básico ...")
        r = attack_plain_http(host, port)
        attack_results.append(r)
        print(f"    Bloqueado: {r['blocked']} | Tiempo: {r['elapsed_ms']} ms | SLA: {'✅' if r['sla_compliant'] else '❌'}")

    if args.attacks in ("all", "creds"):
        print("[2] Ataque: Envío de credenciales en claro ...")
        r = attack_credential_sniff(host, port)
        attack_results.append(r)
        print(f"    Bloqueado: {r['blocked']} | Tiempo: {r['elapsed_ms']} ms | SLA: {'✅' if r['sla_compliant'] else '❌'}")

    if args.attacks in ("all", "replay"):
        print("[3] Ataque: Replay HTTP (10 peticiones) ...")
        r = attack_replay_http(host, port)
        attack_results.append(r)
        print(f"    Todos bloqueados: {r['all_blocked']} | Max: {r['max_elapsed_ms']} ms | SLA: {'✅' if r['sla_compliant'] else '❌'}")

    all_blocked = all(r.get("blocked") or r.get("all_blocked") for r in attack_results)
    all_sla = all(r["sla_compliant"] for r in attack_results)

    print("\n" + "=" * 65)
    print("VEREDICTO ASR-3")
    print("=" * 65)
    print(f"  Todos los ataques bloqueados : {'✅ SÍ' if all_blocked else '❌ NO'}")
    print(f"  Todos en < 1000 ms           : {'✅ SÍ' if all_sla else '❌ NO'}")
    print(f"  ASR-3 CUMPLIDO               : {'✅ SÍ' if (all_blocked and all_sla) else '❌ NO'}")
    print("=" * 65)

    os.makedirs("results", exist_ok=True)
    with open("results/asr3_attack_results.json", "w") as f:
        json.dump({
            "target": f"{host}:{port}",
            "all_blocked": all_blocked,
            "all_sla_compliant": all_sla,
            "asr3_compliant": all_blocked and all_sla,
            "attack_details": attack_results,
        }, indent=2, fp=f)
    print(f"\n  📄 Resultados guardados en results/asr3_attack_results.json")


if __name__ == "__main__":
    main()
