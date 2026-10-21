"""SQLite-backed OAuth 2.1 authorization-code provider with PKCE."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    OAuthToken,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SQLiteOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """The browser consent page is owned by the relay, while token data stays in SQLite."""

    def __init__(self, repository, public_base_url: str, scope: str = "health:read"):
        self.repo = repository
        self.public_base_url = public_base_url.rstrip("/")
        self.scope = scope

    def _connect(self):
        return self.repo.connect()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        con = self._connect()
        try:
            row = con.execute("SELECT client_json FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
            return OAuthClientInformationFull.model_validate_json(row[0]) if row else None
        finally:
            con.close()

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO oauth_clients(client_id,client_json,created_at) VALUES(?,?,?)",
                (client_info.client_id, client_info.model_dump_json(), int(time.time())),
            )
            con.commit()
        finally:
            con.close()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(32)
        data = {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "params": params.model_dump(mode="json"),
            "created_at": int(time.time()),
        }
        con = self._connect()
        try:
            now = int(time.time())
            con.execute("DELETE FROM oauth_pending WHERE expires_at<?", (now,))
            con.execute("DELETE FROM oauth_codes WHERE expires_at<?", (now,))
            con.execute("DELETE FROM oauth_access_tokens WHERE expires_at<?", (now,))
            con.execute("DELETE FROM oauth_refresh_tokens WHERE expires_at<?", (now,))
            con.execute(
                "INSERT INTO oauth_pending(request_id,request_json,expires_at) VALUES(?,?,?)",
                (request_id, json.dumps(data, ensure_ascii=False), int(time.time()) + 600),
            )
            con.commit()
        finally:
            con.close()
        return f"{self.public_base_url}/consent/{request_id}"

    def pending_request(self, request_id: str) -> dict[str, Any] | None:
        con = self._connect()
        try:
            row = con.execute("SELECT request_json,expires_at FROM oauth_pending WHERE request_id=?", (request_id,)).fetchone()
            if not row or row[1] < int(time.time()):
                return None
            data = json.loads(row[0])
            params = AuthorizationParams.model_validate(data["params"])
            return {
                "request_id": request_id,
                "client_id": data["client_id"],
                "client_name": data.get("client_name") or data["client_id"],
                "scopes": params.scopes or [self.scope],
                "state": params.state,
                "redirect_uri": str(params.redirect_uri),
                "expires_at": row[1],
            }
        finally:
            con.close()

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        con = self._connect()
        try:
            row = con.execute("SELECT code_json,expires_at FROM oauth_codes WHERE code=?", (_hash(authorization_code),)).fetchone()
            if not row or row[1] < int(time.time()):
                return None
            data = json.loads(row[0])
            code = AuthorizationCode.model_validate(data | {"code": authorization_code})
            return code if code.client_id == client.client_id else None
        finally:
            con.close()

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            code_hash = _hash(authorization_code.code)
            row = con.execute("SELECT expires_at FROM oauth_codes WHERE code=?", (code_hash,)).fetchone()
            if not row or row[0] < int(time.time()):
                con.rollback()
                raise TokenError("invalid_grant", "authorization code expired or already used")
            con.execute("DELETE FROM oauth_codes WHERE code=?", (code_hash,))
            con.commit()
        finally:
            con.close()
        return await self._issue_tokens(client.client_id, authorization_code.scopes, authorization_code.resource, authorization_code.subject)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT token_json,expires_at,revoked FROM oauth_refresh_tokens WHERE token_hash=?",
                (_hash(refresh_token),),
            ).fetchone()
            if not row or row[2] or row[1] < int(time.time()):
                return None
            token = RefreshToken.model_validate(json.loads(row[0]) | {"token": refresh_token})
            return token if token.client_id == client.client_id else None
        finally:
            con.close()

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if any(scope not in refresh_token.scopes for scope in scopes):
            raise TokenError("invalid_scope", "requested scopes exceed the original grant")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            token_hash = _hash(refresh_token.token)
            row = con.execute("SELECT token_json,expires_at,revoked FROM oauth_refresh_tokens WHERE token_hash=?", (token_hash,)).fetchone()
            if not row or row[2] or row[1] < int(time.time()):
                con.rollback()
                raise TokenError("invalid_grant", "refresh token expired or already used")
            stored = json.loads(row[0])
            if stored.get("client_id") != client.client_id:
                con.rollback()
                raise TokenError("invalid_grant", "refresh token belongs to another client")
            con.execute("UPDATE oauth_refresh_tokens SET revoked=1 WHERE token_hash=?", (token_hash,))
            con.commit()
        finally:
            con.close()
        return await self._issue_tokens(client.client_id, scopes or refresh_token.scopes, refresh_token.resource, refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT token_json,expires_at,revoked FROM oauth_access_tokens WHERE token_hash=?",
                (_hash(token),),
            ).fetchone()
            if not row or row[2] or row[1] < int(time.time()):
                return None
            return AccessToken.model_validate(json.loads(row[0]) | {"token": token})
        finally:
            con.close()

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        table = "oauth_access_tokens" if isinstance(token, AccessToken) else "oauth_refresh_tokens"
        con = self._connect()
        try:
            row = con.execute(f"SELECT token_json FROM {table} WHERE token_hash=?", (_hash(token.token),)).fetchone()
            if not row:
                return
            client_id = json.loads(row[0]).get("client_id")
            if not client_id:
                return
            # A client grant is the revocation unit: revoke both token types for
            # that client, including rotated refresh tokens.
            for token_table in ("oauth_access_tokens", "oauth_refresh_tokens"):
                rows = con.execute(f"SELECT token_hash,token_json FROM {token_table} WHERE revoked=0").fetchall()
                hashes = [item["token_hash"] for item in rows if json.loads(item["token_json"]).get("client_id") == client_id]
                con.executemany(f"UPDATE {token_table} SET revoked=1 WHERE token_hash=?", [(value,) for value in hashes])
            con.commit()
        finally:
            con.close()

    async def _issue_tokens(self, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        access_value = secrets.token_urlsafe(48)
        refresh_value = secrets.token_urlsafe(48)
        now = int(time.time())
        access = AccessToken(token=access_value, client_id=client_id, scopes=scopes, expires_at=now + 900, resource=resource, subject=subject)
        refresh = RefreshToken(token=refresh_value, client_id=client_id, scopes=scopes, expires_at=now + 30 * 86400, resource=resource, subject=subject)
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO oauth_access_tokens(token_hash,token_json,expires_at,revoked) VALUES(?,?,?,0)",
                (_hash(access_value), access.model_dump_json(exclude={"token"}), now + 900),
            )
            con.execute(
                "INSERT INTO oauth_refresh_tokens(token_hash,token_json,expires_at,revoked) VALUES(?,?,?,0)",
                (_hash(refresh_value), refresh.model_dump_json(exclude={"token"}), now + 30 * 86400),
            )
            con.commit()
        finally:
            con.close()
        return OAuthToken(access_token=access_value, expires_in=900, scope=" ".join(scopes), refresh_token=refresh_value)

    async def complete_authorization(self, request_id: str, subject: str) -> str:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT request_json,expires_at FROM oauth_pending WHERE request_id=?", (request_id,)).fetchone()
            if not row or row[1] < int(time.time()):
                con.rollback()
                raise ValueError("authorization request expired")
            con.execute("DELETE FROM oauth_pending WHERE request_id=?", (request_id,))
            data = json.loads(row[0])
            params = AuthorizationParams.model_validate(data["params"])
            code_value = secrets.token_urlsafe(32)
            code = AuthorizationCode(
                code=code_value,
                scopes=params.scopes or [self.scope],
                expires_at=time.time() + 300,
                client_id=data["client_id"],
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource,
                subject=subject,
            )
            con.execute(
                "INSERT INTO oauth_codes(code,code_json,expires_at) VALUES(?,?,?)",
                (_hash(code_value), code.model_dump_json(exclude={"code"}), int(code.expires_at)),
            )
            con.commit()
        finally:
            con.close()
        return construct_redirect_uri(str(params.redirect_uri), code=code_value, state=params.state)

    async def deny_authorization(self, request_id: str) -> str:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT request_json,expires_at FROM oauth_pending WHERE request_id=?", (request_id,)).fetchone()
            if not row or row[1] < int(time.time()):
                con.rollback()
                raise ValueError("authorization request expired")
            con.execute("DELETE FROM oauth_pending WHERE request_id=?", (request_id,))
            data = json.loads(row[0])
            params = AuthorizationParams.model_validate(data["params"])
            con.commit()
        finally:
            con.close()
        return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)