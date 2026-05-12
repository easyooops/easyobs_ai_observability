locals {
  name_prefix = "${var.project_name}-${var.environment}"
  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "easyobs"
  }

  source_dir  = abspath("${path.module}/${var.easyobs_source_dir}")
  product_dir = abspath("${path.module}/../../..")
}

# ---- Secrets ---------------------------------------------------------------

resource "random_password" "postgres" {
  length  = 24
  special = false
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

# ---- Source archive (terraform → S3 → EC2) ---------------------------------

# EasyObs 소스 트리(.venv/__pycache__/node_modules 제외)를 zip 으로 패키징.
# EC2 bootstrap 이 IAM role 로 S3 에서 다운로드한 뒤 docker build 한다.
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

# 이미지/compose 산출물(Dockerfile, docker-compose.yml 등)도 같은 방식으로
# 패키징해 EC2 에 함께 배포한다.
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

# ---- Trace archive S3 bucket (hybrid storage cold tier) -------------------

resource "aws_s3_bucket" "trace_archive" {
  bucket_prefix = "${local.name_prefix}-traces-"
  force_destroy = false
  tags          = merge(local.tags, { Purpose = "trace-archive" })
}

resource "aws_s3_bucket_public_access_block" "trace_archive" {
  bucket                  = aws_s3_bucket.trace_archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "trace_archive" {
  bucket = aws_s3_bucket.trace_archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trace_archive" {
  bucket = aws_s3_bucket.trace_archive.id

  rule {
    id     = "glacier-after-90d"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

# ---- Network ---------------------------------------------------------------

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
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${local.name_prefix}-public" })
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
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "easyobs" {
  name        = "${local.name_prefix}-sg"
  description = "EasyObs API + Web + optional SSH"
  vpc_id      = aws_vpc.this.id

  egress {
    description      = "all"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    description = "EasyObs Web console"
    from_port   = var.easyobs_web_port
    to_port     = var.easyobs_web_port
    protocol    = "tcp"
    cidr_blocks = [var.allow_easyobs_cidr]
  }

  ingress {
    description = "EasyObs API + OTLP/HTTP ingest"
    from_port   = var.easyobs_api_port
    to_port     = var.easyobs_api_port
    protocol    = "tcp"
    cidr_blocks = [var.allow_easyobs_cidr]
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

  tags = merge(local.tags, { Name = "${local.name_prefix}-sg" })
}

# ---- IAM -------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "easyobs" {
  name               = "${local.name_prefix}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.easyobs.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "stage_read" {
  statement {
    sid     = "ReadStageBucket"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [
      "${aws_s3_bucket.stage.arn}/*",
    ]
  }
  statement {
    sid     = "ListStageBucket"
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      aws_s3_bucket.stage.arn,
    ]
  }
}

resource "aws_iam_role_policy" "stage_read" {
  name   = "${local.name_prefix}-stage-read"
  role   = aws_iam_role.easyobs.id
  policy = data.aws_iam_policy_document.stage_read.json
}

data "aws_iam_policy_document" "trace_archive_rw" {
  statement {
    sid    = "TraceArchiveReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      "${aws_s3_bucket.trace_archive.arn}/*",
    ]
  }
  statement {
    sid     = "TraceArchiveList"
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      aws_s3_bucket.trace_archive.arn,
    ]
  }
}

resource "aws_iam_role_policy" "trace_archive_rw" {
  name   = "${local.name_prefix}-trace-archive-rw"
  role   = aws_iam_role.easyobs.id
  policy = data.aws_iam_policy_document.trace_archive_rw.json
}

resource "aws_iam_instance_profile" "easyobs" {
  name = "${local.name_prefix}-ec2-profile"
  role = aws_iam_role.easyobs.name
  tags = local.tags
}

# ---- EC2 instance ----------------------------------------------------------

resource "aws_instance" "easyobs" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.easyobs.id]
  iam_instance_profile   = aws_iam_instance_profile.easyobs.name

  key_name = var.key_name != "" ? var.key_name : null

  user_data = base64encode(templatefile("${path.module}/templates/bootstrap.sh.tpl", {
    aws_region            = var.aws_region
    stage_bucket          = aws_s3_bucket.stage.bucket
    trace_archive_bucket  = aws_s3_bucket.trace_archive.bucket
    source_object_key     = aws_s3_object.easyobs_source.key
    product_object_key    = aws_s3_object.easyobs_product.key
    postgres_password     = random_password.postgres.result
    jwt_secret            = random_password.jwt_secret.result
    easyobs_api_port      = var.easyobs_api_port
    easyobs_web_port      = var.easyobs_web_port
    easyobs_api_image_tag = var.easyobs_api_image_tag
    easyobs_web_image_tag = var.easyobs_web_image_tag
    enable_data_volume    = var.enable_data_volume
    seed_mock_data        = var.seed_mock_data
    blob_hot_retention_days = var.blob_hot_retention_days
  }))

  user_data_replace_on_change = true

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  dynamic "ebs_block_device" {
    for_each = var.enable_data_volume ? [1] : []
    content {
      device_name           = "/dev/sdf"
      volume_size           = var.data_volume_size_gb
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

  tags = merge(local.tags, { Name = "${local.name_prefix}-host" })

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip" "easyobs" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name_prefix}-eip" })
}

resource "aws_eip_association" "easyobs" {
  instance_id   = aws_instance.easyobs.id
  allocation_id = aws_eip.easyobs.id
}
