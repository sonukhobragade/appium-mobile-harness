# config.example

What the harness expects in `config/`, which is not checked in because its
contents describe one specific backend.

Copy this directory to `config/` and replace the contents.

    cp -r config.example config

## Files

| Path | Holds |
|---|---|
| `api_endpoints.json` | base URL env var, auth, and every endpoint fetched before a run |
| `api_responses/<endpoint>/schema.json` | `required_fields` and `required_deep_paths` |
| `api_responses/<endpoint>/<endpoint>_known_fields.json` | every field path seen so far, for new-field detection |
| `api_responses/<endpoint>/response.json` | fallback used when the API is unreachable |

`transforms_example.py` belongs at `src/utils/transforms/__init__.py`, not in
`config/`. It is here because it is the other half of the same job.

## How the pieces connect

```
api_endpoints.json      which endpoints to fetch, and how to authenticate
        │
        ▼
   live API call         or response.json if the API is unreachable
        │
        ▼
   schema.json           required_fields present? required_deep_paths resolve?
   known_fields.json     any field paths added or removed since last run?
        │
        ▼
   transforms            backend payload becomes expected screen text
        │
        ▼
   expected_screens      the fixture a test asks for
```

Validation runs before transforms on purpose. A response that changed shape
should be reported as drift, not silently turned into a wrong expected value.

## Credentials

Nothing here holds secrets. `base_url_env` and `token_env` name environment
variables; the values live in your environment.

    export API_BASE="https://api.internal.example.com"
    export API_TOKEN="..."

Use read-only credentials. An oracle only needs to read.
