# O SHA do HEAD do branch padrão, que é o que a instância clona no boot: o
# `Accept` pede o media type que devolve os 40 caracteres crus, sem JSON a
# decodificar.
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

  # Depois do boot, quem troca a versão do código é o pesquisador, por `checkout`
  # no clone da instância (ADR-0021). Sem o `ignore_changes`, o primeiro `apply`
  # depois de um push no master recria a instância: no meio da campanha isso mata
  # o `tmux` do Orquestrador e deixa as efêmeras órfãs.
  lifecycle {
    ignore_changes = [user_data]
  }
}
