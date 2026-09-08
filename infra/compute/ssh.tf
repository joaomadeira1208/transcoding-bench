resource "tls_private_key" "orchestrator" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "orchestrator" {
  key_name   = local.name_prefix
  public_key = tls_private_key.orchestrator.public_key_openssh
}

resource "aws_ssm_parameter" "orchestrator_ssh_key" {
  name  = "/${local.name_prefix}/orchestrator/ssh-private-key"
  type  = "SecureString"
  value = tls_private_key.orchestrator.private_key_openssh
}

# A chave gerenciada do SSM só nasce no primeiro SecureString da conta: sem o
# `depends_on`, o primeiro `apply` lê um alias ainda sem alvo e a policy do
# Orquestrador sai com um `kms:Decrypt` sobre resource vazio.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"

  depends_on = [aws_ssm_parameter.orchestrator_ssh_key]
}
