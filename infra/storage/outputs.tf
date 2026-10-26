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

output "topologia_bucket_name" {
  value = aws_s3_bucket.experiment["topologia"].bucket
}

output "topologia_bucket_arn" {
  value = aws_s3_bucket.experiment["topologia"].arn
}

output "repetibilidade_bucket_name" {
  value = aws_s3_bucket.experiment["repetibilidade"].bucket
}

output "repetibilidade_bucket_arn" {
  value = aws_s3_bucket.experiment["repetibilidade"].arn
}

output "contadores_bucket_name" {
  value = aws_s3_bucket.experiment["contadores"].bucket
}

output "contadores_bucket_arn" {
  value = aws_s3_bucket.experiment["contadores"].arn
}
