# CI/CD : GitHub Actions → Azure Container Registry → Azure Container Apps

À chaque **push sur `main`**, le workflow `.github/workflows/deploy.yml` :

1. récupère le code ;
2. construit l'image Docker ;
3. la pousse dans le registre `acrodoobi3traga7tf44`, avec deux tags : le **SHA du commit** et `latest` ;
4. met à jour l'application `ca-odoo-ocr` avec l'image de ce commit. Azure crée une nouvelle **révision** et remplace l'ancienne : c'est le « signal » de redéploiement ;
5. vérifie qu'Odoo répond sur `/web/health`.

```
 git push main ──► GitHub Actions ──build──► image ──push──► ACR
                        │                                     │
                        └── az containerapp update ──► Container Apps tire la nouvelle image
```

## Sécurité : connexion OIDC, sans mot de passe

GitHub ne stocke **aucun mot de passe ni aucune clé Azure**. À chaque exécution, GitHub fournit un
jeton temporaire qui prouve « je suis le workflow du dépôt `walaghrairi-ui/odoo-ocr`, branche `main` ».
Azure n'accepte que ce dépôt et cette branche (identité fédérée).

L'identité `id-github-odoo-ocr` n'a que les droits nécessaires :

| Droit | Sur | Pourquoi |
|---|---|---|
| AcrPush | le registre | pousser les images |
| Contributor | l'application `ca-odoo-ocr` uniquement | changer l'image |
| Managed Identity Operator | l'identité `id-odoo-ocr` | garder l'identité de l'application lors de la mise à jour |

Les mots de passe PostgreSQL et Odoo **ne passent jamais par GitHub** : ils restent dans les secrets de Container Apps.

## Mise en place (une seule fois)

### 1. Créer l'identité GitHub dans Azure
```cmd
az deployment group create --resource-group rg-odoo-ocr --name github-ci --template-file infra\github-ci.bicep
```

Récupérer les trois valeurs à donner à GitHub :
```cmd
az deployment group show --resource-group rg-odoo-ocr --name github-ci --query "properties.outputs.{AZURE_CLIENT_ID:clientId.value, AZURE_TENANT_ID:tenantId.value, AZURE_SUBSCRIPTION_ID:subscriptionId.value}" -o json
```

### 2. Ajouter les secrets dans GitHub
Sur https://github.com/walaghrairi-ui/odoo-ocr : **Settings → Secrets and variables → Actions → New repository secret**.
Créez trois secrets, avec les valeurs de l'étape 1 :

| Nom | Valeur |
|---|---|
| `AZURE_CLIENT_ID` | AZURE_CLIENT_ID |
| `AZURE_TENANT_ID` | AZURE_TENANT_ID |
| `AZURE_SUBSCRIPTION_ID` | AZURE_SUBSCRIPTION_ID |

Ces valeurs sont des identifiants, pas des mots de passe : sans le jeton OIDC de votre dépôt, elles ne donnent aucun accès. On les range quand même en secrets, car le dépôt est public.

### 3. Pousser le workflow
```cmd
git add .github/workflows/deploy.yml infra/github-ci.bicep CICD.md
git commit -m "CI/CD : build, push ACR et deploiement Container Apps"
git push origin main
```
Suivez l'exécution dans l'onglet **Actions** du dépôt.

## Utilisation au quotidien

- Modifiez le code, puis faites `git push` sur `main` : le déploiement se fait seul.
- Pour relancer sans modifier le code : **Actions → Build et deploiement Azure → Run workflow**.
- Une modification qui ne touche que `*.md`, `infra/` ou `docker-compose*.yml` ne déclenche pas de build.

### Après une modification d'un module Odoo
Le nouveau code est bien en ligne, mais Odoo ne relit les **vues XML, les droits et les données** d'un
module qu'après sa mise à jour dans la base. Pour les changements Python seuls, rien à faire.
Sinon : **Applications → retirer le filtre « Applications » → le module → ⋮ → Mettre à jour**
(mode développeur activé).

## Durées

- **Premier build** : 20 à 30 minutes (PaddlePaddle, OpenCV…). Le cache est rempli à cette occasion.
- **Builds suivants** : quelques minutes, car seules les couches modifiées (vos modules) sont reconstruites.
- **Démarrage de la nouvelle révision** : 1 à 5 minutes.

## Attention avec `main.bicep`

Le workflow ne change **que l'image**. Si vous relancez plus tard la phase 2 de `main.bicep` à la main,
passez `imageTag=latest`, sinon vous reviendrez à l'image `1.0.0` :
```cmd
az deployment group create --resource-group rg-odoo-ocr --name odoo-app --template-file infra\main.bicep --parameters infra\main.parameters.json --parameters deployApp=true imageTag=latest
```

## Dépannage

| Erreur dans Actions | Cause probable |
|---|---|
| `AADSTS70021` / `No matching federated identity record` | Les secrets GitHub ou le nom du dépôt/de la branche ne correspondent pas à `github-ci.bicep` |
| `unauthorized` au push | Le rôle AcrPush n'est pas encore actif : relancez quelques minutes plus tard |
| `AuthorizationFailed` au déploiement | Rôle Contributor ou Managed Identity Operator manquant : redéployez `github-ci.bicep` |
| `No space left on device` | Image trop lourde pour le runner : prévenir, on réduira l'image |
| La vérification échoue | `az containerapp logs show -g rg-odoo-ocr -n ca-odoo-ocr --tail 100` |
