# Invoice OCR – Odoo 19 sous Docker

Odoo 19 + PostgreSQL 16 + les modules `invoice_ocr_*`
(PyMuPDF, PaddleOCR, Groq), lancés avec Docker Compose.

```
odoo-docker/
├── Dockerfile            # image Odoo 19 + dependances OCR / IA
├── docker-compose.yml    # services odoo + db (PostgreSQL)
├── requirements.txt      # dependances Python des modules
├── config/odoo.conf      # configuration Odoo
├── .env.example          # identifiants PostgreSQL et port
└── custom_addons/        # vos 4 modules
```

## Prérequis

- Docker Desktop (Windows / macOS) ou Docker Engine + plugin Compose (Linux)
- Environ 6 Go d'espace disque (PaddlePaddle et OpenCV sont volumineux)
- Une connexion internet (premier build, appels Groq, modèles PaddleOCR)

## 1. Démarrer

```bash
cp .env.example .env              # puis changez POSTGRES_PASSWORD
docker compose up -d --build      # premier build : 5 à 15 minutes
docker compose ps                 # db doit etre "healthy", odoo "running"
```

Ouvrez http://localhost:8069.

## 2. Créer la base et installer les modules

**Option A – depuis le navigateur**

1. Sur l'écran de création de base, le *Master Password* est la valeur
   `admin_passwd` de `config/odoo.conf` (`admin_change_me` par défaut).
2. Une fois connecté : **Applications → Mettre à jour la liste des applications**.
3. Cherchez **Invoice OCR - AI Agent** et installez-le
   (il installe automatiquement les modules `ai`, `pdf` et `image`).

**Option B – en ligne de commande**

```bash
docker compose run --rm odoo odoo -d odoo -i invoice_ocr_agent --without-demo=all --stop-after-init
docker compose restart odoo
```

Connexion ensuite avec `admin` / `admin` sur la base `odoo`
(changez ce mot de passe tout de suite).

## 3. Configurer Groq

**Paramètres → Comptabilité → Saisie automatique IA (Groq)** :
collez votre clé, cliquez sur **Tester la connexion**, puis **Enregistrer**.

## Tester

- **Saisie automatique** : Comptabilité → Fournisseurs → Factures → Nouveau,
  joignez un PDF ou une image, puis cliquez sur **Saisie automatique**.
- **Assistant IA** : Comptabilité → Fournisseurs → Assistant IA.

Au **premier OCR d'une image**, PaddleOCR télécharge ses modèles (quelques
dizaines de Mo). Ils sont conservés dans le volume `odoo-data`, donc ce
délai n'arrive qu'une fois.

## Commandes utiles

| Besoin | Commande |
|---|---|
| Voir les logs | `docker compose logs -f odoo` |
| Après modification d'un fichier Python | `docker compose restart odoo` |
| Après modification d'une vue XML / du manifeste | `docker compose run --rm odoo odoo -d odoo -u invoice_ocr_ai --stop-after-init` puis `docker compose restart odoo` |
| Après modification de `requirements.txt` ou `Dockerfile` | `docker compose up -d --build` |
| Shell Odoo | `docker compose exec odoo odoo shell -d odoo --db_host=db --db_user=odoo --db_password=<mot de passe>` |
| Sauvegarder la base | `docker compose exec db pg_dump -U odoo odoo > sauvegarde.sql` |
| Arrêter | `docker compose down` |
| Tout effacer (base comprise) | `docker compose down -v` |

## Notes

- **Mode développement** : `custom_addons/` est monté directement dans le
  conteneur, vos modifications sont visibles sans reconstruire l'image.
  En production, retirez ce montage dans `docker-compose.yml` : l'image
  contient déjà les modules.
- **Versions figées** : PaddleOCR reste en 2.10 car le code utilise
  `ocr.ocr(image, cls=True)`, une API modifiée en 3.x. Le Dockerfile
  empêche pip de remplacer les bibliothèques d'Odoo (Pillow, lxml, requests).
- **Mac Apple Silicon (M1/M2/M3)** : les paquets existent pour ARM, aucune
  option particulière n'est nécessaire.
- **PostgreSQL** n'est pas exposé sur votre PC par défaut ; décommentez la
  section `ports` du service `db` pour y accéder avec pgAdmin ou DBeaver.
