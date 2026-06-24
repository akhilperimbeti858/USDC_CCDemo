locals {
  # The pre/post function names contain "SageMaker" so the
  # AmazonSageMakerGroundTruthExecution managed policy is permitted to invoke them.
  pre_function_name       = "${var.project_name}-SageMaker-pre-annotation"
  post_function_name      = "${var.project_name}-SageMaker-post-single"
  converter_function_name = "${var.project_name}-comprehend-to-manifest"
  launcher_function_name  = "${var.project_name}-launch-labeling-job"
}

# --- Packaging (zip each handler directory) ----------------------------------
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

data "archive_file" "converter" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/comprehend_to_manifest"
  output_path = "${path.module}/build/comprehend_to_manifest.zip"
}

data "archive_file" "launcher" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/launch_labeling_job"
  output_path = "${path.module}/build/launch_labeling_job.zip"
}

# --- Pre-annotation Lambda ---------------------------------------------------
resource "aws_lambda_function" "pre_annotation" {
  function_name    = local.pre_function_name
  role             = aws_iam_role.pre_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.pre_lambda_timeout_seconds
  filename         = data.archive_file.pre.output_path
  source_code_hash = data.archive_file.pre.output_base64sha256

  environment {
    variables = {
      ENTITY_LABELS = jsonencode(var.entity_labels)
    }
  }
}

# --- Post-annotation (single-worker consolidation) Lambda --------------------
resource "aws_lambda_function" "post_single" {
  function_name    = local.post_function_name
  role             = aws_iam_role.post_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.post_lambda_timeout_seconds
  filename         = data.archive_file.post_single.output_path
  source_code_hash = data.archive_file.post_single.output_base64sha256
}

# --- Comprehend output.tar.gz -> manifest converter Lambda -------------------
resource "aws_lambda_function" "converter" {
  function_name    = local.converter_function_name
  role             = aws_iam_role.converter_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.converter_lambda_timeout_seconds
  memory_size      = var.converter_lambda_memory_mb
  filename         = data.archive_file.converter.output_path
  source_code_hash = data.archive_file.converter.output_base64sha256

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

# --- Labeling-job launcher Lambda --------------------------------------------
resource "aws_lambda_function" "launcher" {
  function_name    = local.launcher_function_name
  role             = aws_iam_role.launcher_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.launcher_lambda_timeout_seconds
  filename         = data.archive_file.launcher.output_path
  source_code_hash = data.archive_file.launcher.output_base64sha256

  environment {
    variables = {
      ROLE_ARN                           = aws_iam_role.ground_truth.arn
      WORKTEAM_ARN                       = var.private_workteam_arn
      PRE_LAMBDA_ARN                     = aws_lambda_function.pre_annotation.arn
      POST_LAMBDA_ARN                    = aws_lambda_function.post_single.arn
      MANIFEST_S3_URI                    = local.manifest_s3_uri
      UI_TEMPLATE_S3_URI                 = local.ui_template_s3_uri
      OUTPUT_S3_URI                      = local.output_s3_uri
      LABEL_ATTRIBUTE_NAME               = var.label_attribute_name
      JOB_NAME_PREFIX                    = local.job_name_prefix
      TASK_TITLE                         = var.task_title
      TASK_DESCRIPTION                   = var.task_description
      TASK_KEYWORDS                      = jsonencode(var.task_keywords)
      TASK_TIME_LIMIT_SECONDS            = tostring(var.task_time_limit_seconds)
      TASK_AVAILABILITY_LIFETIME_SECONDS = tostring(var.task_availability_lifetime_seconds)
      MAX_CONCURRENT_TASK_COUNT          = tostring(var.max_concurrent_task_count)
      WORKERS_PER_OBJECT                 = tostring(var.workers_per_object)
    }
  }
}

# --- Invoke permissions ------------------------------------------------------
# Ground Truth (SageMaker) invokes the pre/post Lambdas.
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

# EventBridge invokes the converter (on output.tar.gz) and launcher (on manifest).
resource "aws_lambda_permission" "events_invoke_converter" {
  statement_id  = "AllowEventBridgeInvokeConverter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.converter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.comprehend_output.arn
}

resource "aws_lambda_permission" "events_invoke_launcher" {
  statement_id  = "AllowEventBridgeInvokeLauncher"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.launcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.manifest_created.arn
}
