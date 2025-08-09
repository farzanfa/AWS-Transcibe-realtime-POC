#!/usr/bin/env python3
"""
Simple HTTP server to serve the HTML client
"""
import http.server
import socketserver
import os

PORT = 8080

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"🌐 Simple HTML client server running at:")
        print(f"   👉 http://localhost:{PORT}/simple_client.html")
        print(f"")
        print(f"📋 Instructions:")
        print(f"   1. Open the URL above in your browser")
        print(f"   2. Click 'Start Recording' and allow microphone access")
        print(f"   3. Speak - you'll see real-time transcription")
        print(f"   4. Click 'Stop Recording' - transcript saves to S3")
        print(f"")
        print(f"Press Ctrl+C to stop the server")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n👋 Server stopped")
