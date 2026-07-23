#!/bin/sh
set -eu

: "${ODOO_MASTER_PASSWORD:?ODOO_MASTER_PASSWORD is required}"
umask 077
runtime_config=/tmp/parcel-odoo.conf
cp /etc/odoo/odoo.conf "$runtime_config"
printf '\nadmin_passwd = %s\n' "$ODOO_MASTER_PASSWORD" >> "$runtime_config"

if [ "$#" -gt 0 ] && [ "$1" = "odoo" ]; then
    shift
fi

exec /usr/bin/odoo server --config "$runtime_config" "$@"
