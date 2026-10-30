{
    'name': 'Invoice OCR - AI Analyzer',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Analyse des factures avec Groq IA et remplissage automatique dans Odoo',
    'depends': ['account', 'invoice_ocr_pdf', 'invoice_ocr_image'],
    'data': [
        'security/ir.model.access.csv',
        'views/account_move_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'external_dependencies': {'python': ['groq']},
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
