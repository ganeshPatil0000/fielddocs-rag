variable "project" {
  type    = string
  default = "fielddocs"
}

variable "environment" {
  description = "dev | staging | prod"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  description = "Your AWS project's Region. (Pinecone's free Starter plan runs in AWS us-east-1; that's fine, it's reached over HTTPS.)"
  type        = string
  default     = "ap-southeast-2"
}

variable "image_tag" {
  description = "Container image tag to deploy (use the git SHA; the ECR repo is immutable)."
  type        = string
}

variable "lambda_architecture" {
  description = "x86_64 or arm64 (arm64 is ~20% cheaper but you must build the image for arm64)."
  type        = string
  default     = "x86_64"
}

# --- models -------------------------------------------------------------------
variable "llm_provider" {
  description = "bedrock, or openai for any OpenAI-compatible API (key in SSM OPENAI_API_KEY)."
  type        = string
  default     = "bedrock"
  validation {
    condition     = contains(["bedrock", "openai"], var.llm_provider)
    error_message = "llm_provider must be bedrock or openai."
  }
}

variable "openai_base_url" {
  description = "Base URL of the OpenAI-compatible API (only used when llm_provider = openai)."
  type        = string
  default     = ""
  validation {
    condition     = var.llm_provider != "openai" || startswith(var.openai_base_url, "https://")
    error_message = "llm_provider = openai needs openai_base_url, and it must use https://."
  }
}

variable "llm_model_id" {
  type    = string
  default = "global.amazon.nova-2-lite-v1:0"
}

variable "utility_model_id" {
  type    = string
  default = "amazon.nova-micro-v1:0"
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  description = "Must match the Pinecone index dimension (Titan V2 supports 256, 512, 1024)."
  type        = number
  default     = 1024
}

variable "bedrock_model_patterns" {
  description = "Foundation-model id patterns the Lambdas may invoke (least privilege)."
  type        = list(string)
  default     = ["amazon.nova-*", "amazon.titan-embed-text-v2*", "meta.llama*", "amazon.rerank*", "cohere.rerank*"]
}

# --- retrieval ----------------------------------------------------------------
variable "pinecone_index" {
  type    = string
  default = "fielddocs"
}

variable "pinecone_namespace" {
  type    = string
  default = "default"
}

variable "enable_hybrid_search" {
  description = "Semantic + keyword search. Needs the sparse index (scripts/create_pinecone_index.py --sparse)."
  type        = bool
  default     = true
}

variable "pinecone_sparse_index" {
  description = "Sparse Pinecone index (pinecone-sparse-english-v0) used for keyword search."
  type        = string
  default     = "fielddocs-sparse"
}

variable "min_relevance_score" {
  type    = number
  default = 0.35
}

variable "enable_grounding_check" {
  description = "Second (cheap) LLM call that verifies each answer against its sources."
  type        = bool
  default     = false
}

# --- observability ------------------------------------------------------------
variable "langsmith_tracing" {
  description = "Send traces to LangSmith. Turn on after storing LANGSMITH_API_KEY with scripts/put_secrets.sh."
  type        = bool
  default     = false
}

variable "langsmith_project" {
  type    = string
  default = "fielddocs-rag"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "alarm_email" {
  description = "Email for CloudWatch alarms and budget alerts (confirm the SNS subscription email)."
  type        = string
  default     = ""
}

variable "hourly_llm_cost_alarm_usd" {
  description = "Alarm if estimated Bedrock spend in one hour exceeds this."
  type        = number
  default     = 1
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

# --- api ----------------------------------------------------------------------
variable "cors_origins" {
  description = "Browser origins allowed to call the API and upload documents. Empty = the CloudFront web app (plus http://localhost:5173 outside prod)."
  type        = list(string)
  default     = []
}

variable "api_throttle_rate" {
  description = "Steady-state requests/second across the API."
  type        = number
  default     = 10
}

variable "api_throttle_burst" {
  type    = number
  default = 20
}

variable "api_reserved_concurrency" {
  description = "-1 = no reservation. New AWS accounts have a 10-concurrency limit, so reserving fails there."
  type        = number
  default     = -1
}

# --- ci/cd --------------------------------------------------------------------
variable "github_repository" {
  description = "owner/repo allowed to deploy via GitHub Actions OIDC. Empty = don't create the role."
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Set false if your account already has the GitHub OIDC provider."
  type        = bool
  default     = true
}
