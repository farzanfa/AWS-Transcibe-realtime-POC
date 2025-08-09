FROM python:3.11-slim

WORKDIR /app

# Copy the HTML client files
COPY simple_client.html .
COPY serve_client.py .

# Expose HTTP server port
EXPOSE 8080

# Run simple HTTP server
CMD ["python3", "serve_client.py"]
