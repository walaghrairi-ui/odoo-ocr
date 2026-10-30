from odoo import models, fields, api


class AgentConversation(models.Model):
    """Une conversation sauvegardee avec l'assistant IA."""
    _name = 'invoice.agent.conversation'
    _description = 'Conversation Assistant IA'
    _order = 'write_date desc'

    name = fields.Char(string="Titre", default="Nouvelle conversation")
    user_id = fields.Many2one(
        'res.users', string="Utilisateur",
        default=lambda self: self.env.user, required=True, index=True,
    )
    message_ids = fields.One2many(
        'invoice.agent.message', 'conversation_id', string="Messages",
    )

    # -----------------------------------------------------------------
    #  API appelee depuis l'interface (JS)
    # -----------------------------------------------------------------
    @api.model
    def get_my_conversations(self):
        """Retourne la liste des conversations de l'utilisateur courant."""
        convs = self.search([('user_id', '=', self.env.uid)])
        return [{
            'id': c.id,
            'name': c.name or "Nouvelle conversation",
        } for c in convs]

    @api.model
    def get_messages(self, conversation_id):
        """Retourne tous les messages d'une conversation (avec leurs donnees)."""
        conv = self.browse(conversation_id)
        if not conv.exists() or conv.user_id.id != self.env.uid:
            return []
        result = []
        for m in conv.message_ids.sorted('id'):
            data = {
                'role': m.role,
                'text': m.text or '',
                'table': m.get_table(),
                'chart': m.get_chart(),
                'kpis': m.get_kpis(),
            }
            result.append(data)
        return result

    @api.model
    def create_conversation(self, first_question):
        """Cree une nouvelle conversation avec un titre base sur la 1ere question."""
        titre = (first_question or "Nouvelle conversation").strip()
        if len(titre) > 40:
            titre = titre[:40] + "..."
        conv = self.create({'name': titre})
        return conv.id

    @api.model
    def save_message(self, conversation_id, role, text, table=None, chart=None, kpis=None):
        """Sauvegarde un message dans une conversation."""
        import json
        conv = self.browse(conversation_id)
        if not conv.exists() or conv.user_id.id != self.env.uid:
            return False
        self.env['invoice.agent.message'].create({
            'conversation_id': conversation_id,
            'role': role,
            'text': text or '',
            'table_json': json.dumps(table) if table else False,
            'chart_json': json.dumps(chart) if chart else False,
            'kpis_json': json.dumps(kpis) if kpis else False,
        })
        # mettre a jour la date de modif pour le tri
        conv.write({})
        return True

    @api.model
    def delete_conversation(self, conversation_id):
        """Supprime une conversation de l'utilisateur courant."""
        conv = self.browse(conversation_id)
        if conv.exists() and conv.user_id.id == self.env.uid:
            conv.unlink()
            return True
        return False

    @api.model
    def rename_conversation(self, conversation_id, new_name):
        """Renomme une conversation de l'utilisateur courant."""
        conv = self.browse(conversation_id)
        if conv.exists() and conv.user_id.id == self.env.uid:
            name = (new_name or '').strip()
            if name:
                conv.write({'name': name[:60]})
                return True
        return False

    @api.model
    def delete_all_my_conversations(self):
        """Efface tout l'historique de l'utilisateur courant."""
        convs = self.search([('user_id', '=', self.env.uid)])
        convs.unlink()
        return True


class AgentMessage(models.Model):
    """Un message dans une conversation (question ou reponse)."""
    _name = 'invoice.agent.message'
    _description = 'Message Assistant IA'
    _order = 'id asc'

    conversation_id = fields.Many2one(
        'invoice.agent.conversation', string="Conversation",
        required=True, ondelete='cascade', index=True,
    )
    role = fields.Selection([
        ('user', 'Utilisateur'),
        ('assistant', 'Assistant'),
    ], string="Role", required=True)
    text = fields.Text(string="Texte")

    # Donnees riches stockees en JSON (tableau, graphique, KPI)
    table_json = fields.Text(string="Tableau (JSON)")
    chart_json = fields.Text(string="Graphique (JSON)")
    kpis_json = fields.Text(string="KPI (JSON)")

    def get_table(self):
        import json
        self.ensure_one()
        return json.loads(self.table_json) if self.table_json else None

    def get_chart(self):
        import json
        self.ensure_one()
        return json.loads(self.chart_json) if self.chart_json else None

    def get_kpis(self):
        import json
        self.ensure_one()
        return json.loads(self.kpis_json) if self.kpis_json else None
