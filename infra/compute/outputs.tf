output "orchestrator_public_ip" {
  value = aws_instance.orchestrator.public_ip
}

output "subnet_id" {
  value = aws_subnet.public.id
}

output "orchestrator_security_group_id" {
  value = aws_security_group.orchestrator.id
}

output "ephemeral_security_group_id" {
  value = aws_security_group.ephemeral.id
}

output "orchestrator_instance_profile_name" {
  value = aws_iam_instance_profile.instance["orchestrator"].name
}

output "encode_instance_profile_name" {
  value = aws_iam_instance_profile.instance["encode"].name
}

output "judge_instance_profile_name" {
  value = aws_iam_instance_profile.instance["judge"].name
}

output "masters_instance_profile_name" {
  value = aws_iam_instance_profile.instance["masters"].name
}

output "key_pair_name" {
  value = aws_key_pair.orchestrator.key_name
}

output "orchestrator_ami_id" {
  value = var.orchestrator_ami_id
}

output "encode_amd64_ami_id" {
  value = var.encode_amd64_ami_id
}

output "encode_arm64_ami_id" {
  value = var.encode_arm64_ami_id
}

output "bucket_names" {
  value = local.bucket_names
}

output "orchestrator_infra" {
  description = "O infra.json que o user-data grava no boot; o `ignore_changes` não o regrava depois."
  value       = local.orchestrator_infra
}

output "ssh_private_key_parameter_name" {
  value = aws_ssm_parameter.orchestrator_ssh_key.name
}
