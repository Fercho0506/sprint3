# ASR-1 — Disponibilidad 99.9% Mensual

## Descripción del ASR

> Como usuario de empresa cliente, cuando solicite consultar el reporte mensual de gasto cloud por proyecto, dado que el sistema se encuentra en operación normal, se espera que el servicio esté disponible y responda la solicitud, de forma que la plataforma garantice una **disponibilidad mínima del 99.9% mensual** (máximo 8 horas y 46 segundos de tiempo fuera de servicio no planificado por mes).

## Estrategia del experimento

| Táctica | Implementación |
|---------|---------------|
| Health check profundo | Verifica DB (RDS), Redis (ElastiCache) y el propio proceso Django |
| Circuit Breaker | Evita cascada de fallos en el servicio de reportes |
| Auto Scaling | Mín 2 instancias en el ASG del API Server (configurado en Terraform) |
| ALB Health Check | El Application Load Balancer retira instancias unhealthy automáticamente |
| RDS Multi-AZ | Failover automático de la base de datos en < 60s |

## Archivos

```
asr1_disponibilidad/
├── app/
│   ├── views.py      ← Circuit Breaker, UptimeTracker, endpoints /health/ y /metrics/
│   ├── urls.py       ← Rutas
│   └── settings.py   ← Config con variables de entorno
├── tests/
│   └── load_test_availability.py  ← Prueba de carga con Locust
└── scripts/
    └── simulate_failure.py        ← Simulador de fallos (Chaos Engineering)
```

## Pasos para ejecutar el experimento

### Opción A — Local (sin AWS)

```bash
# 1. Levantar postgres y redis con Docker
docker compose up -d postgres redis

# 2. Configurar variables de entorno
export DJANGO_SETTINGS_MODULE=asr1_disponibilidad.app.settings
export DB_HOST=localhost DB_NAME=finops DB_USER=finops_user DB_PASSWORD=changeme
export REDIS_URL=redis://localhost:6379/0

# 3. Arrancar Django
python manage.py runserver 8000

# 4. En otra terminal, ejecutar el test de carga (5 minutos)
locust -f asr1_disponibilidad/tests/load_test_availability.py \
       --host http://localhost:8000 \
       --users 50 --spawn-rate 10 --run-time 5m \
       --headless --csv=results/asr1

# 5. Ver resumen
cat results/asr1_summary.csv
```

### Opción B — Simulación de fallo local

```bash
# Con Django corriendo en :8000
python asr1_disponibilidad/scripts/simulate_failure.py \
       --target local --host http://localhost:8000
# El script te pedirá que detengas el servidor manualmente y mide el recovery
```

### Opción C — AWS (experimento real)

```bash
# Con la infra desplegada (ver docs/DEPLOYMENT.md)
python asr1_disponibilidad/scripts/simulate_failure.py \
       --target api \
       --instance-id i-0abc1234def56789 \
       --host http://<ALB_DNS_NAME> \
       --region us-east-1
```

## Cómo leer los resultados

El archivo `results/asr1_summary.csv` y el output de Locust muestran:

| Campo | Qué significa |
|-------|--------------|
| `availability_pct` | Debe ser >= 99.9 para cumplir el SLA |
| `observed_downtime_seconds` | Tiempo real de indisponibilidad observado |
| `monthly_equivalent_downtime_seconds` | Proyección a 30 días del downtime observado |
| `sla_compliant` | `True` = cumple ASR |

### Interpretación del Circuit Breaker

- **CLOSED** → operación normal ✅
- **OPEN** → el servicio de reportes falló 5+ veces, se rechaza tráfico → protege la DB ⚠️
- **HALF_OPEN** → intentando recuperarse, acepta tráfico de prueba 🔄

## Qué configurar en AWS antes del experimento

1. **ALB Health Check**: path `/health/`, interval 10s, threshold 2
2. **Auto Scaling Group** (API Server): Min=2, Desired=2, Max=6
3. **RDS Multi-AZ**: activar Multi-AZ en la consola de RDS
4. **ElastiCache**: cluster mode con réplica en segunda AZ
