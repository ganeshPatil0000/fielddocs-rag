# ----------------------------------------------------------------------------
# ECR: one image for both Lambdas. Immutable tags = every deploy is traceable
# to a git SHA and can be rolled back by redeploying the previous tag.
# ----------------------------------------------------------------------------
resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = !local.is_prod

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 15 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 15 }
      action       = { type = "expire" }
    }]
  })
}

# ----------------------------------------------------------------------------
# S3: raw documents. Private, encrypted, versioned, TLS-only.
# ----------------------------------------------------------------------------
resource "aws_s3_bucket" "docs" {
  bucket        = "${local.name}-docs-${local.account_id}"
  force_destroy = !local.is_prod
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "docs_tls_only" {
  bucket = aws_s3_bucket.docs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.docs.arn, "${aws_s3_bucket.docs.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.docs]
}

# Browsers upload straight to S3 with the presigned POST from the API.
resource "aws_s3_bucket_cors_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = local.cors_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
  }

  depends_on = [aws_sqs_queue_policy.ingest]
}

# ----------------------------------------------------------------------------
# SQS: buffers uploads, retries failures, parks poison messages in a DLQ.
# ----------------------------------------------------------------------------
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 1800 # >= 6x the ingestion Lambda timeout (300s)
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3Notifications"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.docs.arn }
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}

# ----------------------------------------------------------------------------
# DynamoDB: single table for registry, chat history, answer cache.
# ----------------------------------------------------------------------------
resource "aws_dynamodb_table" "app" {
  name                        = local.name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = local.is_prod

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

# ----------------------------------------------------------------------------
# SSM SecureString placeholders. Real values are set out-of-band by
# scripts/put_secrets.sh so they never appear in Terraform state or git.
# ----------------------------------------------------------------------------
resource "aws_ssm_parameter" "secret" {
  for_each = toset(["PINECONE_API_KEY", "LANGSMITH_API_KEY", "OPENAI_API_KEY"])

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}
