locals {
  name_prefix = "transcoding-bench"
  region      = "us-east-1"

  ephemeral_roles = ["encode", "judge", "masters"]

  experiments = ["campaign", "pilot", "topologia", "repetibilidade", "contadores"]

  bucket_names = {
    for experiment in local.experiments :
    experiment => data.terraform_remote_state.storage.outputs["${experiment}_bucket_name"]
  }

  bucket_arns = [
    for experiment in local.experiments :
    data.terraform_remote_state.storage.outputs["${experiment}_bucket_arn"]
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
