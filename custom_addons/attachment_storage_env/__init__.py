import logging
import os

_logger = logging.getLogger(__name__)

ALLOWED_LOCATIONS = ('db', 'file')


def post_load():
    """Appele au demarrage du serveur, avant le chargement des bases.

    Remplace la lecture du parametre systeme ir_attachment.location par la
    valeur de ODOO_ATTACHMENT_LOCATION. Agir ici (et non dans un modele)
    garantit que meme les fichiers crees a la creation de la base, comme
    les CSS/JS de l'interface, suivent ce reglage.
    """
    location = (os.environ.get('ODOO_ATTACHMENT_LOCATION') or '').strip().lower()
    if location not in ALLOWED_LOCATIONS:
        return

    from odoo import api
    from odoo.addons.base.models.ir_attachment import IrAttachment

    @api.model
    def _storage(self):
        return location

    IrAttachment._storage = _storage
    _logger.info(
        "Pieces jointes stockees en '%s' (variable ODOO_ATTACHMENT_LOCATION)", location)
