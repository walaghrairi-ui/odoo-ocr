# Déploiement sur Azure Container Apps

Le template `infra/main.bicep` crée, dans le groupe de ressources `rg-odoo-ocr`
(région `switzerlandnorth`) :

| Ressource | Rôle |
|---|---|
| Azure Container Registry `acrodoo…` | Stocke l'image Docker |
| Key Vault `kv-odoo-…` | Stocke les mots de passe (PostgreSQL, mot de passe maître Odoo) |
| Identité managée `id-odoo-ocr` | Permet à l'application de tirer l'image et de lire Key Vault, sans mot de passe |
| Compte de stockage + partage `odoo-data` | Dossier `/var/lib/odoo` persistant (pièces jointes, sessions, modèles OCR) |
| Log Analytics `log-odoo-ocr` | Journaux de l'application |
| Environnement `cae-odoo-ocr` + application `ca-odoo-ocr` | Odoo, accessible en HTTPS |

Le serveur PostgreSQL `psql-odoo-ocr-2026`, déjà créé, est seulement **référencé** par le template.

## Comment les secrets sont protégés

```
 Vous (saisie masquée) ──► Bicep @secure() ──► Key Vault ──► Container App ──► variables d'environnement
                                                     ▲              │
                                                     └── identité managée (rôle "Key Vault Secrets User")
```

- Les mots de passe sont **demandés au moment du déploiement** (saisie masquée). Ils ne sont écrits dans aucun fichier.
- Ils sont rangés dans **Key Vault**. L'application les lit avec son **identité managée** : il n'y a aucun mot de passe dans l'image, le code ou le template.
- Le registre n'a pas de compte administrateur : l'image est tirée grâce à l'identité (rôle `AcrPull`).
- Le mot de passe maître d'Odoo est injecté au démarrage par `docker/odoo-entrypoint.sh`, dans un fichier temporaire lisible seulement par Odoo.

## 0. Préparer l'abonnement (une seule fois)

```cmd
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.KeyVault --wait
az provider register --namespace Microsoft.Storage --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
```

## 1. Phase 1 : créer l'infrastructure

Valider le template (rien n'est créé) :
```cmd
az deployment group validate --resource-group rg-odoo-ocr --template-file infra\main.bicep --parameters infra\main.parameters.json
```

Créer l'infrastructure (5 à 10 minutes) :
```cmd
az deployment group create --resource-group rg-odoo-ocr --name odoo-infra --template-file infra\main.bicep --parameters infra\main.parameters.json
```

Azure CLI vous **demande les deux mots de passe** (la saisie est masquée) :
- `postgresPassword` : le mot de passe du serveur PostgreSQL ;
- `odooAdminPassword` : le mot de passe maître d'Odoo, choisi par vous.

Récupérez le nom du registre :
```cmd
az deployment group show --resource-group rg-odoo-ocr --name odoo-infra --query properties.outputs.acrLoginServer.value -o tsv
```
Le résultat ressemble à `acrodooxxxxxxxxxxxxx.azurecr.io`. Il remplace `ACR_LOGIN_SERVER` dans les commandes ci-dessous. La partie avant `.azurecr.io` remplace `ACR_NAME`.

## 2. Construire, tagger et pousser l'image

```cmd
docker build -t invoice-ocr-odoo:1.0.0 .
docker tag invoice-ocr-odoo:1.0.0 ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.0
az acr login --name ACR_NAME
docker push ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.0
```

Vérifier que l'image est dans le registre :
```cmd
az acr repository show-tags --name ACR_NAME --repository invoice-ocr-odoo -o table
```

L'image pèse plusieurs Go : le premier envoi peut prendre longtemps selon votre connexion. Les envois suivants ne transfèrent que les couches modifiées.

## 3. Phase 2 : déployer l'application

```cmd
az deployment group create --resource-group rg-odoo-ocr --name odoo-app --template-file infra\main.bicep --parameters infra\main.parameters.json --parameters deployApp=true imageTag=1.0.0
```
Les deux mots de passe sont redemandés : saisissez les mêmes.

Adresse de l'application :
```cmd
az containerapp show --resource-group rg-odoo-ocr --name ca-odoo-ocr --query properties.configuration.ingress.fqdn -o tsv
```

## 4. Premier accès

1. Ouvrez `https://<adresse>/web/database/manager`.
2. Créez la base. Le **Master Password** est le `odooAdminPassword` saisi au déploiement.
3. Installez **Invoice OCR - AI Agent**, puis configurez la clé Groq dans
   **Paramètres → Facturation/Comptabilité → Saisie automatique IA (Groq)**.

Si vous avez déjà créé une base sur ce serveur PostgreSQL (tests avec `docker-compose.azure.yml`), elle apparaît directement.

## Mettre à jour l'application

Après une modification du code, construisez une nouvelle version avec un **nouveau tag** :
```cmd
docker build -t invoice-ocr-odoo:1.0.1 .
docker tag invoice-ocr-odoo:1.0.1 ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.1
docker push ACR_LOGIN_SERVER/invoice-ocr-odoo:1.0.1
az deployment group create --resource-group rg-odoo-ocr --name odoo-app --template-file infra\main.bicep --parameters infra\main.parameters.json --parameters deployApp=true imageTag=1.0.1
```
Si la mise à jour concerne une vue XML ou un manifeste, mettez ensuite à jour le module dans Odoo (Applications → module → Mettre à jour).

## Changer un mot de passe

```cmd
az keyvault secret set --vault-name KV_NAME --name postgres-password --value NOUVEAU_MOT_DE_PASSE
```
L'application reprend automatiquement la nouvelle valeur dans les 30 minutes. Le nom du coffre s'obtient avec :
`az deployment group show --resource-group rg-odoo-ocr --name odoo-infra --query properties.outputs.keyVaultName.value -o tsv`

Pour vous donner le droit de lire ou modifier les secrets vous-même :
```cmd
for /f %i in ('az ad signed-in-user show --query id -o tsv') do az role assignment create --assignee %i --role "Key Vault Secrets Officer" --scope /subscriptions/640aee12-b0eb-4c1a-a627-ed71f53d9947/resourceGroups/rg-odoo-ocr
```

## Surveiller

| Besoin | Commande |
|---|---|
| Journaux en direct | `az containerapp logs show --resource-group rg-odoo-ocr --name ca-odoo-ocr --follow` |
| État des révisions | `az containerapp revision list --resource-group rg-odoo-ocr --name ca-odoo-ocr -o table` |

## Coût

- `minReplicas = 0` (par défaut) : l'application s'arrête quand personne ne l'utilise. Elle ne coûte alors presque rien, mais la première visite prend une à quelques minutes, le temps de redémarrer avec une grosse image. Les tâches planifiées d'Odoo ne tournent pas pendant l'arrêt.
- Pour une démonstration (soutenance), gardez l'application allumée :
  ```cmd
  az containerapp update --resource-group rg-odoo-ocr --name ca-odoo-ocr --min-replicas 1
  ```
  Revenez à `0` ensuite.
- PostgreSQL : `az postgres flexible-server stop` / `start` (voir le README principal).

## Tout supprimer

```cmd
az containerapp delete --resource-group rg-odoo-ocr --name ca-odoo-ocr --yes
```
Si vous supprimez tout le groupe de ressources (`az group delete --name rg-odoo-ocr`), le Key Vault reste « supprimé de manière réversible » pendant 7 jours. Pour redéployer avant ce délai, purgez-le :
`az keyvault purge --name KV_NAME`
