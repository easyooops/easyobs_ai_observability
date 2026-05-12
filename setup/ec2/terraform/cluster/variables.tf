variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "ap-northeast-2"
}

variable "project_name" {
  type        = string
  description = "Prefix for resource names"
  default     = "easyobs"
}

variable "environment" {
  type        = string
  description = "Environment label (e.g. dev, prod)"
  default     = "prod"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the new VPC"
  default     = "10.72.0.0/16"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "Two public subnet CIDRs (for ALB) — must be in different AZs"
  default     = ["10.72.1.0/24", "10.72.2.0/24"]
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "Two private subnet CIDRs (for EC2 + RDS + EFS) — must be in different AZs"
  default     = ["10.72.11.0/24", "10.72.12.0/24"]
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type for API leader / API workers / Web"
  default     = "t3.medium"
}

variable "ubuntu_codename" {
  type        = string
  description = "Ubuntu LTS codename for AMI filter"
  default     = "jammy"
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH (empty = SSM only)"
  default     = ""
}

variable "allow_ssh_cidr" {
  type        = string
  description = "CIDR allowed for SSH (only used when key_name is set)"
  default     = "0.0.0.0/0"
}

variable "allow_alb_cidr" {
  type        = string
  description = "CIDR allowed to reach the public ALB"
  default     = "0.0.0.0/0"
}

variable "easyobs_api_image_tag" {
  type        = string
  description = "Docker image tag for the API (built locally on each host)"
  default     = "easyobs/api:0.2.0"
}

variable "easyobs_web_image_tag" {
  type        = string
  description = "Docker image tag for the Web (built locally on the web host)"
  default     = "easyobs/web:0.2.0"
}

variable "api_worker_min_size" {
  type        = number
  description = "Minimum number of API worker EC2 in the ASG"
  default     = 1
}

variable "api_worker_desired_capacity" {
  type        = number
  description = "Desired number of API worker EC2 in the ASG"
  default     = 2
}

variable "api_worker_max_size" {
  type        = number
  description = "Maximum number of API worker EC2 in the ASG"
  default     = 6
}

variable "rds_instance_class" {
  type        = string
  description = "RDS Postgres instance class"
  default     = "db.t3.medium"
}

variable "rds_allocated_storage" {
  type        = number
  description = "RDS Postgres allocated storage (GB)"
  default     = 50
}

variable "rds_engine_version" {
  type        = string
  description = "RDS Postgres engine version"
  default     = "16.4"
}

variable "rds_multi_az" {
  type        = bool
  description = "Enable Multi-AZ for RDS"
  default     = false
}

variable "rds_skip_final_snapshot" {
  type        = bool
  description = "Skip the final snapshot on destroy (dev only)"
  default     = true
}

variable "easyobs_source_dir" {
  type        = string
  description = "EasyObs 소스 트리 경로 (terraform cluster/ 디렉터리 기준 상대). 기본값은 setup/ 의 부모(=EasyObs 소스 루트)."
  default     = "../../../.."
}

variable "seed_mock_data" {
  type        = bool
  description = "true 면 첫 부팅에 데모 트레이스 시드 (운영 환경에서는 false 권장)"
  default     = false
}

variable "blob_hot_retention_days" {
  type        = number
  description = "Hot (local/EFS) blob store retention period in days. Data older than this is only in S3 archive."
  default     = 7
}
