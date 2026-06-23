locals {
  # Function names contain "SageMaker" so the AmazonSageMakerGroundTruthExecution
  # managed policy is permitted to invoke them.
  pre_function_name         = "${var.project_name}-SageMaker-pre-annotation"
  post_single_function_name = "${var.project_name}-SageMaker-post-single"
}

data "archive_file" "pre" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/pre_annotation"
  output_path = "${path.module}/build/pre_annotation.zip"
}

data "archive_file" "post_single" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/post_annotation_single"
  output_path = "${path.module}/build/post_annotation_single.zip"
}

resource "aws_lambda_function" "pre_annotation" {
  function_name    = local.pre_function_name
  role             = aws_iam_role.pre_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  filename         = data.archive_file.pre.output_path
  source_code_hash = data.archive_file.pre.output_base64sha256

  environment {
    variables = {
      ENTITY_LABELS = jsonencode(var.entity_labels)
    }
  }
}

resource "aws_lambda_function" "post_single" {
  function_name    = local.post_single_function_name
  role             = aws_iam_role.post_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  filename         = data.archive_file.post_single.output_path
  source_code_hash = data.archive_file.post_single.output_base64sha256
}

# Allow the SageMaker service principal to invoke the labeling functions.
resource "aws_lambda_permission" "gt_invoke_pre" {
  statement_id  = "AllowGroundTruthInvokePre"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_annotation.function_name
  principal     = "sagemaker.amazonaws.com"
}

resource "aws_lambda_permission" "gt_invoke_post_single" {
  statement_id  = "AllowGroundTruthInvokePostSingle"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.post_single.function_name
  principal     = "sagemaker.amazonaws.com"
}

# ---------------------------------------------------------------------------
# Optional: Comprehend output.tar.gz -> GT input manifest converter Lambda.
# Enabled only when var.source_docs_s3_base is set. It is NOT invoked by Ground
# Truth, so its name needs no SageMaker token. The S3/EventBridge trigger is
# attached out-of-band; this stack does not manage the bucket's notifications.
# ---------------------------------------------------------------------------
locals {
  comprehend_enabled       = var.source_docs_s3_base != ""
  comprehend_output_bucket = var.comprehend_output_bucket != "" ? var.comprehend_output_bucket : var.s3_bucket_name
}

data "archive_file" "comprehend_to_manifest" {
  count       = local.comprehend_enabled ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/comprehend_to_manifest"
  output_path = "${path.module}/build/comprehend_to_manifest.zip"
}

resource "aws_lambda_function" "comprehend_to_manifest" {
  count            = local.comprehend_enabled ? 1 : 0
  function_name    = "${var.project_name}-comprehend-to-manifest"
  role             = aws_iam_role.comprehend_lambda[0].arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  filename         = data.archive_file.comprehend_to_manifest[0].output_path
  source_code_hash = data.archive_file.comprehend_to_manifest[0].output_base64sha256

  environment {
    variables = {
      SOURCE_DOCS_S3_BASE = var.source_docs_s3_base
      MANIFEST_S3_BUCKET  = var.s3_bucket_name
      MANIFEST_S3_KEY     = var.manifest_s3_key
      ENTITY_LABELS       = jsonencode(var.entity_labels)
      MIN_SCORE           = var.min_score
    }
  }
}

# Permit S3 to invoke the converter IF you attach an ObjectCreated notification on
# the Comprehend output bucket (you create that notification yourself).
resource "aws_lambda_permission" "s3_invoke_comprehend" {
  count         = local.comprehend_enabled ? 1 : 0
  statement_id  = "AllowS3InvokeComprehendToManifest"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.comprehend_to_manifest[0].function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::${local.comprehend_output_bucket}"
}
