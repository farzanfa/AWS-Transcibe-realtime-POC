terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Generate random suffix for unique bucket name
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# S3 bucket for storing transcripts
resource "aws_s3_bucket" "transcripts" {
  bucket = "${var.bucket_prefix}-${random_id.bucket_suffix.hex}"

  tags = {
    Name        = "Speech Transcription Bucket"
    Environment = var.environment
    Project     = "speech-transcription-poc"
  }
}

# Block public access to the bucket
resource "aws_s3_bucket_public_access_block" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 bucket versioning
resource "aws_s3_bucket_versioning" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# S3 bucket server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# IAM user for the application
resource "aws_iam_user" "transcription_user" {
  name = "${var.iam_user_prefix}-${random_id.bucket_suffix.hex}"
  path = "/speech-transcription/"

  tags = {
    Name        = "Speech Transcription Service User"
    Environment = var.environment
    Project     = "speech-transcription-poc"
  }
}

# IAM policy for transcription and S3 access
resource "aws_iam_user_policy" "transcription_policy" {
  name = "TranscriptionAndS3Access"
  user = aws_iam_user.transcription_user.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "transcribe:StartStreamTranscription"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:AbortMultipartUpload"
        ]
        Resource = "${aws_s3_bucket.transcripts.arn}/*"
      }
    ]
  })
}

# Access keys for the IAM user
resource "aws_iam_access_key" "transcription_user" {
  user = aws_iam_user.transcription_user.name
}

# Data source to get current AWS region
data "aws_region" "current" {}

# Data source to get current AWS account ID
data "aws_caller_identity" "current" {}
