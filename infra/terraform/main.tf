# infra/terraform/main.tf
# ========================
# Infraestructura AWS mínima para los experimentos ASR-1, ASR-2, ASR-3.
# Basada en el diagrama de arquitectura del proyecto FinOps.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.6"
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region"          { default = "us-east-1" }
variable "project_name"        { default = "finops-sprint3" }
variable "ami_id"              { description = "AMI Ubuntu 24.04 LTS" }
variable "key_pair_name"       { description = "Par de llaves EC2 para SSH" }
variable "db_password" { 
  description = "Contraseña RDS" 
  sensitive   = true 
}

variable "hmac_secret_key" { 
  description = "Clave HMAC para ASR-2"   
  sensitive   = true 
}
variable "acm_certificate_arn" { description = "ARN del certificado TLS en ACM" }

# ---------------------------------------------------------------------------
# VPC, Subnets y NAT Gateway (CORREGIDO)
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = { Name = "${var.project_name}-vpc" }
}

data "aws_availability_zones" "available" { state = "available" }

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = { Name = "${var.project_name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = { Name = "${var.project_name}-private-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = { Name = "${var.project_name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- NUEVO: NAT Gateway para salida a internet de instancias en subred privada ---
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project_name}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.main]
  tags          = { Name = "${var.project_name}-nat" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${var.project_name}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name   = "${var.project_name}-alb-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS only - ASR-3 TLS enforced"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-alb-sg" }
}

# Security Group compartido para las instancias de la aplicación (API, FinOps, Cron)
resource "aws_security_group" "app_server" {
  name   = "${var.project_name}-app-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "Trafico desde ALB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-app-sg" }
}

resource "aws_security_group" "rds" {
  name   = "${var.project_name}-rds-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_server.id]
  }
  tags = { Name = "${var.project_name}-rds-sg" }
}

resource "aws_security_group" "redis" {
  name   = "${var.project_name}-redis-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6380
    protocol        = "tcp"
    security_groups = [aws_security_group.app_server.id]
  }
  tags = { Name = "${var.project_name}-redis-sg" }
}

# ---------------------------------------------------------------------------
# ALB + Listener HTTPS
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags = { Name = "${var.project_name}-alb" }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_target_group" "api" {
  name     = "${var.project_name}-api-tg"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    path                = "/health/"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    matcher             = "200"
  }
}

# ---------------------------------------------------------------------------
# IAM Role para las instancias
# ---------------------------------------------------------------------------

data "aws_iam_instance_profile" "lab_profile" {
  name = "LabInstanceProfile"
}

# ---------------------------------------------------------------------------
# Launch Templates y Auto Scaling Groups
# ---------------------------------------------------------------------------

# 1. API Server
resource "aws_launch_template" "api_server" {
  name_prefix   = "${var.project_name}-api-"
  image_id      = var.ami_id
  instance_type = "t3.small"
  key_name      = var.key_pair_name

  iam_instance_profile { arn = data.aws_iam_instance_profile.lab_profile.arn }
  vpc_security_group_ids = [aws_security_group.app_server.id]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs { 
      volume_size = 8
      encrypted   = true 
    }
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e

    # Guardar logs del arranque
    exec > >(tee /var/log/user-data.log)
    exec 2>&1

    echo "Iniciando bootstrap..."

    # ----------------------------------------------------------
    # Instalar dependencias
    # ----------------------------------------------------------
    apt-get update -y
    apt-get install -y git python3-pip python3-venv

    # ----------------------------------------------------------
    # Clonar proyecto
    # ----------------------------------------------------------
    mkdir -p /opt/sprint3
    if [ ! -d "/opt/sprint3/.git" ]; then
        git clone https://github.com/Teban1101/sprint3 /opt/sprint3
    fi
    cd /opt/sprint3

    # ----------------------------------------------------------
    # Crear entorno virtual
    # ----------------------------------------------------------
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install --no-cache-dir -r requirements.txt
    ./venv/bin/pip install gunicorn

    # ----------------------------------------------------------
    # Variables de entorno (Usando /etc/environment para persistencia)
    # ----------------------------------------------------------
    cat <<EOV >/etc/environment
DJANGO_SETTINGS_MODULE=asr1_disponibilidad.app.settings
DB_HOST=${aws_db_instance.postgres.address}
DB_NAME=finops
DB_USER=finops_user
DB_PASSWORD=${var.db_password}
REDIS_URL=redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0
HMAC_SECRET_KEY=${var.hmac_secret_key}
EOV

    set -a
    . /etc/environment
    set +a

    # ----------------------------------------------------------
    # Ejecutar migraciones
    # ----------------------------------------------------------
    cd /opt/sprint3/asr1_disponibilidad
    ../venv/bin/python manage.py migrate --noinput

    # ----------------------------------------------------------
    # Ejecutar Gunicorn
    # ----------------------------------------------------------
    nohup ../venv/bin/gunicorn \
    asr1_disponibilidad.app.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    > /var/log/gunicorn.log 2>&1 &

    echo "Bootstrap completado"
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = { Name = "${var.project_name}-api-server" }
  }
}

