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

# Managed policy grants invoke on Lambdas whose names contain a SageMaker token
# (our pre/post functions are named accordingly) plus the GT runtime permissions.
resource "aws_iam_role_policy_attachment" "gt_managed" {
  role       = aws_iam_role.ground_truth.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerGroundTruthExecution"
}

data "aws_iam_policy_document" "gt_inline" {
  statement {
    sid     = "S3Access"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      aws_s3_bucket.gt.arn,
      "${aws_s3_bucket.gt.arn}/*",
    ]
  }

  statement {
    sid     = "InvokeLabelingLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.pre_annotation.arn,
      aws_lambda_function.post_single.arn,
      aws_lambda_function.post_merge.arn,
    ]
  }
}

resource "aws_iam_role_policy" "gt_inline" {
  name   = "${var.project_name}-gt-inline"
  role   = aws_iam_role.ground_truth.id
  policy = data.aws_iam_policy_document.gt_inline.json
}

# ---------------------------------------------------------------------------
# Lambda execution roles
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

# Pre-annotation Lambda: logs + S3 read (for source-ref records).
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
    resources = ["${aws_s3_bucket.gt.arn}/*"]
  }
}

resource "aws_iam_role_policy" "pre_s3" {
  name   = "${var.project_name}-pre-s3"
  role   = aws_iam_role.pre_lambda.id
  policy = data.aws_iam_policy_document.pre_s3.json
}

# Post-annotation Lambdas: logs + S3 read (annotations) + S3 write (output).
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
    resources = [aws_s3_bucket.gt.arn, "${aws_s3_bucket.gt.arn}/*"]
  }
}

resource "aws_iam_role_policy" "post_s3" {
  name   = "${var.project_name}-post-s3"
  role   = aws_iam_role.post_lambda.id
  policy = data.aws_iam_policy_document.post_s3.json
}
