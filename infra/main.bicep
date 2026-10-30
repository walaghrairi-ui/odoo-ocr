// =====================================================================
//  Odoo 19 + modules Invoice OCR sur Azure Container Apps
//
//  Deploiement en 2 phases (meme template) :
//    1. deployApp = false : registre, Key Vault, stockage, environnement
//       -> on pousse ensuite l'image Docker dans le registre
//    2. deployApp = true  : l'application Odoo elle-meme
//
//  Les mots de passe sont des parametres @secure() : ils ne sont jamais
//  ecrits dans un fichier ni affiches dans l'historique de deploiement.
//  Ils sont ranges dans Key Vault, que l'application lit grace a une
//  identite managee (aucun mot de passe dans l'image ni dans le code).
// =====================================================================
targetScope = 'resourceGroup'

// ------------------------------------------------------------ Parametres
@description('Region des ressources (doit etre autorisee sur l\'abonnement).')
param location string = 'switzerlandnorth'

@description('Nom court de l\'application, utilise pour nommer les ressources.')
param appName string = 'odoo-ocr'

@description('false = phase 1 (infrastructure) ; true = phase 2 (application).')
param deployApp bool = false

@description('Nom de l\'image dans le registre.')
param imageName string = 'invoice-ocr-odoo'

@description('Tag (version) de l\'image a deployer.')
param imageTag string = '1.0.0'

@description('Nom du serveur PostgreSQL Flexible existant (meme groupe de ressources).')
param postgresServerName string = 'psql-odoo-ocr-2026'

@description('Utilisateur administrateur PostgreSQL.')
param postgresUser string = 'odooadmin'

@secure()
@description('Mot de passe PostgreSQL (range dans Key Vault).')
param postgresPassword string

@secure()
@description('Mot de passe maitre Odoo : protege le gestionnaire de bases (range dans Key Vault).')
param odooAdminPassword string

@allowed(['1.0', '1.5', '2.0'])
@description('vCPU du conteneur (PaddleOCR a besoin d\'au moins 1).')
param cpu string = '1.5'

@allowed(['2Gi', '3Gi', '4Gi'])
@description('Memoire du conteneur (le double du nombre de vCPU).')
param memory string = '3Gi'

@minValue(0)
@maxValue(1)
@description('0 = l\'application s\'arrete quand personne ne l\'utilise (economique) ; 1 = toujours allumee.')
param minReplicas int = 0

// ------------------------------------------------------------ Noms
// Suffixe stable et unique : evite les noms deja pris sur Azure.
var suffix = uniqueString(resourceGroup().id)
var acrName = 'acrodoo${suffix}'
var keyVaultName = 'kv-odoo-${suffix}'
var storageName = 'stodoo${suffix}'
var shareName = 'odoo-data'
var identityName = 'id-${appName}'
var logName = 'log-${appName}'
// Suffixe -std : environnement standard (workload profiles). Un premier
// environnement cree en mode Express ne supportait pas Azure Files.
var environmentName = 'cae-${appName}-std'
var workloadProfileName = 'Consumption'
var containerAppName = 'ca-${appName}'

// Roles Azure integres
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'

// ------------------------------------------------------------ Identite
// Identite managee de l'application : lui permet de tirer l'image et de
// lire les secrets sans aucun mot de passe.
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// ------------------------------------------------------------ Registre
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roleAcrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------ Key Vault
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource secretPostgres 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'postgres-password'
  properties: {
    value: postgresPassword
  }
}

resource secretOdooAdmin 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'odoo-admin-password'
  properties: {
    value: odooAdminPassword
  }
}

resource keyVaultRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, roleKeyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------ Stockage
// Dossier /var/lib/odoo persistant : pieces jointes (filestore),
// sessions et modeles PaddleOCR telecharges.
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareName
  properties: {
    shareQuota: 20
  }
}

// ------------------------------------------------------------ Journaux
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ------------------------------------------------------------ Environnement
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    // Declarer un profil force un environnement standard (et non Express),
    // seul mode qui accepte le stockage Azure Files. Le profil Consumption
    // reste facture a l'usage.
    workloadProfiles: [
      {
        name: workloadProfileName
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: shareName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

// ------------------------------------------------------------ PostgreSQL
// Serveur deja cree : on lit seulement son adresse.
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: postgresServerName
}

// ------------------------------------------------------------ Application
resource odooApp 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  dependsOn: [
    registryPull
    keyVaultRead
  ]
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: workloadProfileName
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8069
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      // References Key Vault : l'application lit les secrets elle-meme.
      secrets: [
        {
          name: 'postgres-password'
          keyVaultUrl: secretPostgres.properties.secretUri
          identity: identity.id
        }
        {
          name: 'odoo-admin-password'
          keyVaultUrl: secretOdooAdmin.properties.secretUri
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'odoo'
          image: '${registry.properties.loginServer}/${imageName}:${imageTag}'
          // --proxy-mode : Odoo est derriere le proxy HTTPS de Container Apps
          args: [
            'odoo'
            '--proxy-mode'
          ]
          env: [
            {
              name: 'HOST'
              value: postgres.properties.fullyQualifiedDomainName
            }
            {
              name: 'USER'
              value: postgresUser
            }
            {
              name: 'PASSWORD'
              secretRef: 'postgres-password'
            }
            {
              name: 'PGSSLMODE'
              value: 'require'
            }
            {
              name: 'ODOO_ADMIN_PASSWD'
              secretRef: 'odoo-admin-password'
            }
          ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/web/health'
                port: 8069
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/web/health'
                port: 8069
              }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
          volumeMounts: [
            {
              volumeName: 'odoo-data'
              mountPath: '/var/lib/odoo'
            }
          ]
        }
      ]
      // Une seule instance : Odoo n'est pas prevu ici pour plusieurs copies.
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
      }
      volumes: [
        {
          name: 'odoo-data'
          storageType: 'AzureFile'
          storageName: environmentStorage.name
          // Odoo tourne en utilisateur non-root : droits ouverts sur le partage
          mountOptions: 'dir_mode=0777,file_mode=0777,mfsymlinks,nobrl'
        }
      ]
    }
  }
}

// ------------------------------------------------------------ Sorties
output acrName string = registry.name
output acrLoginServer string = registry.properties.loginServer
output keyVaultName string = keyVault.name
output containerAppName string = containerAppName
