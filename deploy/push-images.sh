#!/usr/bin/env bash
# Сборка образов Affectra и отправка в реестр, откуда их берут узлы Swarm.
#
#   REGISTRY=192.168.1.10:5000 TAG=v1 deploy/push-images.sh
#
# Реестр в локальной сети без TLS: docker run -d -p 5000:5000 --restart=always --name registry registry:2
# и "insecure-registries" в настройках Docker каждого узла.
set -euo pipefail

REGISTRY=${REGISTRY:-127.0.0.1:5000}
TAG=${TAG:-latest}
cd "$(dirname "$0")/.."

docker compose build consumer postgres frontend

docker tag affectra-python:latest         "$REGISTRY/affectra-python:$TAG"
docker tag affectra-postgres:latest       "$REGISTRY/affectra-postgres:$TAG"
docker tag affectra-frontend:latest       "$REGISTRY/affectra-frontend:$TAG"

if [ "${PUSH:-1}" = "1" ]; then
    for image in affectra-python affectra-postgres affectra-frontend; do
        docker push "$REGISTRY/$image:$TAG"
    done
fi
