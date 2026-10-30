// =====================================================================
//  Identite utilisee par GitHub Actions pour deployer sur Azure
//
//  Connexion OIDC (identite federee) : GitHub prouve son identite a Azure
//  avec un jeton temporaire. Aucun mot de passe ni cle n'est stocke dans
//  GitHub. Seul un push sur la branche indiquee du depot indique est
//  accepte.
//
//  A deployer une fois, apres l'application (main.bicep phase 2) :
//    az deployment group create --resource-group rg-odoo-ocr --name github-ci
//       --template-file infra\github-ci.bicep
// =====================================================================
targetScope = 'resourceGroup'

@description('Region de l\'identite.')
param location string = 'switzerlandnorth'

@description('Depot GitHub autorise (proprietaire/nom).')
param githubRepository string = 'walaghrairi-ui/odoo-ocr'

@description('Branche autorisee a deployer.')
param githubBranch string = 'main'

@description('Nom du registre cree par main.bicep.')
param acrName string = 'acrodoo${uniqueString(resourceGroup().id)}'

@description('Nom de l\'application Container Apps.')
param containerAppName string = 'ca-odoo-ocr'

@description('Identite managee utilisee par l\'application elle-meme.')
param appIdentityName string = 'id-odoo-ocr'

// Roles Azure integres
var roleAcrPush = '8311e382-0749-4cb8-b61a-304f252e45ec'
var roleContributor = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var roleManagedIdentityOperator = 'f1a07417-d97a-45cb-824c-7a7467783830'

// ------------------------------------------------------------ Ressources existantes
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: containerAppName
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: appIdentityName
}

// ------------------------------------------------------------ Identite GitHub
resource githubIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-github-odoo-ocr'
  location: location
}

// Relie l'identite au depot : seuls les workflows de cette branche
// peuvent l'utiliser.
resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: githubIdentity
  name: 'github-${githubBranch}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:ref:refs/heads/${githubBranch}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

// ------------------------------------------------------------ Droits minimums
// 1. Pousser des images dans le registre
resource canPushImages 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, githubIdentity.id, roleAcrPush)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPush)
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// 2. Mettre a jour l'application (et seulement elle)
resource canUpdateApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerApp.id, githubIdentity.id, roleContributor)
  scope: containerApp
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleContributor)
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// 3. Conserver l'identite de l'application lors de la mise a jour
resource canKeepAppIdentity 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appIdentity.id, githubIdentity.id, roleManagedIdentityOperator)
  scope: appIdentity
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleManagedIdentityOperator)
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------ Sorties
// Valeurs a copier dans les secrets GitHub (ce sont des identifiants,
// pas des mots de passe : ils ne donnent aucun acces sans le jeton OIDC).
output clientId string = githubIdentity.properties.clientId
output tenantId string = subscription().tenantId
output subscriptionId string = subscription().subscriptionId
