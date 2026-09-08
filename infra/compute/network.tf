data "aws_ec2_instance_type_offerings" "allowed" {
  for_each = toset(var.allowed_instance_types)

  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [each.key]
  }
}

locals {
  availability_zone = sort(setintersection([
    for offerings in data.aws_ec2_instance_type_offerings.allowed : toset(offerings.locations)
  ]...))[0]
}

resource "aws_vpc" "experiment" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = local.name_prefix
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.experiment.id
  cidr_block              = "10.0.0.0/20"
  availability_zone       = local.availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name_prefix}-public"
  }
}

resource "aws_internet_gateway" "experiment" {
  vpc_id = aws_vpc.experiment.id

  tags = {
    Name = local.name_prefix
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.experiment.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.experiment.id
  }

  tags = {
    Name = "${local.name_prefix}-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.experiment.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = {
    Name = "${local.name_prefix}-s3"
  }
}

resource "aws_security_group" "orchestrator" {
  name        = "${local.name_prefix}-orchestrator"
  description = "Orchestrator: SSH from the researcher CIDR."
  vpc_id      = aws_vpc.experiment.id
}

resource "aws_security_group" "ephemeral" {
  name        = "${local.name_prefix}-ephemeral"
  description = "Ephemeral instances: SSH from the orchestrator security group only."
  vpc_id      = aws_vpc.experiment.id
}

resource "aws_vpc_security_group_ingress_rule" "orchestrator_ssh" {
  security_group_id = aws_security_group.orchestrator.id
  cidr_ipv4         = var.researcher_ssh_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "ephemeral_ssh" {
  security_group_id            = aws_security_group.ephemeral.id
  referenced_security_group_id = aws_security_group.orchestrator.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
}

resource "aws_vpc_security_group_egress_rule" "orchestrator" {
  security_group_id = aws_security_group.orchestrator.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "ephemeral" {
  security_group_id = aws_security_group.ephemeral.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
