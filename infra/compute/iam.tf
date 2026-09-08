data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  for_each = toset(["orchestrator", "encode", "judge", "masters"])

  name               = "${local.name_prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_instance_profile" "instance" {
  for_each = aws_iam_role.instance

  name = each.value.name
  role = each.value.name
}

resource "aws_iam_role_policy" "orchestrator" {
  name   = "orchestrator"
  role   = aws_iam_role.instance["orchestrator"].id
  policy = data.aws_iam_policy_document.orchestrator.json
}

resource "aws_iam_role_policy" "encode" {
  name   = "encode"
  role   = aws_iam_role.instance["encode"].id
  policy = data.aws_iam_policy_document.encode.json
}

resource "aws_iam_role_policy" "judge" {
  name   = "judge"
  role   = aws_iam_role.instance["judge"].id
  policy = data.aws_iam_policy_document.judge.json
}

resource "aws_iam_role_policy" "masters" {
  name   = "masters"
  role   = aws_iam_role.instance["masters"].id
  policy = data.aws_iam_policy_document.masters.json
}

data "aws_iam_policy_document" "orchestrator" {
  statement {
    sid       = "RunAndTerminateInstances"
    actions   = ["ec2:RunInstances", "ec2:TerminateInstances"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }

    # `RunInstances` é autorizado uma vez por recurso que cria — volume, ENI,
    # subnet, AMI — e `ec2:InstanceType` só existe no contexto da instância.
    # Trocar por `StringEquals` nega todo lançamento nos demais.
    condition {
      test     = "StringEqualsIfExists"
      variable = "ec2:InstanceType"
      values   = var.allowed_instance_types
    }
  }

  statement {
    sid       = "DescribeInstances"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  statement {
    sid       = "PassInstanceRoles"
    actions   = ["iam:PassRole"]
    resources = [for role in ["encode", "judge", "masters"] : aws_iam_role.instance[role].arn]
  }

  statement {
    sid       = "ReadWriteObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [for arn in local.bucket_arns : "${arn}/*"]
  }

  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket"]
    resources = local.bucket_arns
  }

  statement {
    sid       = "DeleteRunObjects"
    actions   = ["s3:DeleteObject"]
    resources = [for arn in local.bucket_arns : "${arn}/runs/*"]
  }

  statement {
    sid       = "ReadSshKeyParameter"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.orchestrator_ssh_key.arn]
  }

  statement {
    sid       = "DecryptSshKeyParameter"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
}

data "aws_iam_policy_document" "encode" {
  statement {
    sid       = "ReadMastersAndScenarios"
    actions   = ["s3:GetObject"]
    resources = flatten([for arn in local.bucket_arns : ["${arn}/masters/*", "${arn}/scenarios/*"]])
  }

  statement {
    sid       = "WriteRunsAndStatus"
    actions   = ["s3:PutObject"]
    resources = flatten([for arn in local.bucket_arns : ["${arn}/runs/*", "${arn}/status/*"]])
  }
}

data "aws_iam_policy_document" "judge" {
  statement {
    sid     = "ReadRunsMastersAndPlan"
    actions = ["s3:GetObject"]
    resources = flatten([
      for arn in local.bucket_arns : ["${arn}/runs/*", "${arn}/masters/*", "${arn}/quality/plan.json"]
    ])
  }

  statement {
    sid       = "WriteQualityResultsAndStatus"
    actions   = ["s3:PutObject"]
    resources = flatten([for arn in local.bucket_arns : ["${arn}/quality/results/*", "${arn}/status/*"]])
  }
}

data "aws_iam_policy_document" "masters" {
  statement {
    sid       = "WriteMasters"
    actions   = ["s3:PutObject"]
    resources = [for arn in local.bucket_arns : "${arn}/masters/*"]
  }

  statement {
    sid       = "ListMastersPrefix"
    actions   = ["s3:ListBucket"]
    resources = local.bucket_arns

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["masters/*"]
    }
  }
}
