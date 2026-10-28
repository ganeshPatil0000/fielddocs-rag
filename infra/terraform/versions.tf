terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80, < 7.0"
    }
  }

  # Remote state (recommended once more than one person or CI deploys).
  # Create the bucket once, then uncomment and run `terraform init -migrate-state`.
  # S3-native locking (use_lockfile) needs Terraform >= 1.10; no DynamoDB table.
  #
  # backend "s3" {
  #   bucket       = "<your-tf-state-bucket>"
  #   key          = "fielddocs/dev/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name        = "${var.project}-${var.environment}"
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition
  ssm_prefix  = "/${var.project}/${var.environment}"
  is_prod     = var.environment == "prod"
  image_uri   = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  alarm_email = trimspace(var.alarm_email)

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
