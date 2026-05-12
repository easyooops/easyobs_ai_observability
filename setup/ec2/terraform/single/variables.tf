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
  default     = "dev"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the new VPC"
  default     = "10.62.0.0/16"
}

variable "public_subnet_cidr" {
  type        = string
  description = "Public subnet CIDR (single AZ)"
  default     = "10.62.1.0/24"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type for the EasyObs single-node host"
  default     = "t3.large"
}

variable "ubuntu_codename" {
  type        = string
  description = "Ubuntu LTS codename for AMI filter (jammy=22.04, noble=24.04)"
  default     = "jammy"
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH (empty = no key pair, use SSM)"
  default     = ""
}

variable "allow_ssh_cidr" {
  type        = string
  description = "CIDR allowed for SSH (only used when key_name is set)"
  default     = "0.0.0.0/0"
}

variable "allow_easyobs_cidr" {
  type        = string
  description = "CIDR allowed for EasyObs HTTP endpoints"
  default     = "0.0.0.0/0"
}

variable "easyobs_api_port" {
  type        = number
  description = "Host port bound to the EasyObs API container"
  default     = 8787
}

variable "easyobs_web_port" {
  type        = number
  description = "Host port bound to the EasyObs Web console container"
  default     = 3000
}

variable "easyobs_api_image_tag" {
  type        = string
  description = "Docker image tag built locally on the EC2 host for the API"
  default     = "easyobs/api:0.2.0"
}

variable "easyobs_web_image_tag" {
  type        = string
  description = "Docker image tag built locally on the EC2 host for the Web"
  default     = "easyobs/web:0.2.0"
}

variable "root_volume_size_gb" {
  type        = number
  description = "Root EBS volume size in GB"
  default     = 60
}

variable "data_volume_size_gb" {
  type        = number
  description = "Extra EBS for blob/data at /mnt/data"
  default     = 100
}

variable "enable_data_volume" {
  type        = bool
  description = "If false, only use /mnt/data on root disk"
  default     = true
}

variable "easyobs_source_dir" {
  type        = string
  description = "EasyObs 소스 트리 경로(terraform single/ 디렉터리 기준 상대). 기본값은 setup/ 의 부모(=EasyObs 소스 루트)."
  default     = "../../../.."
}

variable "seed_mock_data" {
  type        = bool
  description = "true 면 첫 부팅에 데모 트레이스 시드 (운영 환경에서는 false 권장)"
  default     = false
}

variable "blob_hot_retention_days" {
  type        = number
  description = "Hot (local) blob store retention period in days. Data older than this is only in S3 archive."
  default     = 7
}
