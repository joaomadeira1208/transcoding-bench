output "campaign_bucket_name" {
  value = aws_s3_bucket.experiment["campaign"].bucket
}

output "campaign_bucket_arn" {
  value = aws_s3_bucket.experiment["campaign"].arn
}

output "pilot_bucket_name" {
  value = aws_s3_bucket.experiment["pilot"].bucket
}

output "pilot_bucket_arn" {
  value = aws_s3_bucket.experiment["pilot"].arn
}
