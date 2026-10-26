from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx

from .config import BACKEND_DIR

BASE_URL = os.getenv("MELOPET_VALIDATION_URL", "http://127.0.0.1:8010").rstrip("/")
SAMPLE_FILE = BACKEND_DIR / "samples" / "demo-playlist.csv"
SESSION_FILE = BACKEND_DIR / "runtime" / "validation" / "demo-session.json"
TERMINAL_STATES = {
    "PROFILE_READY",
    "CANDIDATES_READY",
    "SAFE_FALLBACK_READY",
    "FINAL_FAILED",
    "CANCELLED",
}


class DemoError(RuntimeError):
    pass


def _data(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise DemoError(f"{response.request.method} {response.request.url}: 返回了非 JSON 响应") from exc
    if response.is_error or payload.get("status") != "ok":
        message = payload.get("safeMessage") or response.text[:300]
        raise DemoError(
            f"{response.request.method} {response.request.url}: "
            f"HTTP {response.status_code}, {payload.get('errorCode', 'UNKNOWN_ERROR')}, {message}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DemoError(f"{response.request.method} {response.request.url}: 缺少 data 对象")
    return data


def _write_headers(token: str, action: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": f"validation-{action}-{uuid.uuid4().hex}",
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wait_for_task(client: httpx.Client, token: str, task_id: str, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    last_status = "UNKNOWN"
    while time.monotonic() < deadline:
        task = _data(client.get(f"/v1/tasks/{task_id}", headers=_auth_headers(token)))
        last_status = str(task.get("status"))
        if last_status == expected:
            return task
        if last_status in TERMINAL_STATES:
            raise DemoError(f"任务 {task_id} 未到达 {expected}，终态为 {last_status}")
        time.sleep(0.25)
    raise DemoError(f"等待任务 {task_id} 超时，最后状态为 {last_status}；请检查 Worker 日志")


def run_demo() -> dict[str, Any]:
    playlist_text = SAMPLE_FILE.read_text(encoding="utf-8")
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        health = _data(client.get("/healthz"))
        capability = _data(client.post("/v1/capabilities/anonymous"))
        token = str(capability["capabilityToken"])

        imported = _data(
            client.post(
                "/v1/playlist-imports",
                headers=_write_headers(token, "import"),
                json={"text": playlist_text, "consentExternalProcessing": False},
            )
        )
        import_id = str(imported["importId"])
        mapped = _data(
            client.put(
                f"/v1/playlist-imports/{import_id}/mapping",
                headers=_write_headers(token, "mapping"),
                json={"mapping": {"title": "title", "artist": "artist", "genre": "genre"}},
            )
        )

        analysis_created = _data(
            client.post(
                f"/v1/playlist-imports/{import_id}/analyses",
                headers=_write_headers(token, "analysis"),
            )
        )
        analysis_task_id = str(analysis_created["taskId"])
        analysis_task = _wait_for_task(client, token, analysis_task_id, "PROFILE_READY")
        revision = str(analysis_task["output"]["profileRevision"])
        profile = _data(client.get(f"/v1/mood-profiles/{revision}", headers=_auth_headers(token)))

        confirmed = _data(
            client.post(
                f"/v1/mood-profiles/{revision}/confirm",
                headers=_write_headers(token, "confirm"),
            )
        )
        pet_created = _data(
            client.post(
                f"/v1/mood-profiles/{revision}/pet-candidates",
                headers=_write_headers(token, "pet"),
            )
        )
        pet_task_id = str(pet_created["taskId"])
        _wait_for_task(client, token, pet_task_id, "CANDIDATES_READY")
        candidate_data = _data(client.get(f"/v1/pet-candidates/{pet_task_id}", headers=_auth_headers(token)))
        candidates = candidate_data["candidates"]
        if len(candidates) != 3:
            raise DemoError(f"期望 3 个候选，实际得到 {len(candidates)} 个")
        selection = _data(
            client.post(
                f"/v1/pet-candidates/{pet_task_id}/select",
                headers=_write_headers(token, "select"),
                json={
                    "candidateRecordId": candidates[0]["candidateRecordId"],
                    "expectedSelectionVersion": 0,
                },
            )
        )
        preview_url = str(candidates[0]["previewUrl"])
        preview = client.get(preview_url, headers=_auth_headers(token))
        preview.raise_for_status()
        if not preview.headers.get("content-type", "").startswith("image/svg+xml"):
            raise DemoError("候选预览不是 SVG")

    session = {
        "baseUrl": BASE_URL,
        "docsUrl": f"{BASE_URL}/docs",
        "capabilityToken": token,
        "ownerRef": capability["ownerRef"],
        "importId": import_id,
        "analysisTaskId": analysis_task_id,
        "profileRevision": revision,
        "petTaskId": pet_task_id,
        "selectionId": selection["selectionId"],
        "previewUrl": f"{BASE_URL}{preview_url}",
        "validation": {
            "health": health["state"],
            "importStatus": imported["status"],
            "normalizedCount": mapped["normalizedCount"],
            "analysisStatus": analysis_task["status"],
            "knownCount": profile["knownCount"],
            "unknownCount": profile["unknownCount"],
            "confirmed": bool(confirmed["confirmedAt"]),
            "petStatus": candidate_data["task"]["status"],
            "candidateCount": len(candidates),
            "selected": selection["candidateRecordId"] == candidates[0]["candidateRecordId"],
            "previewBytes": len(preview.content),
        },
    }
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SESSION_FILE.chmod(0o600)
    return session


def main() -> None:
    try:
        session = run_demo()
    except (DemoError, httpx.HTTPError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"❌ 后端全链路验证失败：{exc}") from exc
    summary = session["validation"]
    print("✅ MeloPet 后端全链路验证通过")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Swagger: {session['docsUrl']}")
    print(f"会话信息: {SESSION_FILE}")


if __name__ == "__main__":
    main()
