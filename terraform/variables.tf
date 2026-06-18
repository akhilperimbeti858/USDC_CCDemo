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
  description = "Globally-unique S3 bucket name for input manifest, UI template, and output."
  type        = string
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
  description = "Entity label set presented to workers and passed to the pre-annotation Lambda."
  type        = list(string)
  default     = ["PERSON", "ORG", "LOC", "SANCTIONED_ENTITY"]
}

variable "consolidation_mode" {
  description = "Which post-annotation Lambda drives the job: 'single' or 'merge'. Both are deployed."
  type        = string
  default     = "single"

  validation {
    condition     = contains(["single", "merge"], var.consolidation_mode)
    error_message = "consolidation_mode must be either 'single' or 'merge'."
  }
}

variable "number_of_human_workers_per_object" {
  description = "Workers per dataset object. Use 1 with 'single', >1 with 'merge'."
  type        = number
  default     = 1
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
