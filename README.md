# Sprint 3 — Experimentos ASR (Arquitectura de Software)

Este repositorio contiene el código de los **3 experimentos de atributos de calidad** para la plataforma FinOps cloud.

## Arquitectura de referencia

La plataforma corre sobre AWS con los siguientes componentes:
- **Kong API Gateway** (EC2 t3.small) — enruta tráfico HTTPS:443
- **API Server** (EC2 t3.small, Auto Scaling Min:2 Max:6) — Django + DRF
- **FinOps Server** (EC2 t3.small, Auto Scaling Min:2 Max:4) — Django (FinOps y reportes + Integración cloud)
- **CRON Worker** (EC2 t3.small, Auto Scaling Min:1 Max:3) — tareas asíncronas
- **Amazon RDS PostgreSQL** — base de datos principal
- **Amazon ElastiCache Redis** — caché (TCP:6379/6380)
- **Auth0** — autenticación externa (HTTPS)

---

## Experimentos

| # | ASR | Métrica clave | Carpeta |
|---|-----|--------------|---------|
| 1 | Disponibilidad 99.9% mensual | Máx 8h 46s downtime no planificado | `asr1_disponibilidad/` |
| 2 | Integridad 100% de paquetes, detección < 500 ms | 0 paquetes corruptos en reportes | `asr2_integridad/` |
| 3 | Detección canales no cifrados < 1 s | Bloqueo inmediato de tráfico no TLS | `asr3_cifrado/` |

---

## Requisitos previos (locales)

```bash
python3 -m pip install -r requirements.txt
docker compose up -d   # opcional, para pruebas locales
```

## Despliegue en AWS

Ver [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) para instrucciones paso a paso.

## Ejecución de experimentos

Cada carpeta `asrX_*/` contiene su propio `README.md` con:
- Descripción del experimento
- Cómo levantar el entorno
- Cómo ejecutar el test
- Cómo leer los resultados
