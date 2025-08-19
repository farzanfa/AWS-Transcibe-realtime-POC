FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy client files
COPY client.html .
COPY client.py .

# Expose HTTP server port
EXPOSE 8080

# Run the client in web server mode
CMD ["python3", "client.py", "--mode", "server", "--port", "8080"]