resource "aws_autoscaling_group" "api_server" {
  name                = "${var.project_name}-api-asg"
  desired_capacity    = 2
  min_size            = 2
  max_size            = 6
  vpc_zone_identifier = aws_subnet.private[*].id
  target_group_arns   = [aws_lb_target_group.api.arn]
  health_check_type   = "ELB"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.api_server.id
    version = "$Latest"
  }
}

# 2. FinOps Server (AÑADIDO)
resource "aws_launch_template" "finops_server" {
  name_prefix   = "${var.project_name}-finops-"
  image_id      = var.ami_id
  instance_type = "t3.small"
  key_name      = var.key_pair_name

  iam_instance_profile { arn = data.aws_iam_instance_profile.lab_profile.arn }
  vpc_security_group_ids = [aws_security_group.app_server.id]

  user_data = base64encode(<<-EOF
    #!/bin/bash
    # (Añadir script de inicio para FinOps aquí)
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = { Name = "${var.project_name}-finops-server" }
  }
}

resource "aws_autoscaling_group" "finops_server" {
  name                = "${var.project_name}-finops-asg"
  desired_capacity    = 2
  min_size            = 2
  max_size            = 4
  vpc_zone_identifier = aws_subnet.private[*].id

  launch_template {
    id      = aws_launch_template.finops_server.id
    version = "$Latest"
  }
}

# 3. CRON Worker (AÑADIDO)
resource "aws_launch_template" "cron_worker" {
  name_prefix   = "${var.project_name}-cron-"
  image_id      = var.ami_id
  instance_type = "t3.small"
  key_name      = var.key_pair_name

  iam_instance_profile { arn = data.aws_iam_instance_profile.lab_profile.arn }
  vpc_security_group_ids = [aws_security_group.app_server.id]

  user_data = base64encode(<<-EOF
    #!/bin/bash
    # (Añadir script de inicio para CRON / Celery workers aquí)
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = { Name = "${var.project_name}-cron-worker" }
  }
}

resource "aws_autoscaling_group" "cron_worker" {
  name                = "${var.project_name}-cron-asg"
  desired_capacity    = 1
  min_size            = 1
  max_size            = 3
  vpc_zone_identifier = aws_subnet.private[*].id

  launch_template {
    id      = aws_launch_template.cron_worker.id
    version = "$Latest"
  }
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL Multi-AZ
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier             = "${var.project_name}-db"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t3.small"
  allocated_storage      = 20
  storage_encrypted      = true

  db_name  = "finops"
  username = "finops_user"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az               = true
  backup_retention_period = 7
  skip_final_snapshot    = true

  tags = { Name = "${var.project_name}-rds" }
}

# ---------------------------------------------------------------------------
# ElastiCache Redis
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-cache-subnet"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-redis"
  engine               = "redis"
  node_type            = "cache.t3.small"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]
  tags = { Name = "${var.project_name}-redis" }
}

# ---------------------------------------------------------------------------
# Secrets Manager
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "hmac_key" {
  name        = "finops/hmac-key"
  description = "Clave HMAC-SHA256 para verificación de integridad (ASR-2)"
}

resource "aws_secretsmanager_secret_version" "hmac_key" {
  secret_id     = aws_secretsmanager_secret.hmac_key.id
  secret_string = jsonencode({ hmac_key = var.hmac_secret_key })
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "DNS del Application Load Balancer — usar en los experimentos"
}

output "rds_endpoint" {
  value     = aws_db_instance.postgres.address
  sensitive = true
}

output "redis_endpoint" {
  value = aws_elasticache_cluster.redis.cache_nodes[0].address
}
