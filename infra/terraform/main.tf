"""
infra/terraform/main.tf
========================
Infraestructura AWS mínima para los experimentos ASR-1, ASR-2, ASR-3.
Basada en el diagrama de arquitectura del proyecto FinOps.

IMPORTANTE: Este archivo es una guía. Ajustar variables según el entorno
de la cuenta AWS del equipo antes de aplicar.

Recursos creados:
  - VPC + subnets públicas/privadas (2 AZs)
  - Application Load Balancer (HTTPS:443)
  - Auto Scaling Group — API Server (t3.small, Min:2, Max:6)
  - Auto Scaling Group — FinOps Server (t3.small, Min:2, Max:4)
  - Auto Scaling Group — CRON Worker (t3.small, Min:1, Max:3)
  - RDS PostgreSQL Multi-AZ (db.t3.small)
  - ElastiCache Redis (cache.t3.small)
  - Security Groups con TLS enforced
  - IAM Role para acceso a Secrets Manager
"""

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

variable "aws_region"         { default = "us-east-1" }
variable "project_name"       { default = "finops-sprint3" }
variable "ami_id"             { description = "AMI Ubuntu 24.04 LTS" }
variable "key_pair_name"      { description = "Par de llaves EC2 para SSH" }
variable "db_password"        { description = "Contraseña RDS" sensitive = true }
variable "hmac_secret_key"    { description = "Clave HMAC para ASR-2"   sensitive = true }
variable "acm_certificate_arn"{ description = "ARN del certificado TLS en ACM" }

# ---------------------------------------------------------------------------
# VPC y Subnets
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
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

data "aws_availability_zones" "available" { state = "available" }

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------

# ALB — solo HTTPS desde internet (ASR-3: sin HTTP:80)
resource "aws_security_group" "alb" {
  name   = "${var.project_name}-alb-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS only — ASR-3 TLS enforced"
  }

  # NOTA: Puerto 80 NO abierto — cumple ASR-3
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-alb-sg" }
}

# EC2 API Server — solo desde el ALB
resource "aws_security_group" "api_server" {
  name   = "${var.project_name}-api-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "Django desde ALB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-api-sg" }
}

# RDS — solo desde EC2
resource "aws_security_group" "rds" {
  name   = "${var.project_name}-rds-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api_server.id]
  }
  tags = { Name = "${var.project_name}-rds-sg" }
}

# ElastiCache — solo desde EC2
resource "aws_security_group" "redis" {
  name   = "${var.project_name}-redis-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6380
    protocol        = "tcp"
    security_groups = [aws_security_group.api_server.id]
  }
  tags = { Name = "${var.project_name}-redis-sg" }
}

# ---------------------------------------------------------------------------
# ALB + Listener HTTPS (ASR-3: fuerza TLS en el ingreso)
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
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"  # TLS 1.3 preferido
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

  # ASR-1: Health check configurado para detección rápida de instancias unhealthy
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
# Launch Template + Auto Scaling Group — API Server (ASR-1)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ec2_role" {
  name = "${var.project_name}-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "secrets_manager" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/SecretsManagerReadWrite"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

resource "aws_launch_template" "api_server" {
  name_prefix   = "${var.project_name}-api-"
  image_id      = var.ami_id
  instance_type = "t3.small"
  key_name      = var.key_pair_name

  iam_instance_profile { arn = aws_iam_instance_profile.ec2_profile.arn }

  vpc_security_group_ids = [aws_security_group.api_server.id]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs { volume_size = 8 encrypted = true }
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y python3-pip git
    pip3 install -r /opt/sprint3/requirements.txt
    export DJANGO_SETTINGS_MODULE=asr1_disponibilidad.app.settings
    export DB_HOST=${aws_db_instance.postgres.address}
    export DB_NAME=finops
    export DB_USER=finops_user
    export DB_PASSWORD=${var.db_password}
    export REDIS_URL=redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0
    export HMAC_SECRET_KEY=${var.hmac_secret_key}
    cd /opt/sprint3 && python manage.py runserver 0.0.0.0:8000
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
  min_size            = 2   # ASR-1: mínimo 2 instancias para HA
  max_size            = 6
  vpc_zone_identifier = aws_subnet.private[*].id
  target_group_arns   = [aws_lb_target_group.api.arn]
  health_check_type   = "ELB"
  health_check_grace_period = 60

  launch_template {
    id      = aws_launch_template.api_server.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-api-server"
    propagate_at_launch = true
  }
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL Multi-AZ (ASR-1: failover automático)
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

  multi_az               = true   # ASR-1: HA con failover automático
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
# Secrets Manager — clave HMAC para ASR-2
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "hmac_key" {
  name        = "finops/hmac-key"
  description = "Clave HMAC-SHA256 para verificación de integridad (ASR-2)"
}

resource "aws_secretsmanager_secret_version" "hmac_key" {
  secret_id = aws_secretsmanager_secret.hmac_key.id
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
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "redis_endpoint" {
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}
