from odoo import models, fields
from odoo.exceptions import UserError
import base64


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_extract_ocr(self):
        """Extraire les données de la facture avec OCR et Groq"""
        self.ensure_one()

        # Vérifier qu'une pièce jointe existe
        if not self.message_main_attachment_id:
            raise UserError("Veuillez uploader une facture d'abord !")

        # Récupérer le fichier uploadé
        attachment = self.message_main_attachment_id
        file_data = base64.b64decode(attachment.datas)
        file_name = attachment.name

        # Extraction du texte selon le type de fichier
        extracted_text = ""
        if file_name.lower().endswith('.pdf'):
            # Addon invoice_ocr_pdf → extraction PDF avec PyMuPDF
            extracted_text = self.env['invoice.ocr.pdf'].extract_text_from_pdf(file_data)
        else:
            # Addon invoice_ocr_image → extraction image avec Tesseract OCR
            extracted_text = self.env['invoice.ocr.image'].extract_text_from_image(file_data)

        # Envoyer le texte à Groq pour analyser
        self._parse_with_groq(extracted_text)

    def _parse_with_groq(self, text):
        """Analyser le texte extrait avec Groq IA"""
        import json
        import re

        # Client Groq avec la clé configurée dans Paramètres > Comptabilité
        client = self.env['invoice.ocr.groq'].get_client()

        # Détection automatique du type de document
        prompt_detect = f"""
        Analyse ce texte et réponds UNIQUEMENT par un mot :
        - "ticket" si c'est un ticket de caisse (restaurant, supermarché, magasin)
        - "facture" si c'est une facture fournisseur professionnelle

        Texte :
        {text}
        """
        response_detect = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt_detect}],
        )
        detected_type = response_detect.choices[0].message.content.strip().lower()

        # Changer automatiquement le type dans Odoo
        if detected_type == 'ticket':
            self.sudo().write({'move_type': 'in_receipt'})
        else:
            self.sudo().write({'move_type': 'in_invoice'})

        # Le prompt qu'on envoie à Groq
        if self.move_type == 'in_receipt':
            prompt = f"""
            Analyse ce ticket de caisse et extrais les informations suivantes.
            Réponds UNIQUEMENT en JSON avec ces champs :
            {{
                "partner_name": "nom du magasin ou restaurant",
                 "partner_city": "ville du magasin si disponible",
                 "partner_phone": "téléphone du magasin si disponible",
                  "invoice_date": "date au format YYYY-MM-DD",
                    "amount_total": montant total en nombre sans symbole,
                        "lines": [
                            {{
                                "name": "nom du produit ou article",
                                "quantity": quantité achetée,
                                "price_unit": prix unitaire,
                                "tax_rate": taux TVA en pourcentage ou 0,
                                "account_class": "numéro de classe du compte selon le Plan Comptable Tunisien (PCT).
                                    Classe 6 : charges.
                                    Retourner UNIQUEMENT le chiffre."
                            }}
                        ]
                    }}
                    Texte du ticket :
                    {text}
                    """
        else:
            prompt = f"""
                    Analyse ce texte d'une facture et extrais les informations suivantes.
        Réponds UNIQUEMENT en JSON avec ces champs :
        {{
            "partner_name": "nom de l'entreprise ou personne qui émet la facture (vendeur/fournisseur), pas le client qui reçoit la facture",
            "company_name": "nom de l'entreprise qui REÇOIT la facture (le client)",
            "partner_street": "adresse rue du fournisseur",
            "partner_city": "ville du fournisseur",
            "partner_zip": "code postal du fournisseur",
            "partner_country": "pays du fournisseur",
            "partner_phone": "téléphone du fournisseur",
            "partner_email": "email du fournisseur",
            "invoice_number": "numéro de facture",
            "invoice_date": "date au format YYYY-MM-DD",
            "invoice_date_due": "date d'échéance (due date) au format YYYY-MM-DD",
            "amount_untaxed": montant HT en nombre sans symbole,
            "amount_tax": montant TVA en nombre sans symbole,
            "amount_total": montant total en nombre sans symbole,
            "lines": [
                {{
                    "name": "nom du produit",
                    "quantity": lire la colonne quantité du tableau. 
                    Si pas visible, mettre 1.,
                    "price_unit": prix unitaire HT SANS remise (pas le total de la ligne). Ex: si Qty=3, Unit Price=120€, Total=324€ → price_unit=120,
                    "discount": remise en pourcentage appliquée sur cette ligne spécifique (nombre sans symbole %). 
                      Lire la colonne 'Remise' ou 'Discount' dans le tableau de la facture pour chaque ligne séparément. 
                      Mettre 0 si pas de remise sur cette ligne,
                    "total_ht": montant total HT de cette ligne après remise et avant TVA.
                     C'est TOUJOURS = quantité × prix_unitaire × (1 - remise/100).
                     Ce montant est TOUJOURS différent du prix_unitaire sauf si quantité=1 ET remise=0%.
                     Lire la colonne Total ou Amount ou Montant du tableau.,
                    "tax_rate": taux de TVA en pourcentage appliqué sur cette ligne (nombre sans symbole %). 
                     Si toutes les lignes ont le même taux, répliquer ce taux sur chaque ligne. 
                     Mettre 0 si vraiment aucune taxe,
                    "account_class": "numéro de classe du compte selon le Plan Comptable Tunisien (PCT).
                        Classe 1 : capitaux propres et passifs non courants.
                        Classe 2 : actifs non courants (immobilisations).
                        Classe 3 : stocks et en-cours.
                        Classe 4 : comptes de tiers.
                        Classe 5 : comptes financiers.
                        Classe 6 : charges.
                        Classe 7 : produits.
                        Retourner UNIQUEMENT le chiffre (1, 2, 3, 4, 5, 6 ou 7)."
                }}
            ]
        }}

        Texte de la facture :
        {text}
        """

        # Envoyer le prompt à Groq
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
        )

        # Extraire le texte de la réponse
        response_text = response.choices[0].message.content.strip()

        # Nettoyer la réponse - garder uniquement le JSON
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group()

        # Convertir le JSON en dictionnaire Python
        print("=== REPONSE BRUTE DE GROQ ===")
        print(response_text)
        print("=== FIN ===")
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            raise UserError(
                "L'extraction automatique a échoué.\n\n"
                "Avant de réessayer, vérifiez :\n"
                "• Votre connexion internet\n"
                "• La lisibilité de l'image ou le PDF\n\n"
                "Si le problème persiste, réessayez plus tard!"
            )

        # Vérifier que la facture appartient à notre entreprise
        company = self.env.company
        company_name = data.get('company_name', '')
        if company_name and company.name.lower() != company_name.lower():
                raise UserError(
               f"Cette facture n'appartient pas à {company.name} !\n"
               f"Nom trouvé sur la facture : {company_name}"


            )

        # Remplir le champ Fournisseur
        if data.get('partner_name'):
            partner = None

            # Recherche stricte 1 : nom + email
            if data.get('partner_email'):
                partner = self.env['res.partner'].search([
                    ('name', 'ilike', data['partner_name']),
                    ('email', '=', data['partner_email']),
                ], limit=1)

            # Recherche stricte 2 : nom + téléphone
            if not partner and data.get('partner_phone'):
                partner = self.env['res.partner'].search([
                    ('name', 'ilike', data['partner_name']),
                    ('phone', '=', data['partner_phone']),
                ], limit=1)

            # Recherche stricte 3 : nom + ville
            if not partner and data.get('partner_city'):
                partner = self.env['res.partner'].search([
                    ('name', 'ilike', data['partner_name']),
                    ('city', 'ilike', data['partner_city']),
                ], limit=1)

            # Recherche souple : nom seul (dernier recours)
            if not partner:
                partner = self.env['res.partner'].search([
                    ('name', 'ilike', data['partner_name']),
                ], limit=1)

            if partner:
                # Fournisseur trouvé → on remplit l'adresse seulement si elle est vide
                if not partner.street:
                    partner.write({
                        'street': data.get('partner_street', ''),
                        'city': data.get('partner_city', ''),
                        'zip': data.get('partner_zip', ''),
                        'phone': data.get('partner_phone', ''),
                        'email': data.get('partner_email', ''),
                    })
                self.partner_id = partner
                self._fetch_partner_logo(partner, data.get('partner_email', ''))
            else:
                # Fournisseur non trouvé → on le crée avec toutes les infos
                partner = self.env['res.partner'].create({
                    'name': data['partner_name'],
                    'supplier_rank': 1,
                    'street': data.get('partner_street', ''),
                    'city': data.get('partner_city', ''),
                    'zip': data.get('partner_zip', ''),
                    'phone': data.get('partner_phone', ''),
                    'email': data.get('partner_email', ''),
                })
                self.partner_id = partner
                self._fetch_partner_logo(partner, data.get('partner_email', ''))

        # Remplir le champ Référence facture
        if data.get('invoice_number'):
            self.ref = data['invoice_number']

        # Remplir le champ Date
        if data.get('invoice_date'):
            from datetime import datetime
            self.invoice_date = datetime.strptime(
                data['invoice_date'], '%Y-%m-%d'
            ).date()

        # Échéance
        if data.get('invoice_date_due'):
            from datetime import datetime
            self.invoice_date_due = datetime.strptime(
                data['invoice_date_due'], '%Y-%m-%d'
            ).date()

        # Remplir les lignes de produits
        if data.get('lines'):
            # Supprimer les lignes existantes
            self.invoice_line_ids = [(5, 0, 0)]
            lines = []
            for line in data['lines']:
                tax_ids = []
                tax_rate = float(line.get('tax_rate') or 0)
                if tax_rate > 0:
                    tax = self.env['account.tax'].search([
                        ('amount', '=', tax_rate),
                        ('type_tax_use', '=', 'purchase'),
                    ], limit=1)
                    if tax:
                        tax_ids = [(4, tax.id)]

                # Chercher le compte comptable PCT dans Odoo
                account_id = False
                account_class = str(line.get('account_class', '') or '').strip()

                if account_class:
                    # Étape 1 : Récupérer TOUS les comptes de cette classe dans Odoo
                    accounts = self.env['account.account'].search([
                        ('code', '=like', account_class + '_____'),
                        ('company_ids', 'in', self.company_id.id),
                    ])

                    if accounts:
                        # Étape 2 : Préparer la liste complète pour Groq
                        accounts_str = '\n'.join([
                            f"{a.code} - {a.name}" for a in accounts
                        ])

                        # Étape 3 : 2ème prompt à Groq avec liste complète
                        prompt2 = f"""
                        Tu es un expert comptable tunisien certifié.
                        Tu dois choisir le compte comptable PCT le plus précis pour ce produit/service.

                        Produit/Service : {line.get('name')}

                        Comptes disponibles :
                        {accounts_str}

                        Règles importantes :
                        - Un audit ou honoraire professionnel → compte 622000
                        - Une étude ou recherche → compte 617000
                        - Une formation → compte 617000
                        - Une sous-traitance → compte 611000
                        - Une marchandise → compte 607000

                        Retourne UNIQUEMENT le code numérique exact à 6 chiffres.
                        """

                        response2 = client.chat.completions.create(
                            model="openai/gpt-oss-120b",
                            messages=[{"role": "user", "content": prompt2}],
                        )

                        code2 = response2.choices[0].message.content.strip()
                        match = re.search(r'\d{6}', code2)
                        code2 = match.group() if match else ''

                        # Étape 4 : Vérification finale
                        account = self.env['account.account'].search([
                            ('code', '=', code2),
                            ('company_ids', 'in', self.company_id.id),
                        ], limit=1)

                        if account:
                            account_id = account.id

                qty = int(float(line.get('quantity') or 1))

                lines.append((0, 0, {
                    'name': line.get('name', 'Produit'),
                    'quantity': qty,
                    'price_unit': float(line.get('price_unit') or 0),
                    'discount': float(line.get('discount') or 0),
                    'tax_ids': tax_ids,
                    'account_id': account_id,
                }))
            self.invoice_line_ids = lines

    def _fetch_partner_logo(self, partner, email):
        """Récupérer le logo du fournisseur depuis Google Favicon"""
        if not email or partner.image_1920:
            return
        try:
            import requests
            import base64
            domain = email.split('@')[-1]
            url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                partner.image_1920 = base64.b64encode(response.content)
        except Exception:
            pass
