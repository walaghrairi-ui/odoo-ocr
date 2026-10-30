from odoo import models, fields
from odoo.exceptions import UserError

from .groq_service import GROQ_API_KEY_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # config_parameter : la valeur est lue et enregistree automatiquement
    # dans ir.config_parameter lors du clic sur "Enregistrer".
    groq_api_key = fields.Char(
        string="Cle API Groq",
        config_parameter=GROQ_API_KEY_PARAM,
        help="Token d'acces Groq (commence par gsk_). "
             "Cree-le sur https://console.groq.com/keys",
    )

    def action_test_groq_connection(self):
        """Verifie que la cle saisie est acceptee par Groq."""
        self.ensure_one()
        key = (self.groq_api_key or '').strip()
        if not key:
            raise UserError("Saisissez d'abord une cle API Groq.")

        client = self.env['invoice.ocr.groq'].get_client(api_key=key)
        try:
            client.models.list()
        except Exception as e:
            raise UserError(
                "Groq a refuse la connexion. Verifiez la cle et votre "
                f"connexion internet.\n\nDetail : {e}"
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Connexion Groq reussie",
                'message': "La cle API est valide. Pensez a cliquer sur Enregistrer.",
                'type': 'success',
                'sticky': False,
            },
        }
