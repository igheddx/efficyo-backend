"""
Authentication (identity): establishing *who* is calling the API.

Cookie-backed sessions resolve to a persisted `User` and `AuthSession`.
Future enterprise providers (OIDC/SAML) will populate the same `User` fields
(`auth_provider`, `external_subject_id`) without changing authorization rules.

Authorization — what that user may do in an organization — lives in
`app.core.authz` and `OrgMembership`.
"""
