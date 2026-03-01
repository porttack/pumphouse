#!/bin/bash
# Generate self-signed SSL certificate for pumphouse web server

echo "Generating self-signed SSL certificate..."
echo ""

openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout key.pem \
    -out cert.pem \
    -days 365 \
    -subj "/C=US/ST=Oregon/L=Coast/O=Pumphouse/CN=pumphouse.local"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Certificate generated successfully!"
    echo "  - cert.pem (certificate)"
    echo "  - key.pem (private key)"
    echo ""
    echo "Valid for 365 days"
    echo ""
    echo "To start the web server:"
    echo "  python -m monitor.web"
    echo ""
    echo "Note: Your browser will show a security warning because this is"
    echo "      a self-signed certificate. This is normal - click 'Advanced'"
    echo "      and proceed to the site."
else
    echo ""
    echo "✗ Certificate generation failed"
    exit 1
fi
