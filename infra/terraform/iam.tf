# Least-privilege roles: the API can't read queues, the worker can't presign uploads.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

locals {
  bedrock_resources = concat(
    # Global / geo inference profiles (e.g. global.amazon.nova-2-lite) route to models in
    # other regions, so the foundation-model ARNs use a region wildcard.
    [for p in var.bedrock_model_patterns : "arn:${local.partition}:bedrock:*::foundation-model/${p}"],
    ["arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/*"],
  )
  ssm_resources = [
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}",
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}/*",
  ]
}

data "aws_iam_policy_document" "common" {
  statement {
    sid       = "Bedrock"
    actions   = ["bedrock:InvokeModel", "bedrock:Rerank", "bedrock:ApplyGuardrail"]
    resources = concat(local.bedrock_resources, ["arn:${local.partition}:bedrock:${var.region}:${local.account_id}:guardrail/*"])
  }

  statement {
    sid       = "SecretsFromSsm"
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = local.ssm_resources
  }

  statement {
    sid = "AppTable"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:DeleteItem", "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.app.arn]
  }
}

# --- API --------------------------------------------------------------------
resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "api" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]

  statement {
    sid = "RawDocuments"
    # PutObject is what the presigned POST is signed with; DeleteObject for DELETE /documents.
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.docs.arn}/raw/*"]
  }
}

resource "aws_iam_role_policy" "api" {
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- Ingestion worker -----------------------------------------------------------
resource "aws_iam_role" "ingest" {
  name               = "${local.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingest" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]

  statement {
    sid       = "ReadRawDocuments"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/raw/*"]
  }

  statement {
    sid = "ConsumeQueue"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.ingest.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# Logs + X-Ray for both roles.
resource "aws_iam_role_policy_attachment" "basic" {
  for_each = {
    api_logs    = [aws_iam_role.api.name, "service-role/AWSLambdaBasicExecutionRole"]
    api_xray    = [aws_iam_role.api.name, "AWSXRayDaemonWriteAccess"]
    ingest_logs = [aws_iam_role.ingest.name, "service-role/AWSLambdaBasicExecutionRole"]
    ingest_xray = [aws_iam_role.ingest.name, "AWSXRayDaemonWriteAccess"]
  }
  role       = each.value[0]
  policy_arn = "arn:${local.partition}:iam::aws:policy/${each.value[1]}"
}
