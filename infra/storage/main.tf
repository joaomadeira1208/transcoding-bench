data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "experiment" {
  for_each = toset(["campaign", "pilot"])

  bucket = "${var.bucket_prefix}-${data.aws_caller_identity.current.account_id}-${each.key}"
}

resource "aws_s3_bucket_public_access_block" "experiment" {
  for_each = aws_s3_bucket.experiment

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "experiment" {
  for_each = aws_s3_bucket.experiment

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
