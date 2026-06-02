from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    provider: str
    auth: str
    runtime: str
    default_model: str
    role: str
    env: str
    base_url: str
    notes: str


class ProviderRouter:
    """Read provider routes without importing heavy live backends."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else self.root / "providers.yaml"

    def profiles(self) -> list[ProviderProfile]:
        data = self._read()
        return [_profile_from_mapping(item) for item in data.get("profiles") or [] if isinstance(item, dict)]

    def default_provider(self) -> str:
        return str(self._read().get("default_provider") or "")

    def route_for_role(self, role: str) -> ProviderProfile | None:
        normalized = role.strip().lower().replace("-", "_")
        profiles = self.profiles()
        for profile in profiles:
            if profile.role == normalized:
                return profile
        default = self.default_provider()
        for profile in profiles:
            if profile.id == default:
                return profile
        return profiles[0] if profiles else None

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for profile in self.profiles():
            available, detail = self._profile_status(profile)
            rows.append(
                {
                    "id": profile.id,
                    "provider": profile.provider,
                    "runtime": profile.runtime,
                    "auth": profile.auth,
                    "role": profile.role,
                    "default_model": profile.default_model,
                    "base_url": profile.base_url,
                    "available": available,
                    "detail": detail,
                    "notes": profile.notes,
                }
            )
        return rows

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.path.exists():
            return errors
        try:
            profiles = self.profiles()
        except Exception as exc:
            return [f"{self.path}: {exc}"]
        ids: set[str] = set()
        for profile in profiles:
            if not profile.id:
                errors.append(f"{self.path}: provider profile missing id")
            if profile.id in ids:
                errors.append(f"{self.path}: duplicate provider profile {profile.id}")
            ids.add(profile.id)
            if not profile.runtime:
                errors.append(f"provider {profile.id}: missing runtime")
            if profile.auth == "env" and not profile.env:
                errors.append(f"provider {profile.id}: auth env requires env")
        default = self.default_provider()
        if default and default not in ids:
            errors.append(f"{self.path}: default_provider {default} is not a profile id")
        return errors

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "profiles": []}
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("providers.yaml must contain a mapping")
        return data

    def _profile_status(self, profile: ProviderProfile) -> tuple[bool, str]:
        if profile.runtime == "codex_cli":
            from .codex_cli import codex_command_status

            return codex_command_status()
        if profile.provider == "ollama" or profile.runtime in {"ollama", "openai_compatible_ollama"}:
            from .ollama_runner import ollama_status

            return ollama_status()
        if profile.provider == "nvidia":
            from .nvidia_runner import nvidia_status

            return nvidia_status()
        if profile.auth == "env":
            if not profile.env:
                return False, "missing env variable name in provider profile"
            return (bool(os.getenv(profile.env)), f"{profile.env} is {'set' if os.getenv(profile.env) else 'not set'}")
        if profile.auth in {"none", "oauth", "codex_cli_login"}:
            return True, f"{profile.auth} route configured"
        return False, f"unknown auth mode: {profile.auth}"


def _profile_from_mapping(data: dict[str, Any]) -> ProviderProfile:
    return ProviderProfile(
        id=str(data.get("id") or "").strip(),
        provider=str(data.get("provider") or "").strip(),
        auth=str(data.get("auth") or "").strip(),
        runtime=str(data.get("runtime") or "").strip(),
        default_model=str(data.get("default_model") or "").strip(),
        role=str(data.get("role") or "").strip().lower().replace("-", "_"),
        env=str(data.get("env") or "").strip(),
        base_url=str(data.get("base_url") or "").strip(),
        notes=str(data.get("notes") or "").strip(),
    )
