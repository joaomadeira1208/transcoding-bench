variable "state_bucket" {
  description = "Bucket de state criado fora de banda; o mesmo nome que o `-backend-config` do init recebe."
  type        = string
}

variable "researcher_ssh_cidr" {
  description = "CIDR de onde a porta 22 do Orquestrador aceita conexão; o IP público do pesquisador em /32."
  type        = string

  validation {
    condition     = var.researcher_ssh_cidr != "0.0.0.0/0"
    error_message = "A porta 22 do Orquestrador não pode ficar aberta ao mundo (ADR-0015)."
  }
}

variable "budget_notification_email" {
  description = "Email que recebe os dois alertas do orçamento de $150 (ADR-0012)."
  type        = string
}

variable "allowed_instance_types" {
  description = "Tipos que o Orquestrador pode lançar; o teto de custo sob comprometimento (ADR-0016). O tipo do Juiz entra aqui quando a spec do Pass o fixar."
  type        = list(string)
  default     = ["c7g.xlarge", "c7i.xlarge", "c7a.xlarge", "t3.micro"]
}

variable "orchestrator_ami_id" {
  description = "Ubuntu 24.04 LTS amd64 do Orquestrador, resolvido do parâmetro público da Canonical em 2026-09-08."
  type        = string
  default     = "ami-025d99823a4caad37"
}

variable "encode_amd64_ami_id" {
  description = "Ubuntu 24.04 LTS amd64 das efêmeras c7i.xlarge e c7a.xlarge, resolvido do parâmetro público da Canonical em 2026-09-08."
  type        = string
  default     = "ami-025d99823a4caad37"
}

variable "encode_arm64_ami_id" {
  description = "Ubuntu 24.04 LTS arm64 da efêmera c7g.xlarge e da preparação dos Masters, resolvido do parâmetro público da Canonical em 2026-09-08."
  type        = string
  default     = "ami-0246d714afcc1d494"
}
