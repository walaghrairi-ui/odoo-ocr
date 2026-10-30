{
    'name': 'Invoice OCR - AI Agent',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Assistant IA conversationnel pour interroger les factures en langage naturel',
    'description': """
Agent IA conversationnel
=========================
Un assistant intelligent qui permet d'interroger vos factures et tickets
en langage naturel grace a Groq IA.

Exemples de questions :
- Combien j'ai depense chez Dell ce mois-ci ?
- Quelles factures arrivent a echeance cette semaine ?
- Combien de tickets ai-je traites ?
    """,
    'author': 'PFE OCR',
    'depends': ['account', 'invoice_ocr_ai'],
    'data': [
        'security/ir.model.access.csv',
        'security/agent_security.xml',
        'views/invoice_agent_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'invoice_ocr_agent/static/src/css/agent_chat.css',
            'invoice_ocr_agent/static/src/js/agent_chat.js',
            'invoice_ocr_agent/static/src/xml/agent_chat.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
