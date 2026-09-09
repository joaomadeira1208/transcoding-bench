locals {
  repository     = "joaomadeira1208/transcoding-bench"
  default_branch = "master"

  orchestrator_work_dir = "/home/ubuntu/work"

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

# O `Accept` pede o media type que devolve os 40 caracteres do SHA crus, sem
# JSON a decodificar.
data "http" "default_branch_head" {
  url = "https://api.github.com/repos/${local.repository}/commits/${local.default_branch}"

  request_headers = {
    Accept = "application/vnd.github.sha"
  }
}

resource "aws_instance" "orchestrator" {
  ami                    = var.orchestrator_ami_id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.orchestrator.id]
  iam_instance_profile   = aws_iam_instance_profile.instance["orchestrator"].name
  key_name               = aws_key_pair.orchestrator.key_name

  root_block_device {
    volume_size = 16
    volume_type = "gp3"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = templatefile("${path.module}/../../orchestrator/user-data.sh", {
    repo_url  = "https://github.com/${local.repository}.git"
    commit    = chomp(data.http.default_branch_head.response_body)
    role      = "orchestrator"
    role_args = join(" ", ["--work-dir", local.orchestrator_work_dir, "--infra", "'${local.orchestrator_infra}'"])
  })

  tags = {
    Name = "${local.name_prefix}-orchestrator"
    role = "orchestrator"
  }

  # A dependência é do bootstrap, não do lançamento: o instance profile já basta
  # para a instância subir, e sem isto ela pode chegar ao `ssm get-parameter`
  # antes de a policy existir — o primeiro `apply` termina com a instância de pé
  # e o cloud-init em erro.
  depends_on = [aws_iam_role_policy.orchestrator]

  # Sem isto, o primeiro `apply` depois de um push no master recria a instância, e
  # no meio da campanha o `tmux` do Orquestrador morre com ela.
  lifecycle {
    ignore_changes = [user_data]
  }
}
