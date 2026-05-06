# ASR-3 — Detección de Canales No Cifrados en < 1 segundo

## Descripción del ASR

> Cuando un hacker realice un ataque de tipo **Information Disclosure** sobre la comunicación entre los microservicios, dado que el sistema está funcionando correctamente, se espera que el sistema **detecte cualquier intento de transmisión de información a través de canales no cifrados** y **bloquee la comunicación en menos de 1 segundo** después de ser detectada.

## Estrategia del experimento

| Mecanismo | Dónde aplica |
|-----------|-------------|
| `TLSEnforcementMiddleware` | Django — rechaza peticiones HTTP planas inmediatamente |
| `ServiceMeshClient` | Llamadas entre microservicios — eleva HTTP a HTTPS automáticamente |
| `NonEncryptedTrafficDetector` | Servidor TCP que escucha en el puerto HTTP y bloquea conexiones |
| Kong Plugin `request-termination` | API Gateway — bloquea rutas sin TLS (config en `infra/`) |
| Security Group AWS | Ingress solo en 443 desde el ALB; puerto 80 cerrado |

## Archivos

```
asr3_cifrado/
├── app/
│   ├── tls_enforcement.py  ← Middleware, ServiceMeshClient, Detector TCP
│   └── views.py            ← /security/tls-audit/ y /security/non-encrypted-stats/
├── tests/
│   └── test_tls_enforcement.py  ← pytest: middleware, detector, tiempos
└── scripts/
    └── simulate_attack.py  ← Simulador de ataques Information Disclosure
```

## Pasos para ejecutar el experimento

### Opción A — Tests unitarios (sin AWS, sin Docker)

```bash
pytest asr3_cifrado/tests/test_tls_enforcement.py -v --tb=short
```

Todos los tests deben pasar, incluyendo el timing < 1000 ms.

### Opción B — Simulación de ataque local

```bash
# Terminal 1: arrancar el detector
python -c "
from asr3_cifrado.app.tls_enforcement import NonEncryptedTrafficDetector
import time
d = NonEncryptedTrafficDetector(listen_port=8080)
d.start()
print('Detector activo en :8080')
time.sleep(60)
"

# Terminal 2: simular el ataque
python asr3_cifrado/scripts/simulate_attack.py --target localhost:8080 --attacks all
```

### Opción C — Vía Django + HTTP simulado

```bash
# Arrancar Django en modo "permite http" para probar el middleware
python manage.py runserver 8000

# Enviar petición HTTP plana (el middleware responderá 403)
curl -v http://localhost:8000/health/

# Resultado esperado:
# HTTP/1.1 403 Forbidden
# {"error": "Comunicación no cifrada bloqueada", ...}

# Ver estadísticas del detector
curl https://localhost:8000/security/non-encrypted-stats/
```

### Opción D — AWS con Kong configurado

Ver `infra/kong/tls-plugin.yaml` para la configuración del plugin `request-termination` de Kong.

```bash
# Auditar TLS de todos los microservicios
curl https://<ALB_DNS>/security/tls-audit/
```

## Cómo leer los resultados

**`results/asr3_attack_results.json`:**

| Campo | Valor esperado | Qué significa |
|-------|---------------|--------------|
| `all_blocked` | `true` | Todos los ataques fueron bloqueados |
| `all_sla_compliant` | `true` | Todos los bloqueos ocurrieron en < 1000 ms |
| `asr3_compliant` | `true` | ASR cumplido |

**Endpoint `/security/non-encrypted-stats/`:**
```json
{
  "total_blocked": 10,
  "avg_detection_ms": 0.8,
  "max_detection_ms": 2.1,
  "all_within_1s": true,
  "asr3_compliant": true
}
```

## Qué configurar en AWS antes del experimento

1. **Security Groups**: Puerto 80 CERRADO para las instancias EC2. Solo 443 desde el ALB.
2. **ALB Listener**: Redirigir HTTP:80 → HTTPS:443 automáticamente.
3. **Kong Plugin**: aplicar `request-termination` en rutas sin TLS (ver `infra/kong/`).
4. **ACM Certificate**: certificado TLS válido en el ALB.
5. **VPC**: Habilitar VPC Flow Logs para detectar tráfico HTTP en la red interna.
