output "vpc_id" {
  value       = aws_vpc.this.id
  description = "VPC ID"
}

output "alb_dns_name" {
  value       = aws_lb.this.dns_name
  description = "ALB DNS — EasyObs entry point. /v1·/otlp·/healthz·/docs go to API pool, the rest to Web."
}

output "easyobs_url" {
  value       = "http://${aws_lb.this.dns_name}"
  description = "EasyObs UI URL (HTTP)"
}

output "easyobs_api_url" {
  value       = "http://${aws_lb.this.dns_name}/healthz"
  description = "EasyObs API healthcheck URL"
}

output "rds_endpoint" {
  value       = aws_db_instance.easyobs.address
  description = "RDS Postgres endpoint"
  sensitive   = true
}

output "rds_password" {
  value       = random_password.rds.result
  description = "RDS Postgres password (also in Terraform state)"
  sensitive   = true
}

output "jwt_secret" {
  value       = random_password.jwt_secret.result
  description = "EASYOBS_JWT_SECRET (also in Terraform state)"
  sensitive   = true
}

output "efs_id" {
  value       = aws_efs_file_system.blob.id
  description = "EFS file system ID for shared blob"
}

output "stage_bucket" {
  value       = aws_s3_bucket.stage.bucket
  description = "S3 stage bucket holding source/product archives"
}

output "api_leader_instance_id" {
  value       = aws_instance.api_leader.id
  description = "API leader EC2 (the only one with EASYOBS_ALARM_ENABLED=true)"
}

output "api_worker_asg_name" {
  value       = aws_autoscaling_group.api_worker.name
  description = "API worker ASG name. Edit desired_capacity to scale."
}

output "web_instance_id" {
  value       = aws_instance.web.id
  description = "Web EC2 instance ID"
}
