# Configure exercise OIDC

Use your centrally managed identity realm, with authorization-code flow, PKCE
S256, exact callback URI and an ID-token `groups` claim. This integration accepts
RS256-signed ID tokens and binds each account to the immutable issuer plus `sub`.
Email changes or reuse never link two accounts. Existing local users stay separate.

Set these non-secret values in the service environment:

```text
RANGE42_OIDC_ISSUER=https://login.example.test/realms/exercises
RANGE42_OIDC_CLIENT_ID=table42
RANGE42_OIDC_REDIRECT_URI=https://table42.example.test/api/oidc/callback
RANGE42_OIDC_REQUIRED_GROUP=range42-users
RANGE42_OIDC_CLIENT_SECRET_FILE=/run/secrets/table42-client
RANGE42_OIDC_SESSION_SECRET_FILE=/run/secrets/table42-cookie
RANGE42_SECURE_COOKIE=true
RANGE42_DATA_DIR=/data
```

OIDC always uses Secure application-session cookies, including local recovery
sessions. Without OIDC, use `RANGE42_SECURE_COOKIE=true` for HTTPS hosting.

Provide a confidential client secret and a random cookie-signing secret of at
least32characters through separate files, owned by the running account and
mode0400or0600. Never place secrets in Git or an exercise definition. All endpoint,
client and secret settings must be present together; partial configuration fails
startup. A restart is needed after secret-file changes. Rotating the cookie secret
invalidates pending login flows, rather than the separate application sessions.

`RANGE42_OIDC_ADMIN_GROUP` is empty by default. OIDC users then receive no global
administrator role. Set an explicit separately managed group only if that global
privilege is intended; exercise directors use per-exercise membership instead.
Accounts are centrally managed in the IdP, through administrator creation or
controlled invitations. Temporary accounts may have unverified email addresses;
the callback does not require email or `email_verified: true`. Table42 validates
the signed issuer/subject and enrollment group, without using email to link
accounts or grant exercise membership. Verification is an optional onboarding
or recovery policy in the central identity service.

## Membership and offboarding

Administrators can preprovision a subject using `POST /api/oidc/accounts` with
`issuer`, `subject`, `name`; then use the existing exercise groups/members API
to assign that returned user ID. Mutation requests require the administrator's
current session and `X-CSRF-Token`. Provisioning is idempotent for issuer/subject
and cannot reactivate an account. Do not use email as the subject.

The People page exposes **Disable access / Enable access** for OIDC users.
`POST /api/users/{id}/access` with `{"enabled": false}` immediately revokes all
Table42 sessions and blocks later login. It retains the user record, attribution,
memberships and evidence. External passwords are managed in the IdP; local reset
and password-login paths reject externally managed accounts.

Removing an IdP group prevents future login but does not itself invalidate an
existing Table42 session, whose maximum age remains12hours. Until lifecycle
reconciliation is implemented, offboarding must also disable the Table42 account,
revoke/expire the VPN nodes and remove chat sessions/room membership. Restoring
Table42 access requires an explicit administrator action.

## Verify and recover

Test discovery/TLS, actual selected-provider PKCE login, shared-service access
and two-team isolation in a browser before enrolling a real roster. Validate denial without
the required group, account disablement during an active session and preservation
of authored evidence. The automated fixture tests cover issuer/audience/expiry,
nonce, browser state, callback replay, email reuse and privilege separation.

Back up the data directory before upgrading an existing database. The schema adds
OIDC account and pending-flow tables without deleting existing users or exercises.
Application rollback does not remove those tables. Disable OIDC by removing the
complete OIDC setting set and restarting; the local recovery administrator remains
usable. External users require OIDC again to sign in, so this is a recovery measure,
not an exercise migration or data deletion procedure.

## Application-owned container

Build the project's Dockerfile, qualify its immutable image ID and run it behind
verified HTTPS. The image runs as UID10001 with `/data` as its persistent state
path; it supports a read-only container root with writable `/tmp`. Mount private
OIDC/cookie secret files readable by that UID. Initialize the local recovery
administrator before participant access. Back up the data volume and identity
configuration before updates. Deployment-specific endpoints, identity groups and
secret files remain installation inputs outside this repository.
