FROM postgres:16

COPY init.sql /docker-entrypoint-initdb.d/

RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

ENV POSTGRES_DB=emotions
ENV POSTGRES_USER=app_user
ENV POSTGRES_PASSWORD=basic_app_password
