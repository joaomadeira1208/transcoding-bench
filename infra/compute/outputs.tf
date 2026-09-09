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

output "campaign_bucket_name" {
  value = data.terraform_remote_state.storage.outputs.campaign_bucket_name
}

output "pilot_bucket_name" {
  value = data.terraform_remote_state.storage.outputs.pilot_bucket_name
}

output "ssh_private_key_parameter_name" {
  value = aws_ssm_parameter.orchestrator_ssh_key.name
}
