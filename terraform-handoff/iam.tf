data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Ground Truth labeling job execution role
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "gt_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ground_truth" {
  name               = "${var.project_name}-gt-execution-role"
  assume_role_policy = data.aws_iam_policy_document.gt_assume.json
}

resource "aws_iam_role_policy_attachment" "gt_managed" {
  role       = aws_iam_role.ground_truth.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerGroundTruthExecution"
}

data "aws_iam_policy_document" "gt_inline" {
  statement {
    sid     = "S3Access"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      data.aws_s3_bucket.gt.arn,
      "${data.aws_s3_bucket.gt.arn}/*",
    ]
  }

  statement {
    sid     = "InvokeLabelingLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.pre_annotation.arn,
      aws_lambda_function.post_single.arn,
    ]
  }
}

resource "aws_iam_role_policy" "gt_inline" {
  name   = "${var.project_name}-gt-inline"
  role   = aws_iam_role.ground_truth.id
  policy = data.aws_iam_policy_document.gt_inline.json
}

# ---------------------------------------------------------------------------
# Lambda execution roles (all trust the Lambda service)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Pre-annotation Lambda: logs + S3 read (for source-ref document text).
resource "aws_iam_role" "pre_lambda" {
  name               = "${var.project_name}-pre-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "pre_logs" {
  role       = aws_iam_role.pre_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "pre_s3" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.gt.arn}/*"]
  }
}

resource "aws_iam_role_policy" "pre_s3" {
  name   = "${var.project_name}-pre-s3"
  role   = aws_iam_role.pre_lambda.id
  policy = data.aws_iam_policy_document.pre_s3.json
}

# Post-annotation Lambda: logs + S3 read (annotations) + S3 write (output).
resource "aws_iam_role" "post_lambda" {
  name               = "${var.project_name}-post-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "post_logs" {
  role       = aws_iam_role.post_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "post_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [data.aws_s3_bucket.gt.arn, "${data.aws_s3_bucket.gt.arn}/*"]
  }
}

resource "aws_iam_role_policy" "post_s3" {
  name   = "${var.project_name}-post-s3"
  role   = aws_iam_role.post_lambda.id
  policy = data.aws_iam_policy_document.post_s3.json
}

# Converter Lambda: logs + S3 read (output.tar.gz) + S3 write (manifest).
resource "aws_iam_role" "converter_lambda" {
  name               = "${var.project_name}-converter-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "converter_logs" {
  role       = aws_iam_role.converter_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "converter_s3" {
  statement {
    sid       = "ReadComprehendOutput"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.comprehend_output_bucket}/*"]
  }
  statement {
    sid       = "WriteManifest"
    actions   = ["s3:PutObject"]
    resources = ["${data.aws_s3_bucket.gt.arn}/*"]
  }
}

resource "aws_iam_role_policy" "converter_s3" {
  name   = "${var.project_name}-converter-s3"
  role   = aws_iam_role.converter_lambda.id
  policy = data.aws_iam_policy_document.converter_s3.json
}

# Launcher Lambda: logs + create-labeling-job + pass the GT execution role.
resource "aws_iam_role" "launcher_lambda" {
  name               = "${var.project_name}-launcher-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "launcher_logs" {
  role       = aws_iam_role.launcher_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "launcher" {
  statement {
    sid       = "CreateLabelingJob"
    actions   = ["sagemaker:CreateLabelingJob"]
    resources = ["arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:labeling-job/*"]
  }
  # Ground Truth's create-labeling-job call passes the execution role; the
  # launcher must be allowed to PassRole exactly that role.
  statement {
    sid       = "PassGtRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ground_truth.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "launcher" {
  name   = "${var.project_name}-launcher"
  role   = aws_iam_role.launcher_lambda.id
  policy = data.aws_iam_policy_document.launcher.json
}
