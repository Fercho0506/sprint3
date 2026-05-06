# ASR-2 — Integridad de Datos 100% / Detección < 500 ms

## Descripción del ASR

> Como usuario de empresa cliente, dado que el sistema está funcionando correctamente y procesando datos financieros de consumo cloud de mi organización, quiero que el **100% de los paquetes** de datos que la plataforma intercambie con el proveedor cloud no sean modificados en tránsito, de forma que cualquier alteración sea detectada y el paquete rechazado en **menos de 500 milisegundos**, garantizando que ningún dato corrompido persista en los reportes de gasto.

## Estrategia del experimento

| Componente | Mecanismo |
|-----------|-----------|
| Firma de salida | HMAC-SHA256 sobre el payload JSON serializado canónicamente |
| Verificación de entrada | `hmac.compare_digest` (tiempo constante, inmune a timing attacks) |
| Clave secreta | AWS Secrets Manager (`finops/hmac-key`) en producción |
| Rechazo | `IntegrityError` lanzada antes de persistir en RDS |
| Alerta | Log de ERROR en CloudWatch Logs |

**Flujo:**
```
FinOps Server ──sign──▶ Red ──▶ Verificación HMAC ──▶ (válido) → RDS
                                      │
                              (alterado) ──▶ RECHAZO + Log ERROR
```

## Archivos

```
asr2_integridad/
├── app/
│   ├── integrity.py  ← PacketIntegrityService, MockCloudProvider, IntegrityStats
│   └── views.py      ← Endpoints /cloud-data/ingest/ y /metrics/integrity/
├── tests/
│   └── test_integrity.py  ← pytest: firma, verificación, rendimiento p99
└── scripts/
    └── run_experiment.py  ← Experimento standalone (sin Django)
```

## Pasos para ejecutar el experimento

### Opción A — Experimento standalone (recomendado para validar primero)

```bash
# Desde la raíz del repo
python asr2_integridad/scripts/run_experiment.py --iterations 2000 --tamper-rate 0.3
```

Salida esperada:
```
ASR-2 — EXPERIMENTO DE INTEGRIDAD DE DATOS
  Iteraciones    : 2000
  Tasa de tamper : 30% de paquetes alterados
  Deadline SLA   : 500 ms

RESULTADOS
  Paquetes válidos procesados   : 1400
  Paquetes alterados detectados : 600
  Falsos negativos              : 0
  Falsos positivos              : 0
  Tiempos de detección:
     p99 = 0.08 ms

VEREDICTO ASR-2
  100% integridad   : ✅ CUMPLE
  Detección < 500ms : ✅ CUMPLE
  ASR-2 CUMPLIDO    : ✅ SÍ
```

### Opción B — Tests unitarios completos

```bash
pytest asr2_integridad/tests/test_integrity.py -v --tb=short
```

### Opción C — Vía HTTP (con Django corriendo)

```bash
# Paquete limpio
curl "http://localhost:8000/cloud-data/ingest/?project=proj-001"

# Paquete alterado (simula MITM)
curl "http://localhost:8000/cloud-data/ingest/?project=proj-001&tamper=1"

# Métricas acumuladas
curl "http://localhost:8000/metrics/integrity/"
```

## Cómo leer los resultados

| Métrica | Valor esperado | Qué indica |
|---------|---------------|------------|
| `false_negatives` | 0 | Ningún paquete corrupto llegó a la DB |
| `false_positives` | 0 | No se rechazaron paquetes válidos |
| `p99 detection_ms` | < 500 | El 99% de detecciones son < 500 ms |
| `asr2_compliant` | `true` | ASR cumplido |

## Qué configurar en AWS antes del experimento

1. **AWS Secrets Manager**: crear secreto `finops/hmac-key` con el valor `{"hmac_key": "<clave-aleatoria-256-bits>"}`
2. **IAM Role**: dar permiso `secretsmanager:GetSecretValue` al rol de las instancias EC2 del FinOps Server
3. **Variable de entorno en EC2**: `HMAC_SECRET_KEY` (o configurar la app para leer de Secrets Manager)
4. **CloudWatch Logs**: configurar el Log Group `/finops/integrity` para monitorear rechazos
