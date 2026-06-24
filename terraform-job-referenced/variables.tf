variable "aws_region" {
  description = "AWS region for the labeling job. Must match the region the referenced Lambdas live in."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used as a prefix for the execution role and job name. Make it unique per concurrently-applied job stack to avoid IAM role name collisions."
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

# --- Referenced (pre-existing) annotation Lambdas ----------------------------
# This stack does NOT create these. They are shared across many labeling jobs and
# deployed once by a separate stack (e.g. the lambdas/ + IAM in ../terraform).
# Pass their ARNs in. Their NAMES must contain a "SageMaker" token, and they must
# already grant the sagemaker.amazonaws.com principal lambda:InvokeFunction via a
# resource-based policy (aws_lambda_permission) -- the creating stack does this.
variable "pre_annotation_lambda_arn" {
  description = "ARN of the EXISTING pre-annotation (PreHumanTask) Lambda to wire into the job."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:lambda:[a-z0-9-]+:[0-9]{12}:function:", var.pre_annotation_lambda_arn))
    error_message = "pre_annotation_lambda_arn must be a Lambda function ARN."
  }
}

variable "post_annotation_lambda_arn" {
  description = "ARN of the EXISTING annotation-consolidation (post-annotation) Lambda to wire into the job."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:lambda:[a-z0-9-]+:[0-9]{12}:function:", var.post_annotation_lambda_arn))
    error_message = "post_annotation_lambda_arn must be a Lambda function ARN."
  }
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
