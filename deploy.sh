#!/bin/bash

# Production deployment script
# WARNING: Use with caution in production!

set -e

echo "🚀 New Level Hub Backend - Production Deployment"
echo "=================================================="

# Check if .env.production exists
if [ ! -f .env.production ]; then
    echo "❌ Error: .env.production file not found!"
    echo "Please create .env.production with your production settings."
    exit 1
fi

# Pull latest code
echo "📥 Pulling latest code..."
git pull origin main

# Build and start containers
echo "🐳 Building and starting production containers..."
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml up -d --build

# Wait for services to be ready
echo "⏳ Waiting for services to be ready..."
sleep 15

# Run migrations
echo "📦 Running database migrations..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

# Collect static files
echo "📦 Collecting static files..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Check health
echo "🏥 Checking service health..."
sleep 5
curl -f http://localhost/api/v1/health/ || echo "⚠️  Health check failed"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "🌐 Services available at:"
echo "   - Backend API:    http://your-domain.com/api/v1/"
echo "   - Swagger Docs:   http://your-domain.com/api/docs/"
echo "   - Admin Panel:    http://your-domain.com/admin/"
echo ""
echo "📝 Check logs with:"
echo "   docker-compose -f docker-compose.prod.yml logs -f"
