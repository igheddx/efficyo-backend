"""AWS provider collectors.

Collectors are narrow, deterministic workers responsible for:
- authenticate / assume role
- fetch provider data
- normalize and persist raw/normalized snapshots or intermediate records

They must not score recommendations or produce user-facing narratives.
"""

