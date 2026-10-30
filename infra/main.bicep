// =====================================================================
//  Odoo 19 + modules Invoice OCR sur Azure Container Apps (mode Express)
//
//  Deploiement en 2 phases (meme template) :
//    1. deployApp = false : registre, identite, journaux, environnement
//       -> on pousse ensuite l'image Docker dans le registre
//    2. deployApp = true  : l'application Odoo elle-meme
//
//  Securite :
//   - les mots de passe sont des parametres @secure() : saisis au
//     deploiement, jamais ecrits dans un fichier ni dans l'historique ;
//   - ils deviennent des "secrets" Container Apps (chiffres par Azure),
//     transmis au conteneur sans jamais apparaitre dans l'image ;
//   - l'image est tiree du registre par une identite managee :
//     le registre n'a ni compte admin ni mot de passe.
//
//  Le mode Express (impose sur cet abonnement) n'offre ni disque persistant
//  ni references Key Vault. Les pieces jointes sont donc stockees dans
//  PostgreSQL (module attachment_storage_env).
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
@description('Mot de passe PostgreSQL.')
param postgresPassword string

@secure()
@description('Mot de passe maitre Odoo : protege le gestionnaire de bases.')
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
var identityName = 'id-${appName}'
var logName = 'log-${appName}'
var environmentName = 'cae-${appName}-express'
var containerAppName = 'ca-${appName}'

// Role Azure integre "AcrPull"
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// ------------------------------------------------------------ Identite
// Identite managee de l'application : lui permet de tirer l'image du
// registre sans aucun mot de passe.
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
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
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
  ]
  properties: {
    managedEnvironmentId: environment.id
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
      // Secrets chiffres par Azure, alimentes par les parametres @secure()
      secrets: [
        {
          name: 'postgres-password'
          value: postgresPassword
        }
        {
          name: 'odoo-admin-password'
          value: odooAdminPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'odoo'
          image: '${registry.properties.loginServer}/${imageName}:${imageTag}'
          args: [
            'odoo'
            // Odoo est derriere le proxy HTTPS de Container Apps
            '--proxy-mode'
            // Charge au demarrage le module qui place les pieces jointes en base
            '--load=base,rpc,web,attachment_storage_env'
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
            {
              // Pas de disque persistant : pieces jointes dans PostgreSQL
              name: 'ODOO_ATTACHMENT_LOCATION'
              value: 'db'
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
        }
      ]
      // Une seule instance : Odoo n'est pas prevu ici pour plusieurs copies.
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
      }
    }
  }
}

// ------------------------------------------------------------ Sorties
output acrName string = registry.name
output acrLoginServer string = registry.properties.loginServer
output containerAppName string = containerAppName
