from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


OPENAI_AUTH_BASE_URL = "https://auth.openai.com"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_PROVIDER = "openai-codex"
DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
DEVICE_CODE_DEFAULT_INTERVAL_SECONDS = 5
DEVICE_CODE_MIN_INTERVAL_SECONDS = 1


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str


RequestFn = Callable[[str, str, dict[str, str], bytes], HttpResponse]
SleepFn = Callable[[float], None]
NowFn = Callable[[], float]


@dataclass(frozen=True)
class DeviceCodePrompt:
    verification_url: str
    user_code: str
    expires_in_seconds: int


@dataclass(frozen=True)
class RequestedDeviceCode:
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: float


@dataclass(frozen=True)
class DeviceAuthorization:
    authorization_code: str
    code_verifier: str


@dataclass(frozen=True)
class OAuthCredential:
    provider: str
    type: str
    access_token: str
    refresh_token: str
    expires_at: float | None
    created_at: float
    updated_at: float
    email: str | None = None
    account_id: str | None = None
    chatgpt_plan_type: str | None = None
    profile_name: str | None = None
    origin: str = "device_code"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "OAuthCredential":
        provider = str(data.get("provider") or OPENAI_CODEX_PROVIDER)
        return cls(
            provider=provider,
            type=str(data.get("type") or "oauth"),
            access_token=str(data.get("access_token") or data.get("access") or ""),
            refresh_token=str(data.get("refresh_token") or data.get("refresh") or ""),
            expires_at=_optional_float(data.get("expires_at") or data.get("expires")),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            email=_optional_str(data.get("email")),
            account_id=_optional_str(data.get("account_id") or data.get("accountId")),
            chatgpt_plan_type=_optional_str(data.get("chatgpt_plan_type") or data.get("chatgptPlanType")),
            profile_name=_optional_str(data.get("profile_name") or data.get("profileName")),
            origin=str(data.get("origin") or "device_code"),
        )

    def is_expired(self, *, now: float | None = None, skew_seconds: int = 120) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (time.time() if now is None else now) + skew_seconds

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["access_token"] = redact_secret(self.access_token)
        data["refresh_token"] = redact_secret(self.refresh_token)
        data["expires_at_iso"] = format_epoch(self.expires_at)
        return data


@dataclass(frozen=True)
class AuthStatus:
    provider: str
    path: Path
    configured: bool
    valid: bool
    expired: bool
    expires_at: float | None = None
    email: str | None = None
    account_id: str | None = None
    chatgpt_plan_type: str | None = None
    profile_name: str | None = None
    detail: str = ""

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        data["expires_at_iso"] = format_epoch(self.expires_at)
        return data


