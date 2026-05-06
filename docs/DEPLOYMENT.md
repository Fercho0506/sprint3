# Guía de Despliegue y Ejecución de Experimentos — Sprint 3

## Índice

1. [Estructura del repositorio](#1-estructura-del-repositorio)
2. [Ejecución local (sin AWS)](#2-ejecución-local-sin-aws)
3. [Despliegue en AWS](#3-despliegue-en-aws)
4. [Experimento ASR-1 — Disponibilidad 99.9%](#4-experimento-asr-1--disponibilidad-999)
5. [Experimento ASR-2 — Integridad de datos](#5-experimento-asr-2--integridad-de-datos)
6. [Experimento ASR-3 — Canales no cifrados](#6-experimento-asr-3--canales-no-cifrados)
7. [Leer e interpretar resultados](#7-leer-e-interpretar-resultados)
8. [Problemas frecuentes](#8-problemas-frecuentes)

---

## 1. Estructura del repositorio

```
sprint3/
├── README.md                        ← Resumen general
├── requirements.txt                 ← Dependencias Python
├── docker-compose.yml               ← Postgres + Redis para pruebas locales
│
├── asr1_disponibilidad/
│   ├── README.md                    ← Descripción detallada ASR-1
│   ├── app/
│   │   ├── views.py                 ← Circuit Breaker + Health Check + Uptime Tracker
│   │   ├── urls.py
│   │   └── settings.py
│   ├── tests/
│   │   └── load_test_availability.py   ← Test de carga con Locust
│   └── scripts/
│       └── simulate_failure.py         ← Simulador de fallos EC2/RDS
│
├── asr2_integridad/
│   ├── README.md
│   ├── app/
│   │   ├── integrity.py             ← HMAC-SHA256 + MockCloudProvider
│   │   └── views.py
│   ├── tests/
│   │   └── test_integrity.py        ← pytest: firma, verificación, p99 < 500ms
│   └── scripts/
│       └── run_experiment.py        ← Experimento standalone
│
├── asr3_cifrado/
│   ├── README.md
│   ├── app/
│   │   ├── tls_enforcement.py       ← Middleware + ServiceMeshClient + Detector TCP
│   │   └── views.py
│   ├── tests/
│   │   └── test_tls_enforcement.py  ← pytest: bloqueo + timing < 1s
│   └── scripts/
│       └── simulate_attack.py       ← Simulador de ataques Information Disclosure
│
├── infra/
│   ├── terraform/
│   │   └── main.tf                  ← Infraestructura AWS completa
│   └── kong/
│       └── tls-plugin.yaml          ← Config Kong para TLS enforcement
│
└── docs/
    └── DEPLOYMENT.md                ← Este archivo
```

---

## 2. Ejecución local (sin AWS)

### 2.1 Requisitos

- Python 3.11+
- Docker y Docker Compose
- (Opcional) Locust para tests de carga

### 2.2 Instalación

```bash
# Clonar el repositorio
git clone <URL_REPO>
cd sprint3

# Instalar dependencias
pip install -r requirements.txt
```

### 2.3 Levantar servicios de soporte

```bash
docker compose up -d
# Esto arranca PostgreSQL en :5432 y Redis en :6379
```

### 2.4 Variables de entorno

```bash
export DJANGO_SETTINGS_MODULE=asr1_disponibilidad.app.settings
export DB_HOST=localhost
export DB_NAME=finops
export DB_USER=finops_user
export DB_PASSWORD=changeme
export REDIS_URL=redis://localhost:6379/0
export HMAC_SECRET_KEY=dev-hmac-key-cambiar-en-produccion
```

### 2.5 Arrancar Django

```bash
# Crear tablas (primera vez)
python -c "
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'asr1_disponibilidad.app.settings'
django.setup()
from django.db import connection
connection.cursor().execute('SELECT 1')
print('DB OK')
"

python manage.py runserver 8000
```

### 2.6 Verificar que todo funciona

```bash
curl http://localhost:8000/health/
# Esperado: {"status": "healthy", ...}

curl http://localhost:8000/metrics/availability/
# Esperado: {"sla_target_pct": 99.9, "sla_compliant": true, ...}
```

---

## 3. Despliegue en AWS

### 3.1 Prerrequisitos en AWS

Antes de ejecutar Terraform, el equipo debe:

| Paso | Qué hacer | Dónde |
|------|-----------|-------|
| 1 | Crear una cuenta AWS o usar la del curso | AWS Console |
| 2 | Crear un par de llaves EC2 | EC2 > Key Pairs > Create |
| 3 | Solicitar o importar un certificado TLS | ACM > Request Certificate |
| 4 | Anotar el ARN del certificado | ACM > Certificates |
| 5 | Identificar la AMI de Ubuntu 24.04 en us-east-1 | EC2 > AMIs > Community |

**AMI Ubuntu 24.04 LTS (us-east-1):** `ami-0c7217cdde317cfec` *(verificar vigencia)*

### 3.2 Clonar el código en una instancia o usar CloudShell

```bash
# En AWS CloudShell o en tu máquina con AWS CLI configurado
git clone <URL_REPO>
cd sprint3/infra/terraform
```

### 3.3 Configurar variables de Terraform

```bash
# Crear archivo de variables (NO subir a git — ya está en .gitignore)
cat > terraform.tfvars <<EOF
aws_region          = "us-east-1"
project_name        = "finops-sprint3"
ami_id              = "ami-0c7217cdde317cfec"
key_pair_name       = "mi-par-de-llaves"
db_password         = "MiPasswordSegura123!"
hmac_secret_key     = "clave-hmac-aleatoria-256-bits-aqui"
acm_certificate_arn = "arn:aws:acm:us-east-1:XXXXXXXXXXXX:certificate/xxxxxx"
EOF
```

> **Generar una clave HMAC segura:**
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

### 3.4 Aplicar la infraestructura

```bash
terraform init
terraform plan        # Revisar qué se va a crear
terraform apply       # Escribir 'yes' para confirmar

# Al terminar, anota el ALB DNS:
terraform output alb_dns_name
```

### 3.5 Subir el código a las instancias EC2

Una vez que el ASG lanza las instancias, conectarse por SSM Session Manager o SSH:

```bash
# Opción A — SSM (recomendado, sin abrir puerto 22)
aws ssm start-session --target <INSTANCE_ID>

# Opción B — SSH
ssh -i mi-par-de-llaves.pem ubuntu@<EC2_PUBLIC_IP>
```

En la instancia:

```bash
git clone <URL_REPO> /opt/sprint3
cd /opt/sprint3
pip install -r requirements.txt

# Las variables de entorno ya están en el user_data del Launch Template
# Si necesitas editarlas manualmente:
export DB_HOST=<RDS_ENDPOINT>  # de `terraform output rds_endpoint`
export REDIS_URL=redis://<REDIS_ENDPOINT>:6379/0
export HMAC_SECRET_KEY=<la-misma-clave-usada-en-tfvars>

python manage.py runserver 0.0.0.0:8000
```

### 3.6 Configurar Kong (ASR-3)

```bash
# Desde una instancia con acceso al admin port de Kong (o desde el bastion)
curl -X POST http://<KONG-ADMIN-IP>:8001/config \
     -F "config=@/opt/sprint3/infra/kong/tls-plugin.yaml"

# Verificar que el plugin está activo
curl http://<KONG-ADMIN-IP>:8001/plugins
```

---

## 4. Experimento ASR-1 — Disponibilidad 99.9%

### Objetivo

Demostrar que la plataforma mantiene disponibilidad >= 99.9% y que el tiempo de recovery ante un fallo de instancia es mínimo gracias al Auto Scaling Group.

### Pasos

**Paso 1 — Verificar estado inicial**
```bash
curl https://<ALB_DNS>/health/
curl https://<ALB_DNS>/metrics/availability/
```

**Paso 2 — Ejecutar test de carga (5 minutos)**
```bash
locust -f asr1_disponibilidad/tests/load_test_availability.py \
       --host https://<ALB_DNS> \
       --users 50 --spawn-rate 10 --run-time 5m \
       --headless --csv=results/asr1
```

**Paso 3 — Simular fallo de instancia (mientras el test de carga corre)**
```bash
# Obtener el ID de una instancia del ASG
INSTANCE_ID=$(aws autoscaling describe-auto-scaling-instances \
  --query 'AutoScalingInstances[0].InstanceId' --output text)

python asr1_disponibilidad/scripts/simulate_failure.py \
       --target api \
       --instance-id $INSTANCE_ID \
       --host https://<ALB_DNS> \
       --region us-east-1
```

**Paso 4 — Ver resultados**
```bash
cat results/asr1_summary.csv
```

**Resultado esperado:** `availability_pct >= 99.9` y `sla_compliant = True`

---

## 5. Experimento ASR-2 — Integridad de datos

### Objetivo

Demostrar que la plataforma detecta y rechaza el 100% de los paquetes alterados en tránsito, y que la detección ocurre en menos de 500 ms.

### Pasos

**Paso 1 — Ejecutar experimento standalone (recomendado)**
```bash
python asr2_integridad/scripts/run_experiment.py --iterations 2000 --tamper-rate 0.3
```

**Paso 2 — Ejecutar tests unitarios con reporte de tiempos**
```bash
pytest asr2_integridad/tests/test_integrity.py -v -s
# El flag -s muestra los tiempos de detección p50/p95/p99
```

**Paso 3 — Probar vía HTTP (opcional, requiere Django corriendo)**
```bash
# Paquete íntegro
curl "https://<ALB_DNS>/cloud-data/ingest/?project=proj-001"

# Paquete alterado (MITM simulado)
curl "https://<ALB_DNS>/cloud-data/ingest/?project=proj-001&tamper=1"

# Métricas acumuladas
curl "https://<ALB_DNS>/metrics/integrity/"
```

**Resultado esperado:**
- `false_negatives = 0` (ningún paquete corrupto pasó)
- `p99 detection_ms < 500`
- `asr2_compliant = true`

---

## 6. Experimento ASR-3 — Canales no cifrados

### Objetivo

Demostrar que cualquier intento de comunicación entre microservicios por HTTP plano es detectado y bloqueado en menos de 1 segundo.

### Pasos

**Paso 1 — Tests unitarios**
```bash
pytest asr3_cifrado/tests/test_tls_enforcement.py -v -s
```

**Paso 2 — Simulación de ataque local**

```bash
# Terminal 1: arrancar detector
python -c "
from asr3_cifrado.app.tls_enforcement import NonEncryptedTrafficDetector
import time
d = NonEncryptedTrafficDetector(listen_port=8080)
d.start()
print('Detector activo — esperando ataques...')
time.sleep(120)
"

# Terminal 2: lanzar ataques
python asr3_cifrado/scripts/simulate_attack.py --target localhost:8080 --attacks all
```

**Paso 3 — Verificar en AWS que el Security Group bloquea HTTP:80**
```bash
# Intentar conectar por puerto 80 (debe fallar — SG no lo permite)
curl --max-time 5 http://<ALB_DNS>/health/
# Resultado esperado: Connection refused o timeout

# HTTPS debe funcionar
curl https://<ALB_DNS>/health/
```

**Paso 4 — Auditar TLS de todos los microservicios**
```bash
curl https://<ALB_DNS>/security/tls-audit/
```

**Resultado esperado:**
- `all_blocked = true`
- `all_sla_compliant = true` (todos < 1000 ms)
- `asr3_compliant = true`

---

## 7. Leer e interpretar resultados

### Archivos generados en `results/`

| Archivo | Experimento | Columna clave |
|---------|-------------|--------------|
| `asr1_summary.csv` | ASR-1 | `availability_pct` >= 99.9 |
| `asr1_stats.csv` (Locust) | ASR-1 | Failure rate <= 0.1% |
| `asr2_experiment.json` | ASR-2 | `asr2_compliant: true` |
| `asr3_attack_results.json` | ASR-3 | `asr3_compliant: true` |

### Tabla resumen de criterios de éxito

| ASR | Métrica | Umbral | ¿Cumple? |
|-----|---------|--------|---------|
| ASR-1 | Disponibilidad mensual | >= 99.9% | Ver `availability_pct` |
| ASR-1 | Downtime mensual equiv. | <= 26,446 s | Ver `monthly_equivalent_downtime_seconds` |
| ASR-2 | Falsos negativos | = 0 | Ver `false_negatives` |
| ASR-2 | Tiempo de detección p99 | < 500 ms | Ver `detection_times_ms` |
| ASR-3 | Ataques bloqueados | = 100% | Ver `all_blocked` |
| ASR-3 | Tiempo de bloqueo máx | < 1000 ms | Ver `max_detection_ms` |

---

## 8. Problemas frecuentes

### "Connection refused" al conectar a PostgreSQL local
```bash
docker compose ps  # verificar que el contenedor está corriendo
docker compose logs postgres
```

### "ModuleNotFoundError" al importar los módulos
```bash
# Ejecutar desde la raíz del repo con PYTHONPATH configurado
export PYTHONPATH=$(pwd)
python asr2_integridad/scripts/run_experiment.py
```

### El test de Locust reporta 100% de fallos
```bash
# Verificar que Django está corriendo y el host es correcto
curl http://localhost:8000/health/
# Si falla, revisar las variables de entorno (DB_HOST, REDIS_URL)
```

### Terraform falla con "InvalidAMIID"
```bash
# Buscar la AMI de Ubuntu 24.04 en tu región
aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-*" \
  --query 'Images[0].ImageId' --output text
```

### El Security Group del ALB permite HTTP:80
El `main.tf` solo abre el puerto 443. Si hay tráfico HTTP:80, revisar si hay otro SG aplicado manualmente o un listener HTTP:80 antiguo en el ALB y eliminarlo.

---

## Contacto

Si tienen dudas técnicas sobre el código, crear un issue en el repo o contactar al autor del Sprint 3.
