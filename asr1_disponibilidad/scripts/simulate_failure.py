#!/usr/bin/env python3
"""
ASR-1 — Script de simulación de fallo (Chaos Engineering ligero)
================================================================
Simula un fallo de instancia EC2 o de la base de datos y mide
cuánto tarda el sistema en recuperarse.

Uso:
    python scripts/simulate_failure.py --target api --host http://localhost:8000
    python scripts/simulate_failure.py --target db  --host http://localhost:8000

En AWS:
    Reemplazar la función `kill_target` por el comando correspondiente
    de boto3 para detener la instancia EC2 o simular un failover de RDS.
"""

import argparse
import time
import requests
import boto3


def wait_for_recovery(host: str, timeout: int = 300) -> float:
    """Sondea /health/ hasta que responde 200. Retorna segundos de recovery."""
    print(f"⏳ Esperando recuperación en {host}/health/ ...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{host}/health/", timeout=3)
            if r.status_code == 200:
                elapsed = time.time() - start
                print(f"✅ Recuperado en {elapsed:.2f}s")
                return elapsed
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    raise TimeoutError(f"No se recuperó en {timeout}s")


def simulate_ec2_failure(instance_id: str, region: str = "us-east-1"):
    """Para la instancia EC2 (simula fallo)."""
    ec2 = boto3.client("ec2", region_name=region)
    print(f"🔴 Deteniendo instancia {instance_id} ...")
    ec2.stop_instances(InstanceIds=[instance_id])
    # Esperar 10s para que el ALB la detecte como unhealthy
    time.sleep(10)


def simulate_rds_reboot(db_instance_id: str, region: str = "us-east-1"):
    """Reinicia la instancia RDS con failover (Multi-AZ)."""
    rds = boto3.client("rds", region_name=region)
    print(f"🔴 Ejecutando failover RDS {db_instance_id} ...")
    rds.reboot_db_instance(
        DBInstanceIdentifier=db_instance_id,
        ForceFailover=True,
    )


def main():
    parser = argparse.ArgumentParser(description="ASR-1 Chaos Simulator")
    parser.add_argument("--host", default="http://localhost:8000", help="URL base de la app")
    parser.add_argument("--target", choices=["api", "db", "local"], default="local",
                        help="Qué componente fallar (local = simula localmente con curl)")
    parser.add_argument("--instance-id", help="EC2 instance ID (para --target api)")
    parser.add_argument("--db-instance-id", help="RDS instance ID (para --target db)")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    print("=" * 60)
    print("ASR-1 — EXPERIMENTO DE DISPONIBILIDAD (Simulación de fallo)")
    print("=" * 60)

    # 1. Verificar que el sistema está sano antes del fallo
    print("\n[1] Verificando estado inicial ...")
    try:
        r = requests.get(f"{args.host}/health/", timeout=5)
        assert r.status_code == 200, f"El sistema ya está en estado anómalo: {r.status_code}"
        print(f"    ✅ Sistema sano (HTTP 200)")
    except Exception as exc:
        print(f"    ❌ Error verificando estado inicial: {exc}")
        return

    # 2. Inducir el fallo
    print("\n[2] Induciendo fallo ...")
    failure_time = time.time()

    if args.target == "api" and args.instance_id:
        simulate_ec2_failure(args.instance_id, args.region)
    elif args.target == "db" and args.db_instance_id:
        simulate_rds_reboot(args.db_instance_id, args.region)
    else:
        # Modo local: simplemente esperamos que el usuario detenga algo manualmente
        print("    ⚠️  Modo local — detén manualmente el servidor Django y presiona Enter.")
        input("    [Enter cuando hayas detenido el servidor]")

    # 3. Medir tiempo de recuperación
    print("\n[3] Midiendo tiempo de recuperación ...")
    try:
        recovery_seconds = wait_for_recovery(args.host, timeout=600)
        monthly_equivalent = recovery_seconds * (30 * 24 * 3600) / (30 * 24 * 3600)

        print("\n" + "=" * 60)
        print("RESULTADO")
        print("=" * 60)
        print(f"  Tiempo de recovery     : {recovery_seconds:.2f}s")
        print(f"  Máx downtime mensual   : 26446s")
        print(f"  ✅ SLA 99.9% CUMPLE    : {'SÍ' if recovery_seconds < 26446 else 'NO ❌'}")
        print("=" * 60)
    except TimeoutError as exc:
        print(f"❌ {exc}")


if __name__ == "__main__":
    main()
