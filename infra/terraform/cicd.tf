# GitHub Actions deploys with short-lived OIDC credentials - no AWS access keys
# stored in GitHub. Only the main branch of var.github_repository can assume it.

locals {
  create_cicd = var.github_repository != ""
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = local.create_cicd && var.create_github_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.create_cicd && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_arn = try(
    aws_iam_openid_connect_provider.github[0].arn,
    data.aws_iam_openid_connect_provider.github[0].arn,
    "",
  )
}

data "aws_iam_policy_document" "github_assume" {
  count = local.create_cicd ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count              = local.create_cicd ? 1 : 0
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume[0].json
}

# Terraform needs broad permissions to manage the stack. PowerUserAccess covers
# everything except IAM; the inline policy grants IAM only on this project's roles.
resource "aws_iam_role_policy_attachment" "github_poweruser" {
  count      = local.create_cicd ? 1 : 0
  role       = aws_iam_role.github_deploy[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "github_iam" {
  count = local.create_cicd ? 1 : 0
  statement {
    actions = ["iam:*"]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/${var.project}-*",
      "arn:${local.partition}:iam::${local.account_id}:policy/${var.project}-*",
    ]
  }
  statement {
    actions   = ["iam:GetOpenIDConnectProvider", "iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_iam" {
  count  = local.create_cicd ? 1 : 0
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_iam[0].json
}
