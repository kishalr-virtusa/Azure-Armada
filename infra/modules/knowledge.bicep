// Black box knowledge index (RAG): container `knowledge` in the console's Cosmos DB database, with built-in vector
// search (Azure Cosmos DB for NoSQL). One document per chunk of unstructured DR text (runbook, reports, diagnoses,
// incident timelines, Autopilot findings), embedded with text-embedding-3-small (embeddings.bicep); written and
// searched by apps/dr-console/dr_agent/knowledge.py.
//   - partition key /pk = source type (runbook, report, diagnosis, ...): a source-filtered search is one partition
//   - vectorEmbeddingPolicy: /embedding, float32, 1536 dimensions, cosine
//   - vector index quantizedFlat: exact-recall brute force over compressed vectors, supports 1536 dims (flat stops
//     at 505) and is meant for small corpora like this one (hundreds of chunks); diskANN pays off from ~50k vectors.
//     Both run on provisioned (shared database) throughput; neither needs serverless.
//   - /embedding/* is excluded from the regular range index (no write RUs spent indexing 1536 numbers)
//   - its own 400 RU/s (the minimum): Cosmos DB refuses vector indexes on shared-throughput containers ("Vector
//     Indexing is not supported for shared throughput offer"). 400 RU/s x 2 regions on top of the free 1000 RU/s
//     is about $1.54/day (list price $0.008 per 100 RU/s per hour per region).
// Needs the account capability EnableNoSQLVectorSearch first (cosmos.bicep sets it; deploy-rag.sh enables it on the
// existing account). The vector policy can only be set when the container is created.

@description('Cosmos DB account name (dra-cosmos-<suffix>)')
param accountName string

@description('Database name')
param databaseName string = 'dra'

@description('Dedicated throughput (RU/s) for the knowledge container; 400 is the minimum')
param throughput int = 400

@description('Embedding dimensions (text-embedding-3-small: 1536)')
param dimensions int = 1536

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' existing = {
  parent: account
  name: databaseName
}

resource knowledge 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'knowledge'
  properties: {
    resource: {
      id: 'knowledge'
      partitionKey: {
        paths: [
          '/pk'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/embedding/*'
          }
          {
            path: '/text/?'
          }
          {
            path: '/"_etag"/?'
          }
        ]
        vectorIndexes: [
          {
            path: '/embedding'
            type: 'quantizedFlat'
          }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            dimensions: dimensions
            distanceFunction: 'cosine'
          }
        ]
      }
    }
    options: {
      throughput: throughput
    }
  }
}

output containerName string = knowledge.name