class AuthStore:
    """Small local credential store for Open Earth auth profiles.

    The store intentionally keeps OAuth credentials outside project templates so
    they are not copied into Git repositories by accident.
    """

    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or earthswarm_home()).expanduser().resolve()
        self.auth_dir = self.home / "auth"

    def path_for(self, provider: str) -> Path:
        return self.auth_dir / f"{normalize_auth_provider(provider)}.json"

    def read(self, provider: str = OPENAI_CODEX_PROVIDER) -> OAuthCredential | None:
        path = self.path_for(provider)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"auth profile is not an object: {path}")
        credential = OAuthCredential.from_mapping(data)
        if not credential.access_token or not credential.refresh_token:
            raise ValueError(f"auth profile is missing token material: {path}")
        return credential

    def write(self, credential: OAuthCredential) -> Path:
        path = self.path_for(credential.provider)
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.auth_dir.chmod(0o700)
        except OSError:
            pass
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(asdict(credential), indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        temp_path.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def delete(self, provider: str = OPENAI_CODEX_PROVIDER) -> bool:
        path = self.path_for(provider)
        if not path.exists():
            return False
        path.unlink()
        return True

    def status(self, provider: str = OPENAI_CODEX_PROVIDER, *, now: float | None = None) -> AuthStatus:
        normalized = normalize_auth_provider(provider)
        path = self.path_for(normalized)
        if not path.exists():
            return AuthStatus(
                provider=normalized,
                path=path,
                configured=False,
                valid=False,
                expired=False,
                detail=f"not signed in; run `openearth auth login --provider {normalized}`",
            )
        try:
            credential = self.read(normalized)
        except Exception as exc:
            return AuthStatus(
                provider=normalized,
                path=path,
                configured=True,
                valid=False,
                expired=False,
                detail=f"credential could not be read: {exc}",
            )
        assert credential is not None
        expired = credential.is_expired(now=now, skew_seconds=0)
        return AuthStatus(
            provider=normalized,
            path=path,
            configured=True,
            valid=not expired,
            expired=expired,
            expires_at=credential.expires_at,
            email=credential.email,
            account_id=credential.account_id,
            chatgpt_plan_type=credential.chatgpt_plan_type,
            profile_name=credential.profile_name,
            detail="signed in" if not expired else "token expired; run `openearth auth refresh`",
        )


class OpenAICodexOAuthClient:
    """OpenAI Codex device-code OAuth client.

    This mirrors the lightweight OpenClaw/OpenSwarm-compatible credential shape,
    but it deliberately does not pretend these tokens are OpenAI Platform API
    keys. Runtime adapters must opt into this auth profile explicitly.
    """

    def __init__(
        self,
        *,
        base_url: str = OPENAI_AUTH_BASE_URL,
        client_id: str = OPENAI_CODEX_CLIENT_ID,
        request_fn: RequestFn | None = None,
        sleep_fn: SleepFn = time.sleep,
        now_fn: NowFn = time.time,
    ):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.request_fn = request_fn or _http_request
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn

    def request_device_code(self) -> RequestedDeviceCode:
        response = self._post_json(
            "/api/accounts/deviceauth/usercode",
            {"client_id": self.client_id},
        )
        if response.status == 404:
            raise RuntimeError("OpenAI Codex device-code login is not enabled for this endpoint.")
        if response.status >= 400:
            raise RuntimeError(_format_oauth_error("OpenAI device code request failed", response))
        body = _parse_json_object(response.body)
        device_auth_id = _optional_str(body.get("device_auth_id"))
        user_code = _optional_str(body.get("user_code")) or _optional_str(body.get("usercode"))
        if not device_auth_id or not user_code:
            raise RuntimeError("OpenAI device code response was missing device_auth_id or user_code.")
        return RequestedDeviceCode(
            device_auth_id=device_auth_id,
            user_code=user_code,
            verification_url=f"{self.base_url}/codex/device",
            interval_seconds=_positive_seconds(body.get("interval")) or DEVICE_CODE_DEFAULT_INTERVAL_SECONDS,
        )

    def poll_device_code(
        self,
        requested: RequestedDeviceCode,
        *,
        timeout_seconds: int = DEVICE_CODE_TIMEOUT_SECONDS,
    ) -> DeviceAuthorization:
        deadline = self.now_fn() + timeout_seconds
        while self.now_fn() < deadline:
            response = self._post_json(
                "/api/accounts/deviceauth/token",
                {
                    "device_auth_id": requested.device_auth_id,
                    "user_code": requested.user_code,
                },
            )
            if response.status < 400:
                body = _parse_json_object(response.body)
                authorization_code = _optional_str(body.get("authorization_code"))
                code_verifier = _optional_str(body.get("code_verifier"))
                if not authorization_code or not code_verifier:
                    raise RuntimeError("OpenAI device authorization response was missing exchange fields.")
                return DeviceAuthorization(authorization_code=authorization_code, code_verifier=code_verifier)
            if response.status in {403, 404}:
                delay = min(
                    max(requested.interval_seconds, DEVICE_CODE_MIN_INTERVAL_SECONDS),
                    max(0.0, deadline - self.now_fn()),
                )
                if delay <= 0:
                    break
                self.sleep_fn(delay)
                continue
            raise RuntimeError(_format_oauth_error("OpenAI device authorization failed", response))
        raise RuntimeError("OpenAI device authorization timed out.")

    def exchange_device_authorization(self, authorization: DeviceAuthorization) -> OAuthCredential:
        response = self._post_form(
            "/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": authorization.authorization_code,
                "redirect_uri": f"{self.base_url}/deviceauth/callback",
                "client_id": self.client_id,
                "code_verifier": authorization.code_verifier,
            },
        )
        if response.status >= 400:
            raise RuntimeError(_format_oauth_error("OpenAI device token exchange failed", response))
        return self._credential_from_token_response(response.body, origin="device_code")

    def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        response = self._post_form(
            "/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": credential.refresh_token,
                "client_id": self.client_id,
            },
        )
        if response.status >= 400:
            raise RuntimeError(_format_oauth_error("OpenAI Codex token refresh failed", response))
        refreshed = self._credential_from_token_response(response.body, origin=credential.origin)
        return OAuthCredential(
            provider=credential.provider,
            type=credential.type,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or credential.refresh_token,
            expires_at=refreshed.expires_at,
            created_at=credential.created_at,
            updated_at=self.now_fn(),
            email=refreshed.email or credential.email,
            account_id=refreshed.account_id or credential.account_id,
            chatgpt_plan_type=refreshed.chatgpt_plan_type or credential.chatgpt_plan_type,
            profile_name=refreshed.profile_name or credential.profile_name,
            origin=credential.origin,
        )

    def _credential_from_token_response(self, body_text: str, *, origin: str) -> OAuthCredential:
        body = _parse_json_object(body_text)
        access_token = _optional_str(body.get("access_token"))
        refresh_token = _optional_str(body.get("refresh_token"))
        if not access_token or not refresh_token:
            raise RuntimeError("OpenAI token response did not include OAuth access and refresh tokens.")
        expires_in = _positive_seconds(body.get("expires_in"))
        expires_at = self.now_fn() + expires_in if expires_in is not None else resolve_jwt_expiry(access_token)
        identity = resolve_codex_auth_identity(access_token, email=_optional_str(body.get("email")))
        now = self.now_fn()
        return OAuthCredential(
            provider=OPENAI_CODEX_PROVIDER,
            type="oauth",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            email=identity.get("email"),
            account_id=identity.get("account_id"),
            chatgpt_plan_type=identity.get("chatgpt_plan_type"),
            profile_name=identity.get("profile_name"),
            origin=origin,
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> HttpResponse:
        return self.request_fn(
            f"{self.base_url}{path}",
            "POST",
            _headers("application/json"),
            json.dumps(payload).encode("utf-8"),
        )

    def _post_form(self, path: str, payload: dict[str, str]) -> HttpResponse:
        return self.request_fn(
            f"{self.base_url}{path}",
            "POST",
            _headers("application/x-www-form-urlencoded"),
            urllib.parse.urlencode(payload).encode("utf-8"),
        )


def login_openai_codex_device_code(
    *,
    store: AuthStore | None = None,
    client: OpenAICodexOAuthClient | None = None,
    open_browser: bool = True,
    timeout_seconds: int = DEVICE_CODE_TIMEOUT_SECONDS,
    on_verification: Callable[[DeviceCodePrompt], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> OAuthCredential:
    store = store or AuthStore()
    client = client or OpenAICodexOAuthClient()
    on_progress = on_progress or (lambda message: None)
    on_progress("Requesting ChatGPT device code")
    requested = client.request_device_code()
    prompt = DeviceCodePrompt(
        verification_url=requested.verification_url,
        user_code=requested.user_code,
        expires_in_seconds=timeout_seconds,
    )
    if on_verification:
        on_verification(prompt)
    if open_browser:
        webbrowser.open(prompt.verification_url)
    on_progress("Waiting for browser authorization")
    authorization = client.poll_device_code(requested, timeout_seconds=timeout_seconds)
    on_progress("Exchanging authorization")
    credential = client.exchange_device_authorization(authorization)
    store.write(credential)
    on_progress("Saved OpenAI Codex OAuth profile")
    return credential


def refresh_openai_codex(
    *,
    store: AuthStore | None = None,
    client: OpenAICodexOAuthClient | None = None,
) -> OAuthCredential:
    store = store or AuthStore()
    client = client or OpenAICodexOAuthClient()
    credential = store.read(OPENAI_CODEX_PROVIDER)
    if credential is None:
        raise RuntimeError("No OpenAI Codex OAuth profile found. Run: openearth auth login --provider openai-codex")
    refreshed = client.refresh(credential)
    store.write(refreshed)
    return refreshed


def ensure_openai_codex_auth(store: AuthStore | None = None, *, refresh: bool = True) -> OAuthCredential:
    store = store or AuthStore()
    credential = store.read(OPENAI_CODEX_PROVIDER)
    if credential is None:
        raise RuntimeError("OpenAI Codex OAuth is not configured. Run: openearth auth login --provider openai-codex")
    if refresh and credential.is_expired(skew_seconds=300):
        return refresh_openai_codex(store=store)
    return credential


def earthswarm_home() -> Path:
    configured = os.getenv("OPENEARTH_HOME") or os.getenv("EARTHSWARM_HOME")
    if configured:
        return Path(configured)
    current = Path.home() / ".openearth"
    legacy = Path.home() / ".earthswarm"
    return legacy if legacy.exists() and not current.exists() else current


def normalize_auth_provider(provider: str | None) -> str:
    value = (provider or OPENAI_CODEX_PROVIDER).strip().lower().replace("_", "-")
    if value in {"openai", "chatgpt", "codex", "openai-codex-oauth"}:
        return OPENAI_CODEX_PROVIDER
    return value


def redact_secret(value: str, *, prefix: int = 6, suffix: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= prefix + suffix:
        return "***"
    return f"{value[:prefix]}...{value[-suffix:]}"


def format_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def resolve_jwt_expiry(access_token: str) -> float | None:
    payload = decode_jwt_payload(access_token)
    exp = payload.get("exp") if payload else None
    if isinstance(exp, (int, float)) and exp > 0:
        return float(exp)
    if isinstance(exp, str) and exp.strip().isdigit():
        return float(exp.strip())
    return None


def resolve_codex_auth_identity(access_token: str, *, email: str | None = None) -> dict[str, str]:
    payload = decode_jwt_payload(access_token)
    if not payload:
        return {"email": email} if email else {}
    auth = payload.get("https://api.openai.com/auth")
    profile = payload.get("https://api.openai.com/profile")
    if not isinstance(auth, dict):
        auth = {}
    if not isinstance(profile, dict):
        profile = {}
    resolved_email = _optional_str(profile.get("email")) or email
    account_id = _optional_str(auth.get("chatgpt_account_id"))
    plan = _optional_str(auth.get("chatgpt_plan_type"))
    subject = (
        _optional_str(auth.get("chatgpt_account_user_id"))
        or _optional_str(auth.get("chatgpt_user_id"))
        or _optional_str(auth.get("user_id"))
        or _optional_str(payload.get("sub"))
    )
    result: dict[str, str] = {}
    if resolved_email:
        result["email"] = resolved_email
        result["profile_name"] = resolved_email
    elif subject:
        result["profile_name"] = "id-" + base64.urlsafe_b64encode(subject.encode("utf-8")).decode("ascii").rstrip("=")
    if account_id:
        result["account_id"] = account_id
    if plan:
        result["chatgpt_plan_type"] = plan
    return result


def decode_jwt_payload(access_token: str) -> dict[str, Any] | None:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else None
    except Exception:
        return None


def _headers(content_type: str) -> dict[str, str]:
    version = (os.getenv("OPENEARTH_VERSION") or os.getenv("EARTHSWARM_VERSION", "")).strip()
    user_agent = f"openearth/{version}" if version else "openearth"
    return {
        "Content-Type": content_type,
        "originator": "openearth",
        "User-Agent": user_agent,
    }


def _http_request(url: str, method: str, headers: dict[str, str], body: bytes) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return HttpResponse(status=response.status, body=response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return HttpResponse(status=exc.code, body=exc.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error while contacting OpenAI auth: {exc}") from exc


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OAuth endpoint returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("OAuth endpoint returned a non-object JSON payload.")
    return value


def _format_oauth_error(prefix: str, response: HttpResponse) -> str:
    try:
        body = json.loads(response.body)
    except json.JSONDecodeError:
        body = None
    if isinstance(body, dict):
        error = _optional_str(body.get("error"))
        description = _optional_str(body.get("error_description"))
        if error and description:
            return f"{prefix}: {error} ({description})"
        if error:
            return f"{prefix}: {error}"
    safe_body = " ".join(response.body.split())[:500]
    if safe_body:
        return f"{prefix}: HTTP {response.status} {safe_body}"
    return f"{prefix}: HTTP {response.status}"


def _positive_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str) and value.strip().isdigit():
        seconds = float(value.strip())
        return seconds if seconds > 0 else None
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None
