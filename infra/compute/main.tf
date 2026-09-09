locals {
  name_prefix = "transcoding-bench"
  region      = "us-east-1"

  bucket_arns = [
    data.terraform_remote_state.storage.outputs.campaign_bucket_arn,
    data.terraform_remote_state.storage.outputs.pilot_bucket_arn,
  ]
}

data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket  = var.state_bucket
    key     = "storage/terraform.tfstate"
    region  = local.region
    encrypt = true
  }
}
