locals {
  name_prefix = "${var.project_name}-${var.environment}"
  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "easyobs"
  }

  source_dir  = abspath("${path.module}/${var.easyobs_source_dir}")
  product_dir = abspath("${path.module}/../../..") # docs/comparison/03.develop/easyobs/setup
}

# =============================================================================
# Secrets
# =============================================================================

resource "random_password" "rds" {
  length  = 24
  special = false
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

# =============================================================================
# Source archive (terraform → S3 → EC2 hosts)
# =============================================================================

data "archive_file" "easyobs_source" {
  type        = "zip"
  source_dir  = local.source_dir
  output_path = "${path.module}/.terraform-staging/easyobs-source.zip"

  # ** glob 으로 어떤 깊이에서도 빌드 산출물 / 가상환경 / .env 비밀 등을 제외.
  # archive provider 2.5+ 는 doublestar glob 을 지원한다.
  excludes = [
    "**/.venv",
    "**/node_modules",
    "**/.next",
    "**/__pycache__",
    "**/data",
    "**/*.pyc",
    "**/tfplan",
    "**/.terraform",
    "**/.terraform.lock.hcl",
    # 운영 비밀이 들어 있을 수 있는 .env 류는 EC2 zip 에 절대 넣지 않는다.
    "**/.env",
    "**/.env.*",
    "**/.gitignore",
    # setup/ 은 product archive 로 따로 패키징되므로 source 에는 넣지 않는다.
    "setup",
  ]
}

data "archive_file" "easyobs_product" {
  type        = "zip"
  source_dir  = local.product_dir
  output_path = "${path.module}/.terraform-staging/easyobs-product.zip"

  # terraform.tfstate 에는 RDS/JWT 시크릿이 들어가므로 product zip → EC2 → S3
  # 로 새는 일이 없도록 반드시 제외한다.
  excludes = [
    "**/.terraform",
    "**/.terraform-staging",
    "**/.terraform.lock.hcl",
    "**/terraform.tfstate",
    "**/terraform.tfstate.*",
    "**/tfplan",
  ]
}

resource "aws_s3_bucket" "stage" {
  bucket_prefix = "${local.name_prefix}-stage-"
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "stage" {
  bucket                  = aws_s3_bucket.stage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "stage" {
  bucket = aws_s3_bucket.stage.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "easyobs_source" {
  bucket = aws_s3_bucket.stage.id
  key    = "easyobs-source.zip"
  source = data.archive_file.easyobs_source.output_path
  etag   = data.archive_file.easyobs_source.output_md5
}

resource "aws_s3_object" "easyobs_product" {
  bucket = aws_s3_bucket.stage.id
  key    = "easyobs-product.zip"
  source = data.archive_file.easyobs_product.output_path
  etag   = data.archive_file.easyobs_product.output_md5
}

# =============================================================================
# Network: VPC + 2 public + 2 private + IGW + NAT
# =============================================================================

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-${var.ubuntu_codename}-*-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = merge(local.tags, {
    Name = "${local.name_prefix}-public-${count.index + 1}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = merge(local.tags, {
    Name = "${local.name_prefix}-private-${count.index + 1}"
    Tier = "private"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = "${local.name_prefix}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name_prefix}-nat-eip" })
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.tags, { Name = "${local.name_prefix}-nat" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = merge(local.tags, { Name = "${local.name_prefix}-private-rt" })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# =============================================================================
# Security groups
# =============================================================================

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB ingress on port 80"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "all"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.allow_alb_cidr]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb-sg" })
}

resource "aws_security_group" "app" {
  name        = "${local.name_prefix}-app-sg"
  description = "EasyObs API + Web hosts in private subnets"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "all"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "API from ALB"
    from_port       = 8787
    to_port         = 8787
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Web from ALB"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  dynamic "ingress" {
    for_each = var.key_name != "" ? [1] : []
    content {
      description = "SSH"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [var.allow_ssh_cidr]
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-app-sg" })
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS Postgres — only from app SG"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "Postgres from app"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-rds-sg" })
}

resource "aws_security_group" "efs" {
  name        = "${local.name_prefix}-efs-sg"
  description = "EFS NFS — only from app SG"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "NFS from app"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-efs-sg" })
}

# =============================================================================
# IAM (shared by leader / worker / web)
# =============================================================================

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name_prefix}-app-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "stage_read" {
  statement {
    sid       = "ReadStage"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.stage.arn}/*"]
  }
  statement {
    sid       = "ListStage"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.stage.arn]
  }
}

resource "aws_iam_role_policy" "stage_read" {
  name   = "${local.name_prefix}-stage-read"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.stage_read.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.name_prefix}-app-profile"
  role = aws_iam_role.app.name
  tags = local.tags
}

# =============================================================================
# RDS (Postgres)
# =============================================================================

resource "aws_db_subnet_group" "this" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = merge(local.tags, { Name = "${local.name_prefix}-db-subnets" })
}

resource "aws_db_instance" "easyobs" {
  identifier              = "${local.name_prefix}-pg"
  engine                  = "postgres"
  engine_version          = var.rds_engine_version
  instance_class          = var.rds_instance_class
  allocated_storage       = var.rds_allocated_storage
  storage_type            = "gp3"
  storage_encrypted       = true
  username                = "easyobs"
  password                = random_password.rds.result
  db_name                 = "easyobs"
  port                    = 5432
  multi_az                = var.rds_multi_az
  publicly_accessible     = false
  db_subnet_group_name    = aws_db_subnet_group.this.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  skip_final_snapshot     = var.rds_skip_final_snapshot
  deletion_protection     = false
  backup_retention_period = 3
  apply_immediately       = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-pg" })
}

# =============================================================================
# EFS (shared blob store for API leader + workers)
# =============================================================================

resource "aws_efs_file_system" "blob" {
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  tags = merge(local.tags, { Name = "${local.name_prefix}-blob" })
}

resource "aws_efs_mount_target" "blob" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.blob.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# =============================================================================
# ALB
# =============================================================================

resource "aws_lb" "this" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  idle_timeout       = 120

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_lb_target_group" "api" {
  name     = "${local.name_prefix}-api-tg"
  port     = 8787
  protocol = "HTTP"
  vpc_id   = aws_vpc.this.id

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  deregistration_delay = 30

  tags = merge(local.tags, { Name = "${local.name_prefix}-api-tg" })
}

resource "aws_lb_target_group" "web" {
  name     = "${local.name_prefix}-web-tg"
  port     = 3000
  protocol = "HTTP"
  vpc_id   = aws_vpc.this.id

  health_check {
    path                = "/"
    matcher             = "200-399"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  deregistration_delay = 30

  tags = merge(local.tags, { Name = "${local.name_prefix}-web-tg" })
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "api_paths" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = [
        "/v1/*",
        "/otlp/*",
        "/healthz",
        "/docs",
        "/docs/*",
        "/openapi.json",
        "/redoc",
      ]
    }
  }
}

# =============================================================================
# Compute: Leader EC2 (1대 고정), Web EC2 (1대), Worker ASG
# =============================================================================

# bootstrap user_data 공통 입력값
locals {
  api_bootstrap_common = {
    aws_region            = var.aws_region
    stage_bucket          = aws_s3_bucket.stage.bucket
    source_object_key     = aws_s3_object.easyobs_source.key
    product_object_key    = aws_s3_object.easyobs_product.key
    easyobs_api_image_tag = var.easyobs_api_image_tag
    rds_endpoint          = aws_db_instance.easyobs.address
    rds_port              = aws_db_instance.easyobs.port
    rds_user              = aws_db_instance.easyobs.username
    rds_password          = random_password.rds.result
    rds_db                = aws_db_instance.easyobs.db_name
    jwt_secret            = random_password.jwt_secret.result
    efs_id                = aws_efs_file_system.blob.id
    seed_mock_data        = var.seed_mock_data
    alb_dns               = aws_lb.this.dns_name
  }
}

resource "aws_instance" "api_leader" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.private[0].id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  associate_public_ip_address = false
  key_name                    = var.key_name != "" ? var.key_name : null

  user_data = base64encode(templatefile("${path.module}/templates/bootstrap-api.sh.tpl", merge(local.api_bootstrap_common, {
    role          = "leader"
    alarm_enabled = "true"
  })))
  user_data_replace_on_change = true

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-api-leader", Role = "api-leader" })

  depends_on = [
    aws_efs_mount_target.blob,
    aws_db_instance.easyobs,
    aws_nat_gateway.this,
    aws_route_table_association.private,
  ]

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_lb_target_group_attachment" "api_leader" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.api_leader.id
  port             = 8787
}

resource "aws_launch_template" "api_worker" {
  name_prefix   = "${local.name_prefix}-api-worker-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type
  key_name      = var.key_name != "" ? var.key_name : null

  vpc_security_group_ids = [aws_security_group.app.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  user_data = base64encode(templatefile("${path.module}/templates/bootstrap-api.sh.tpl", merge(local.api_bootstrap_common, {
    role          = "worker"
    alarm_enabled = "false"
  })))

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${local.name_prefix}-api-worker", Role = "api-worker" })
  }

  update_default_version = true
}

resource "aws_autoscaling_group" "api_worker" {
  name                      = "${local.name_prefix}-api-worker-asg"
  vpc_zone_identifier       = aws_subnet.private[*].id
  min_size                  = var.api_worker_min_size
  desired_capacity          = var.api_worker_desired_capacity
  max_size                  = var.api_worker_max_size
  health_check_type         = "ELB"
  health_check_grace_period = 300
  target_group_arns         = [aws_lb_target_group.api.arn]

  launch_template {
    id      = aws_launch_template.api_worker.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Project"
    value               = var.project_name
    propagate_at_launch = true
  }
  tag {
    key                 = "Environment"
    value               = var.environment
    propagate_at_launch = true
  }
  tag {
    key                 = "Service"
    value               = "easyobs"
    propagate_at_launch = true
  }
  tag {
    key                 = "Role"
    value               = "api-worker"
    propagate_at_launch = true
  }

  depends_on = [
    aws_efs_mount_target.blob,
    aws_db_instance.easyobs,
    aws_nat_gateway.this,
    aws_route_table_association.private,
    aws_instance.api_leader,
  ]
}

resource "aws_instance" "web" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.private[0].id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  associate_public_ip_address = false
  key_name                    = var.key_name != "" ? var.key_name : null

  user_data = base64encode(templatefile("${path.module}/templates/bootstrap-web.sh.tpl", {
    aws_region            = var.aws_region
    stage_bucket          = aws_s3_bucket.stage.bucket
    source_object_key     = aws_s3_object.easyobs_source.key
    product_object_key    = aws_s3_object.easyobs_product.key
    easyobs_web_image_tag = var.easyobs_web_image_tag
    alb_dns               = aws_lb.this.dns_name
  }))
  user_data_replace_on_change = true

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-web", Role = "web" })

  depends_on = [
    aws_lb.this,
    aws_nat_gateway.this,
    aws_route_table_association.private,
  ]

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_lb_target_group_attachment" "web" {
  target_group_arn = aws_lb_target_group.web.arn
  target_id        = aws_instance.web.id
  port             = 3000
}
