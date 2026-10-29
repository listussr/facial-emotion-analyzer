#!/bin/sh
# Запуск SeaweedFS с S3 и тремя ключами доступа:
#   admin  — сервисы системы (S3_ACCESS_KEY / S3_SECRET_KEY), полный доступ;
#   reader — MCP-сервер агента (S3_READER_*), только чтение бакета записей;
#   agent  — сервис агента (S3_AGENT_*), чтение и запись только своего бакета отчётов.
# Бакет отчётов агента создаётся при старте, если его нет.
set -e

: "${S3_BUCKET:=affectra-recordings}"
: "${S3_AGENT_BUCKET:=affectra-agent-reports}"

cat > /tmp/s3.json <<JSON
{
  "identities": [
    {"name": "admin",
     "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
     "actions": ["Admin", "Read", "List", "Tagging", "Write"]},
    {"name": "reader",
     "credentials": [{"accessKey": "${S3_READER_ACCESS_KEY}", "secretKey": "${S3_READER_SECRET_KEY}"}],
     "actions": ["Read:${S3_BUCKET}", "List:${S3_BUCKET}"]},
    {"name": "agent",
     "credentials": [{"accessKey": "${S3_AGENT_ACCESS_KEY}", "secretKey": "${S3_AGENT_SECRET_KEY}"}],
     "actions": ["Read:${S3_AGENT_BUCKET}", "List:${S3_AGENT_BUCKET}", "Write:${S3_AGENT_BUCKET}"]}
  ]
}
JSON

(
    for _ in $(seq 1 60); do
        if echo "s3.bucket.list" | weed shell >/dev/null 2>&1; then
            echo "s3.bucket.list" | weed shell 2>/dev/null | grep -q "${S3_AGENT_BUCKET}" \
                || echo "s3.bucket.create -name ${S3_AGENT_BUCKET}" | weed shell >/dev/null 2>&1
            exit 0
        fi
        sleep 2
    done
) &

# Тома по 1 ГБ и до 100 штук: у каждого бакета свои тома, по умолчанию их не хватает уже на второй бакет
exec weed server -dir=/data -master.volumeSizeLimitMB=1024 -volume.max=100 -s3 -s3.port=8333 -s3.config=/tmp/s3.json
