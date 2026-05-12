output "vpc_id" {
  value       = aws_vpc.this.id
  description = "Created VPC ID"
}

output "public_subnet_id" {
  value       = aws_subnet.public.id
  description = "Public subnet ID"
}

output "instance_id" {
  value       = aws_instance.easyobs.id
  description = "EasyObs EC2 instance ID"
}

output "public_ip" {
  value       = aws_eip.easyobs.public_ip
  description = "Elastic IP for EasyObs access"
}

output "easyobs_api_url" {
  value       = "http://${aws_eip.easyobs.public_ip}:${var.easyobs_api_port}"
  description = "EasyObs API base URL"
}

output "easyobs_web_url" {
  value       = "http://${aws_eip.easyobs.public_ip}:${var.easyobs_web_port}"
  description = "EasyObs Web console URL"
}

output "stage_bucket" {
  value       = aws_s3_bucket.stage.bucket
  description = "S3 bucket holding the source/product archives the EC2 host downloads on boot"
}

output "trace_archive_bucket" {
  value       = aws_s3_bucket.trace_archive.bucket
  description = "S3 bucket for trace Parquet archive (hybrid storage cold tier)"
}

output "postgres_password" {
  value       = random_password.postgres.result
  description = "Auto-generated Postgres password (also in Terraform state)"
  sensitive   = true
}

output "jwt_secret" {
  value       = random_password.jwt_secret.result
  description = "Auto-generated EASYOBS_JWT_SECRET (also in Terraform state)"
  sensitive   = true
}
