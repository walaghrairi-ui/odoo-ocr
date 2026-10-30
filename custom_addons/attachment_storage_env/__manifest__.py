{
    'name': 'Attachment Storage from Environment',
    'version': '19.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': "Force l'emplacement des pieces jointes via ODOO_ATTACHMENT_LOCATION",
    'description': """
Module serveur (a charger avec --load), sans installation dans la base.

Si la variable d'environnement ODOO_ATTACHMENT_LOCATION vaut "db", toutes
les pieces jointes (factures, fichiers CSS/JS d'Odoo...) sont stockees
dans PostgreSQL au lieu du disque du conteneur.

Indispensable sur un hebergement sans disque persistant (Azure Container
Apps Express) : sinon chaque redemarrage efface les fichiers.
Sans la variable, le module ne change rien.
    """,
    'author': 'PFE OCR',
    'depends': ['base'],
    'post_load': 'post_load',
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
