from odoo import models, api
from odoo.exceptions import UserError

# Cle du parametre systeme qui stocke le token Groq
# (modifiable dans Parametres > Comptabilite > Saisie automatique IA).
GROQ_API_KEY_PARAM = 'invoice_ocr_ai.groq_api_key'


class GroqService(models.AbstractModel):
    """Point d'acces unique a Groq pour tous les modules OCR.

    Le token n'est jamais ecrit dans le code : il est lu dans les
    parametres systeme d'Odoo, ou l'administrateur le renseigne depuis
    l'ecran Parametres.
    """
    _name = 'invoice.ocr.groq'
    _description = 'Service Groq (cle API configurable)'

    @api.model
    def get_api_key(self):
        """Retourne le token Groq configure, ou leve une erreur explicite."""
        key = self.env['ir.config_parameter'].sudo().get_param(GROQ_API_KEY_PARAM)
        key = (key or '').strip()
        if not key:
            raise UserError(
                "La cle API Groq n'est pas configuree.\n\n"
                "Un administrateur doit la renseigner dans :\n"
                "Parametres > Comptabilite > Saisie automatique IA (Groq)."
            )
        return key

    @api.model
    def get_client(self, api_key=None):
        """Retourne un client Groq pret a l'emploi.

        :param api_key: cle a utiliser a la place de celle enregistree
                        (sert au bouton de test avant enregistrement).
        """
        try:
            from groq import Groq
        except ImportError:
            raise UserError(
                "La librairie Groq n'est pas installee ! Executez : pip install groq"
            )
        return Groq(api_key=(api_key or '').strip() or self.get_api_key())
