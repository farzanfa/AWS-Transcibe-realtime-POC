#!/bin/bash

# Test script for the Go backend

# Set required environment variables for testing
export AWS_REGION=us-east-1
export S3_BUCKET=test-bucket
export AWS_ACCESS_KEY_ID=test-key
export AWS_SECRET_ACCESS_KEY=test-secret

echo "Testing Go Medical Transcription Backend"
echo "======================================="
echo ""
echo "Environment variables set:"
echo "AWS_REGION=$AWS_REGION"
echo "S3_BUCKET=$S3_BUCKET"
echo ""

# Run the backend in the background
echo "Starting backend..."
./medical-transcription-backend &
BACKEND_PID=$!

# Wait for backend to start
sleep 2

# Test health endpoint
echo "Testing /health endpoint..."
curl -s http://localhost:8000/health | jq .

echo ""
echo "Testing /config endpoint..."
curl -s http://localhost:8000/config | jq .

# Kill the backend
echo ""
echo "Stopping backend..."
kill $BACKEND_PID
wait $BACKEND_PID 2>/dev/null

echo "Test completed."