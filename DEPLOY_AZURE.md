# Déploiement sur Azure Container Apps (mode Express)

Le template `infra/main.bicep` crée, dans le groupe de ressources `rg-odoo-ocr`
(région `switzerlandnorth`) :

| Ressource | Rôle |
|---|---|
| Azure Container Registry `acrodoo…` | Stocke l'image Docker |
| Identité managée `id-odoo-ocr` | Permet à l'application de tirer l'image sans mot de passe |
| Log Analytics `log-odoo-ocr` | Journaux de l'application |
| Environnement `cae-odoo-ocr-express` + application `ca-odoo-ocr` | Odoo, accessible en HTTPS |

Le serveur PostgreSQL `psql-odoo-ocr-2026`, déjà créé, est seulement **référencé** par le template.

## Pourquoi le mode Express

Azure impose le mode **Express** aux environnements Container Apps de cet abonnement étudiant.
Ce mode n'offre ni disque persistant (Azure Files) ni références Key Vault. Le projet s'y adapte ainsi :

- **Pièces jointes** (factures, fichiers CSS/JS d'Odoo) : stockées dans **PostgreSQL**, grâce au module
  serveur `attachment_storage_env`, activé par `ODOO_ATTACHMENT_LOCATION=db`.
- **Modèles PaddleOCR** : retéléchargés automatiquement au premier OCR après chaque redémarrage
  (quelques secondes).
- **Sessions** : un redémarrage du conteneur déconnecte les utilisateurs ; il suffit de se reconnecter.

## Comment les secrets sont protégés

```
 Vous (saisie masquée) ──► paramètres Bicep @secure() ──► secrets Container Apps (chiffrés) ──► variables du conteneur
```

- Les mots de passe sont **demandés au moment du déploiement** (saisie masquée) : ils ne sont écrits dans aucun fichier.
- Les paramètres `@secure()` n'apparaissent jamais dans l'historique des déploiements Azure.
- Le conteneur les reçoit par des **secrets Container Apps** (`secretRef`), chiffrés par Azure. Il n'y a aucun mot de passe dans l'image, le code ou le template.
- Le registre n'a **pas de compte administrateur** : l'application tire l'image grâce à son identité managée (rôle `AcrPull`).
- Le mot de passe maître d'Odoo est injecté au démarrage par `docker/odoo-entrypoint.sh`, dans un fichier temporaire lisible seulement par Odoo.

## 0. Préparer l'abonnement (une seule fois)

```cmd
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
```

## 1. Phase 1 : créer l'infrastructure

```cmd
az deployment group validate --resource-group rg-odoo-ocr --template-file infra\main.bicep --parameters infra\main.parameters.json
az deployment group create --resource-group rg-odoo-ocr --name odoo-infra --template-file infra\main.bicep --parameters infra\main.parameters.json
```

Azure CLI demande les deux mots de passe (saisie masquée) :
- `postgresPassword` : le mot de passe du serveur PostgreSQL ;
- `odooAdminPassword` : le mot de passe maître d'Odoo.

Nom du registre :
```cmd
az deployment group show --resource-group rg-odoo-ocr --name odoo-infra --query properties.outputs.acrLoginServer.value -o tsv
```

## 2. Construire, tagger et pousser l'image

`ACR_LOGIN_SERVER` = résultat de la commande précédente ; `ACR_NAME` = sa partie avant `.azurecr.io`.

```cmd
docker build -t invoice-ocr-odoo:1.0.0 .
docker tag invoice-ocr-odoo:1.0.0 ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.0
az acr login --name ACR_NAME
docker push ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.0
az acr repository show-tags --name ACR_NAME --repository invoice-ocr-odoo -o table
```

## 3. Phase 2 : déployer l'application

```cmd
az deployment group create --resource-group rg-odoo-ocr --name odoo-app --template-file infra\main.bicep --parameters infra\main.parameters.json --parameters deployApp=true imageTag=1.0.0
```

Adresse de l'application :
```cmd
az containerapp show --resource-group rg-odoo-ocr --name ca-odoo-ocr --query properties.configuration.ingress.fqdn -o tsv
```

## 4. Premier accès

1. Ouvrez `https://<adresse>/web/database/manager`.
2. Créez une **nouvelle** base. Le Master Password est le `odooAdminPassword`.
   Créez-la depuis Azure, et non avec Odoo en local : une base créée en local garde ses fichiers sur votre PC.
3. Installez **Invoice OCR - AI Agent**, puis configurez la clé Groq dans les Paramètres.

Vérification : dans les journaux, la ligne `Pieces jointes stockees en 'db'` doit apparaître au démarrage.

## Mettre à jour l'application

```cmd
docker build -t invoice-ocr-odoo:1.0.1 .
docker tag invoice-ocr-odoo:1.0.1 ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.1
docker push ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.1
az deployment group create --resource-group rg-odoo-ocr --name odoo-app --template-file infra\main.bicep --parameters infra\main.parameters.json --parameters deployApp=true imageTag=1.0.1
```

## Changer un mot de passe

Redéployez la phase 2 (commande ci-dessus) en saisissant le nouveau mot de passe.
Pour PostgreSQL, changez-le d'abord sur le serveur :
`az postgres flexible-server update --resource-group rg-odoo-ocr --name psql-odoo-ocr-2026 --admin-password NOUVEAU`

## Surveiller

| Besoin | Commande |
|---|---|
| Journaux en direct | `az containerapp logs show --resource-group rg-odoo-ocr --name ca-odoo-ocr --follow` |
| État des révisions | `az containerapp revision list --resource-group rg-odoo-ocr --name ca-odoo-ocr -o table` |
| Noms des secrets (sans les valeurs) | `az containerapp secret list --resource-group rg-odoo-ocr --name ca-odoo-ocr -o table` |

## Coût

- `minReplicas = 0` (par défaut) : l'application s'arrête quand personne ne l'utilise. Elle ne coûte alors presque rien, mais la première visite prend une à quelques minutes.
- Pour une démonstration, gardez-la allumée, puis revenez à `0` ensuite :
  ```cmd
  az containerapp update --resource-group rg-odoo-ocr --name ca-odoo-ocr --min-replicas 1
  ```
