#!/bin/bash
sudo -E docker system prune -f
sudo -E docker compose -f docker-compose.yaml -p nerdpraat-hamp up --build -d