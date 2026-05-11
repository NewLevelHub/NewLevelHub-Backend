#!/bin/bash

# New Level Hub Backend - Initialization Script
# This script helps set up the development environment

set -e

echo "New Level Hub Backend - Initialization"
echo "=========================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo ".env file created. Please update it with your settings."
else
    echo ".env file already exists."
fi

# Tear down any stale containers (including orphans from previous runs)
# This prevents "container name already in use" conflicts on re-runs.
echo ""
echo "Removing any stale containers..."
docker compose -f docker-compose.local.yml down --remove-orphans

# Start Docker containers and wait until all healthchecks pass
echo ""
echo "Starting Docker containers..."
docker compose -f docker-compose.local.yml up -d --build --wait

# Create MinIO bucket (one-shot setup container)
echo ""
echo "Creating MinIO bucket..."
docker compose -f docker-compose.local.yml run --rm createbuckets

# Run migrations (apply existing migration files only)
echo ""
echo "Running database migrations..."
docker compose -f docker-compose.local.yml exec -T backend python manage.py migrate

# Create superuser (optional)
echo ""
read -p "Do you want to create a superuser? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker compose -f docker-compose.local.yml exec backend python manage.py createsuperuser
fi

echo ""
echo "Setup complete!"
echo ""
echo "Services available at:"
echo "   - Backend API:    http://localhost:8000/api/v1/"
echo "   - Swagger Docs:   http://localhost:8000/api/docs/"
echo "   - Admin Panel:    http://localhost:8000/admin/"
echo "   - Health Check:   http://localhost:8000/api/v1/health/"
echo "   - MinIO Console:  http://localhost:9001  (minioadmin / minioadmin)"
echo ""
echo "Useful commands:"
echo "   - View logs:      docker compose -f docker-compose.local.yml logs -f"
echo "   - Stop services:  docker compose -f docker-compose.local.yml down"
echo "   - Restart:        docker compose -f docker-compose.local.yml restart"
echo ""
echo "Happy coding!"
