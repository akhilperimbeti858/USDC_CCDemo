# =============================================================================
# All configuration lives here. The ONLY file an operator edits is
# terraform.tfvars (copy terraform.tfvars.example). Nothing is hardcoded in the
# other .tf files -- every tunable is a variable with a sensible default.
# =============================================================================

# --- Core / provider ---------------------------------------------------------
variable "aws_region" {
  description = "AWS region for all resources and the labeling job. Must match where the bucket and Comprehend output live."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used as a prefix for resource names."
  type        = string
  default     = "usdc-ner"
}

variable "managed_by_tag" {
  description = "Value for the ManagedBy default tag."
  type        = string
  default     = "terraform"
}

variable "workflow_tag" {
  description = "Value for the Workflow default tag."
  type        = string
  default     = "ground-truth-ner"
}

# --- Existing S3 bucket + asset keys -----------------------------------------
variable "s3_bucket_name" {
  description = "Name of the EXISTING S3 bucket. Holds the UI template, receives the Comprehend output.tar.gz, the generated manifest, and the job output. NOT created by this stack."
  type        = string
}

variable "manifest_s3_key" {
  description = "Key where the converter writes (and the job reads) the input manifest."
  type        = string
  default     = "input/input.manifest"
}

variable "ui_template_s3_key" {
  description = "Key of the Crowd-HTML UI template object in the bucket."
  type        = string
  default     = "templates/ner-template.liquid.html"
}

variable "output_s3_prefix" {
  description = "Prefix in the bucket where the labeling job writes its output."
  type        = string
  default     = "output/"
}

# --- UI template upload (PutObject into the existing bucket) ------------------
variable "upload_ui_template" {
  description = "If true, upload the local Crowd-HTML template to ui_template_s3_key. Set false if the operator manages the template manually."
  type        = bool
  default     = true
}

variable "ui_template_local_path" {
  description = "Path (relative to this stack) to the Crowd-HTML template to upload."
  type        = string
  default     = "../ui/ner-template.liquid.html"
}

# --- EventBridge wiring ------------------------------------------------------
variable "manage_bucket_eventbridge" {
  description = "If true, this stack enables EventBridge notifications on the bucket via the AUTHORITATIVE aws_s3_bucket_notification (it OVERWRITES other notification config). Prefer false + enabling EventBridge on the bucket out-of-band."
  type        = bool
  default     = false
}

variable "comprehend_output_suffix" {
  description = "Object-key suffix that identifies the Comprehend output tarball (triggers the converter)."
  type        = string
  default     = "output.tar.gz"
}

variable "comprehend_output_key_prefix" {
  description = "Optional key prefix to scope the Comprehend-output trigger (e.g. comprehend-output/). Empty matches the whole bucket by suffix."
  type        = string
  default     = ""
}

# --- Comprehend converter Lambda config --------------------------------------
variable "source_docs_s3_base" {
  description = "S3 prefix (e.g. s3://bucket/docs/) of the ORIGINAL documents, joined with each Comprehend File to build the manifest source-ref."
  type        = string
}

variable "comprehend_output_bucket" {
  description = "Bucket the converter reads output.tar.gz from. Empty -> same as s3_bucket_name."
  type        = string
  default     = ""
}

variable "entity_labels" {
  description = "Entity label set kept from Comprehend output and presented to workers."
  type        = list(string)
  default     = ["OFAC_ORG", "OFAC_POI", "FTO"]
}

variable "min_score" {
  description = "Optional Comprehend Score floor for the converter; entities below it are dropped. Empty -> keep all."
  type        = string
  default     = ""
}

# --- Ground Truth job config -------------------------------------------------
variable "private_workteam_arn" {
  description = "ARN of the existing private workteam that will label the data."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:sagemaker:[a-z0-9-]+:[0-9]{12}:workteam/", var.private_workteam_arn))
    error_message = "private_workteam_arn must be a SageMaker workteam ARN."
  }
}

variable "label_attribute_name" {
  description = "Label attribute name for the labeling job (the output manifest key)."
  type        = string
  default     = "ner-labels"
}

variable "job_name_prefix" {
  description = "Prefix for the auto-generated (unique) labeling job name. Empty -> project_name."
  type        = string
  default     = ""
}

variable "task_title" {
  description = "Title shown to workers in the labeling UI."
  type        = string
  default     = "Label named entities and confirm OFAC IDs"
}

variable "task_description" {
  description = "Description shown to workers in the labeling UI."
  type        = string
  default     = "Highlight each named entity, assign its type, and confirm an OFAC ID when prompted."
}

variable "task_keywords" {
  description = "Keywords attached to the labeling job."
  type        = list(string)
  default     = ["named entity recognition", "NER", "OFAC"]
}

variable "task_time_limit_seconds" {
  description = "Per-task time limit for a worker, in seconds."
  type        = number
  default     = 3600
}

variable "task_availability_lifetime_seconds" {
  description = "How long a task stays available to the workforce, in seconds."
  type        = number
  default     = 864000
}

variable "max_concurrent_task_count" {
  description = "Maximum number of tasks Ground Truth keeps in flight to the workteam."
  type        = number
  default     = 1000
}

variable "workers_per_object" {
  description = "Number of human workers per data object (this workflow is single-worker)."
  type        = number
  default     = 1
}

# --- Lambda runtime knobs ----------------------------------------------------
variable "lambda_runtime" {
  description = "Python runtime for all Lambdas."
  type        = string
  default     = "python3.12"
}

variable "pre_lambda_timeout_seconds" {
  description = "Timeout for the pre-annotation Lambda."
  type        = number
  default     = 60
}

variable "post_lambda_timeout_seconds" {
  description = "Timeout for the post-annotation (consolidation) Lambda."
  type        = number
  default     = 300
}

variable "converter_lambda_timeout_seconds" {
  description = "Timeout for the Comprehend-output -> manifest converter Lambda."
  type        = number
  default     = 300
}

variable "converter_lambda_memory_mb" {
  description = "Memory for the converter Lambda."
  type        = number
  default     = 512
}

variable "launcher_lambda_timeout_seconds" {
  description = "Timeout for the labeling-job launcher Lambda."
  type        = number
  default     = 60
}
