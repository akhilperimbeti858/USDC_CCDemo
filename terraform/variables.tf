variable "aws_region" {
  description = "AWS region for all resources and the labeling job."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used as a prefix for resource names."
  type        = string
  default     = "usdc-ner"
}

variable "s3_bucket_name" {
  description = "Name of the EXISTING S3 bucket that already holds the input manifest, the UI template, and that will receive output. Not created by this stack."
  type        = string
}

variable "manifest_s3_key" {
  description = "Key of the existing input manifest object in s3_bucket_name."
  type        = string
  default     = "input/input.manifest"
}

variable "ui_template_s3_key" {
  description = "Key of the existing Crowd-HTML UI template object in s3_bucket_name."
  type        = string
  default     = "templates/ner-template.liquid.html"
}

variable "output_s3_prefix" {
  description = "Prefix in s3_bucket_name where the labeling job writes its output."
  type        = string
  default     = "output/"
}

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

variable "entity_labels" {
  description = "Entity label set presented to workers and passed to the pre-annotation Lambda. OFAC categories from the upstream Comprehend recognizer."
  type        = list(string)
  default     = ["OFAC_ORG", "OFAC_POI", "FTO"]
}

# --- Optional: Comprehend output.tar.gz -> manifest converter Lambda ---------
# Setting source_docs_s3_base (non-empty) ENABLES the converter Lambda; empty
# leaves the core GT stack unchanged.
variable "source_docs_s3_base" {
  description = "S3 prefix (e.g. s3://bucket/docs/) of the ORIGINAL documents, used to build manifest source-ref. Non-empty enables the Comprehend-output -> manifest Lambda."
  type        = string
  default     = ""
}

variable "comprehend_output_bucket" {
  description = "Bucket where Comprehend writes output.tar.gz (read by the converter Lambda). Empty -> same as s3_bucket_name."
  type        = string
  default     = ""
}

variable "min_score" {
  description = "Optional Comprehend Score floor for the converter Lambda; entities below it are dropped. Empty -> keep all."
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
