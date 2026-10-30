#!/bin/bash
# =====================================================================
#  Script de demarrage du conteneur Odoo
#
#  Le mot de passe maitre d'Odoo (admin_passwd) ne peut etre defini que
#  dans le fichier de configuration. Sur Azure, il arrive par la variable
#  ODOO_ADMIN_PASSWD (secret lu dans Key Vault) : on l'ecrit dans une
#  copie temporaire de la configuration, jamais dans l'image.
#
#  Sans cette variable (docker compose en local), rien ne change.
# =====================================================================
set -e

if [ -n "${ODOO_ADMIN_PASSWD:-}" ]; then
    RUNTIME_RC=/tmp/odoo.conf
    python3 - "${ODOO_RC:-/etc/odoo/odoo.conf}" "$RUNTIME_RC" <<'PY'
import configparser
import os
import sys

source, target = sys.argv[1], sys.argv[2]
config = configparser.RawConfigParser()
config.read(source)
if not config.has_section('options'):
    config.add_section('options')
config.set('options', 'admin_passwd', os.environ['ODOO_ADMIN_PASSWD'])

# Fichier lisible uniquement par l'utilisateur odoo
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as handle:
    config.write(handle)
PY
    export ODOO_RC="$RUNTIME_RC"
fi

# Script de demarrage d'origine de l'image officielle Odoo
exec /entrypoint.sh "$@"
