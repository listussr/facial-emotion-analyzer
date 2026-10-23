#!/bin/sh
# Самоподписанный сертификат для HTTPS, если свой не положен в /etc/nginx/certs (affectra.crt и affectra.key).
# В сертификат попадают localhost, 127.0.0.1 и адреса из TLS_HOSTS через запятую (например, IP компьютера в сети).
set -e
DIR=/etc/nginx/certs
if [ -s "$DIR/affectra.crt" ] && [ -s "$DIR/affectra.key" ]; then
    exit 0
fi
mkdir -p "$DIR"
SAN="DNS:localhost,IP:127.0.0.1"
for host in $(echo "${TLS_HOSTS:-}" | tr ',' ' '); do
    case "$host" in
        *[!0-9.]*) SAN="$SAN,DNS:$host" ;;
        *)         SAN="$SAN,IP:$host" ;;
    esac
done
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=Affectra" -addext "subjectAltName=$SAN"     -keyout "$DIR/affectra.key" -out "$DIR/affectra.crt" 2>/dev/null
echo "affectra: self-signed certificate for $SAN"
