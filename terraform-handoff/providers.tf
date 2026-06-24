provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = var.managed_by_tag
      Workflow  = var.workflow_tag
    }
  }
}
