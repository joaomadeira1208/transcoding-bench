output "campaign_bucket_name" {
  description = "Nome do bucket da campanha."
  value       = aws_s3_bucket.experiment["campaign"].bucket
}

output "campaign_bucket_arn" {
  description = "ARN do bucket da campanha."
  value       = aws_s3_bucket.experiment["campaign"].arn
}

output "pilot_bucket_name" {
  description = "Nome do bucket do piloto."
  value       = aws_s3_bucket.experiment["pilot"].bucket
}

output "pilot_bucket_arn" {
  description = "ARN do bucket do piloto."
  value       = aws_s3_bucket.experiment["pilot"].arn
}
