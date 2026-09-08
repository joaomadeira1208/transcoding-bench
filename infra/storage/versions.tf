terraform {
  required_version = "1.15.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.63.0"
    }
  }

  backend "s3" {
    region       = "us-east-1"
    key          = "storage/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
