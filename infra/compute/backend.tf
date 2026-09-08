terraform {
  backend "s3" {
    region       = "us-east-1"
    key          = "compute/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
