# Amazon Bedrock Guardrail: a managed safety layer applied to every model call.
# The app attaches it automatically when BEDROCK_GUARDRAIL_ID is set (see
# app/providers.py). IAM already allows bedrock:ApplyGuardrail on guardrail/*.

variable "enable_guardrail" {
  description = "Create a Bedrock Guardrail (prompt-attack + content filters)."
  type        = bool
  default     = true
}

resource "aws_bedrock_guardrail" "main" {
  count       = var.enable_guardrail ? 1 : 0
  name        = "${local.name}-guardrail"
  description = "FieldDocs RAG safety layer: blocks prompt-injection and egregious content."

  blocked_input_messaging   = "I can't help with that request."
  blocked_outputs_messaging = "I can't provide that response."

  content_policy_config {
    # Prompt-injection protection (input only): defence in depth alongside the
    # in-prompt rule to treat retrieved text as data, never as instructions.
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    # Content filters. VIOLENCE is filtered on INPUT only (to catch malicious
    # requests like "how to kill people"), never on OUTPUT, so legitimate
    # field-safety answers about hazards ("electric shock", "risk of death")
    # are never blocked. The rest stay LOW.
    filters_config {
      type            = "HATE"
      input_strength  = "LOW"
      output_strength = "LOW"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "LOW"
      output_strength = "LOW"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "LOW"
      output_strength = "LOW"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "NONE"
    }
  }
}

output "guardrail_id" {
  description = "Set BEDROCK_GUARDRAIL_ID to this for local runs; the Lambda gets it from Terraform."
  value       = try(aws_bedrock_guardrail.main[0].guardrail_id, "")
}

output "guardrail_version" {
  value = var.enable_guardrail ? "DRAFT" : ""
}
