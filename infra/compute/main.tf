locals {
  name_prefix = "transcoding-bench"
  region      = "us-east-1"

  # Os papéis que nascem e morrem dentro de um subcomando. O Orquestrador está
  # fora, e é isso que o mantém interminável pela própria policy (ADR-0016).
  ephemeral_roles = ["encode", "judge", "masters"]

  repository     = "joaomadeira1208/transcoding-bench"
  default_branch = "master"

  orchestrator_work_dir = "/home/ubuntu/work"

  bucket_arns = [
    data.terraform_remote_state.storage.outputs.campaign_bucket_arn,
    data.terraform_remote_state.storage.outputs.pilot_bucket_arn,
  ]

  # O arquivo de infra que o bootstrap grava no work dir e todo subcomando lê por
  # `--infra`; a forma está no `orchestrator/README.md`.
  orchestrator_infra = jsonencode({
    subnet_id = aws_subnet.public.id
    security_groups = {
      orchestrator = aws_security_group.orchestrator.id
      ephemeral    = aws_security_group.ephemeral.id
    }
    instance_profiles = {
      for role, profile in aws_iam_instance_profile.instance : role => profile.name
    }
    key_pair_name = aws_key_pair.orchestrator.key_name
    amis = {
      orchestrator = var.orchestrator_ami_id
      encode_amd64 = var.encode_amd64_ami_id
      encode_arm64 = var.encode_arm64_ami_id
    }
    buckets = {
      campaign = data.terraform_remote_state.storage.outputs.campaign_bucket_name
      pilot    = data.terraform_remote_state.storage.outputs.pilot_bucket_name
    }
    ssh_private_key_parameter_name = aws_ssm_parameter.orchestrator_ssh_key.name
  })
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
