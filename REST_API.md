# Technical Specification: Production Troubleshooting Assistant REST API

## 1. Overview
The Production Troubleshooting Assistant exposes a versioned REST API under the `/api/v1` prefix, hosted natively on port `8030` [22]. The API facilitates structured data ingestion and retrieval for equipment failure logs, abstracting the underlying Retrieval-Augmented Generation (RAG) pipeline and vector storage mechanisms [26]. Backend automation systems interacting with this interface should utilize `application/json` payloads to ensure strict schema validation and deterministic parsing [8].

## 2. Endpoint: `POST /api/v1/records` (Data Ingestion)
This endpoint accepts structured failure profiles, automatically computes semantic embeddings via the local Ollama instance, and persists the record to the PostgreSQL database [22].

### Request Schema (`FailureLogCreate`)
The payload must conform to the following JSON structure. Fields marked as required will trigger a `422 Unprocessable Entity` response if omitted or malformed [8].

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `equipment_type` | `string` | Yes | 1–255 chars | Categorization string (e.g., "Product", "Production Equipment") [22]. |
| `equipment_name` | `string` | Yes | 1–255 chars | Explicit asset identifier [22]. |
| `failure_description` | `string` | Yes | Min 1 char | Unbound text block used for semantic vector generation [22]. |
| `solution_description` | `string` | Yes | Min 1 char | Root resolution text injected into future LLM context windows [22]. |
| `symptoms` | `array[string]` | No | - | Optional list of observed failure indicators [1]. |
| `cause` | `string` | No | Max 255 chars | Optional root cause analysis label [1]. |
| `internal_ID` | `string` | No | Max 100 chars | Optional legacy indexing identifier used for fallback retrieval logic [1]. |

### Response Behavior
- **Success (`201 Created`)**: Returns the newly created record object. High-dimensional `embeddings` are intentionally omitted from the response payload to conserve bandwidth [8].
- **Failure (`422` / `500`)**: Returns a JSON `detail` object outlining Pydantic validation errors or internal database/LLM service failures [8].

## 3. Endpoint: `GET /api/v1/records` (Data Retrieval)
This endpoint provides paginated access to historical failure logs. It is optimized for bulk data extraction and audit trails, deliberately excluding vector data to maintain lightweight JSON serialization [1].

### Query Parameters
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | `integer` | `0` | Number of records to bypass for pagination [2]. |
| `limit` | `integer` | `50` | Maximum number of records returned per request [2]. |

### Response Schema (`FailureLogResponse`)
The endpoint returns a JSON array of objects conforming to the following structure:

| Field | Type | Description |
|-------|------|-------------|
| `key_ID` | `integer` | Auto-incrementing primary key [1]. |
| `equipment_type` | `string` | Asset category [1]. |
| `equipment_name` | `string` | Asset identifier [1]. |
| `failure_description` | `string` | Original failure narrative [1]. |
| `solution_description` | `string` | Documented resolution steps [1]. |
| `symptoms` | `array[string]` \| `null` | Parsed symptom list [1]. |
| `cause` | `string` \| `null` | Root cause label [1]. |
| `internal_ID` | `string` \| `null` | Legacy identifier [1]. |
| `created_at` | `datetime` | ISO 8601 timestamp of ingestion [1]. |

## 4. Backend Automation Implementation Guidelines

### 4.1 Connection & Client Configuration
Automation scripts should utilize an asynchronous HTTP client (e.g., `httpx`) with persistent connection pooling to minimize TCP handshake overhead during sequential batch operations [7]. The base URL should be dynamically resolved via environment variables to support containerized or remote deployments [29].

```python
# Recommended client configuration pattern
client = httpx.AsyncClient(
    base_url="http://<host>:8030/api/v1",
    timeout=httpx.Timeout(connect=10.0, read=60.0),
    limits=httpx.Limits(max_connections=20)
)
```

### 4.2 Payload Validation & Serialization
Before transmitting data to the `POST` endpoint, automation logic should validate payloads against the `FailureLogCreate` schema locally. This prevents unnecessary network round-trips for malformed data and aligns with the server's strict Pydantic v2 enforcement [1]. Ensure that `symptoms` are passed as native JSON arrays rather than comma-delimited strings, as the REST API expects structured JSON mirroring the ingestion form schema [22].

### 4.3 Pagination Strategy
When extracting historical data via `GET`, implement a cursor-based or offset-based loop using the `skip` and `limit` parameters [2]. Terminate the extraction loop when the returned array length is strictly less than the requested `limit`, indicating the end of the dataset.

### 4.4 Error Handling & Retry Logic
- **Validation Errors (`422`)**: Parse the `detail` array to identify missing or malformed fields. Correct the payload locally before retrying.
- **Service Unavailable (`500` / `503`)**: Implement exponential backoff for transient failures, particularly when the Ollama embedding service or PostgreSQL connection pool experiences temporary saturation [7].
- **Idempotency**: The current schema does not enforce strict idempotency keys. Automation systems should track ingested `key_ID` values or hash `failure_description` + `equipment_name` pairs locally to prevent duplicate record creation during retry cycles.

## 5. Reference Architecture Notes
The API operates as a thin abstraction layer over a hybrid RAG pipeline. Upon successful `POST` ingestion, the system synchronously generates a 768-dimensional vector using `embeddinggemma:300m` and commits it to a PostgreSQL table indexed with HNSW for cosine similarity operations [26]. Automation systems do not need to manage vectorization manually; the API handles embedding generation, database persistence, and index maintenance transparently [22].