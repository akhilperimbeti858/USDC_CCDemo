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
