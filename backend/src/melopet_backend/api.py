from contextlib import asynccontextmanager
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile

from .auth import CapabilityService, Principal, bearer_token
from .config import Settings, get_settings
from .errors import AppError, app_error_handler, error_envelope, unexpected_error_handler
from .pet import customization_catalog
from .schemas import (
    CorrectionsRequest,
    DesktopPetStateRequest,
    MappingRequest,
    PetCustomizationRequest,
    PetSelectionRequest,
)
from .service import MeloPetService
from .util import new_id
from .validation_page import VALIDATION_HTML


def create_app(settings: Settings | None = None, service: MeloPetService | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.service = service or MeloPetService(app_settings)
        application.state.capabilities = CapabilityService(application.state.service.database, app_settings)
        yield

    application = FastAPI(
        title="MeloPet Backend",
        version="0.1.0",
        description="MeloPet local validation backend with playlist analysis and desktop-pet activation.",
        lifespan=lifespan,
    )
    application.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unexpected_error_handler)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = [{"location": list(error["loc"]), "message": error["msg"], "type": error["type"]} for error in exc.errors()]
        error = AppError(
            "INVALID_REQUEST",
            "请求字段不符合接口约定。",
            422,
            details={"validation": safe_errors},
        )
        return JSONResponse(
            error_envelope(getattr(request.state, "request_id", "unknown"), error),
            status_code=422,
        )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or new_id("req")
        request.state.request_id = request_id[:200]
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_service(request: Request) -> MeloPetService:
        return request.app.state.service

    def get_principal(
        request: Request,
        token: Annotated[str, Depends(bearer_token)],
    ) -> Principal:
        return request.app.state.capabilities.authenticate(token)

    def ok(request: Request, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "requestId": request.state.request_id,
            "status": "ok",
            "errorCode": None,
            "safeMessage": None,
            "data": data,
        }

    @application.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/validation")

    @application.get("/validation", include_in_schema=False)
    def validation_page() -> HTMLResponse:
        if app_settings.app_env == "production":
            raise AppError("NOT_FOUND", "页面不存在。", 404)
        return HTMLResponse(VALIDATION_HTML)

    @application.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        return ok(request, {"service": "melopet-backend", "state": "healthy"})

    @application.get("/readyz")
    def readyz(request: Request, backend: Annotated[MeloPetService, Depends(get_service)]):
        issues = backend.settings.readiness_issues()
        operational = backend.operational_status()
        return ok(
            request,
            {
                "state": "degraded" if issues or not backend.settings.model_enabled else "ready",
                "productionReady": False,
                "ruleOnly": not backend.settings.model_enabled,
                "musicSignalProvider": backend.settings.music_signal_provider,
                "assetCatalogVersion": backend.catalogs.asset_catalog_version,
                "productionAssets": backend.catalogs.production_eligible,
                "issues": issues,
                "realModelAcceptance": "pending" if not backend.settings.model_enabled else "required",
                "frontendImplemented": True,
                "operational": operational,
            },
        )

    @application.post("/v1/capabilities/anonymous", status_code=201)
    def issue_capability(request: Request) -> dict[str, Any]:
        token, owner_id = request.app.state.capabilities.issue()
        return ok(
            request,
            {
                "capabilityToken": token,
                "ownerRef": owner_id,
                "warning": "Token 仅显示一次；丢失后无法恢复。",
            },
        )

    @application.post("/v1/playlist-imports", status_code=201)
    async def create_playlist_import(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "")
        declared_length = request.headers.get("content-length")
        if declared_length:
            try:
                request_bytes = int(declared_length)
            except ValueError as exc:
                raise AppError("INVALID_CONTENT_LENGTH", "Content-Length 无效。") from exc
            if request_bytes > backend.settings.upload_max_bytes + 1024 * 1024:
                raise AppError(
                    "FILE_TOO_LARGE",
                    f"请求超过 {backend.settings.upload_max_bytes} 字节文件限制。",
                    413,
                )
        filename: str | None = None
        upload_content_type: str | None = None
        create_new_copy = False
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            file_value = form.get("file")
            text_value = form.get("text")
            consent = str(form.get("consentExternalProcessing", "false")).casefold() == "true"
            create_new_copy = str(form.get("createNewCopy", "false")).casefold() == "true"
            if isinstance(file_value, UploadFile):
                data = await file_value.read(backend.settings.upload_max_bytes + 1)
                filename = file_value.filename
                upload_content_type = file_value.content_type
                input_kind = "file"
            elif text_value is not None:
                data = str(text_value).encode("utf-8")
                input_kind = "text"
            else:
                raise AppError("FILE_EMPTY", "请上传文件或提供粘贴文本。")
        elif content_type.startswith("application/json"):
            try:
                body = await request.json()
            except ValueError as exc:
                raise AppError("INVALID_REQUEST", "请求不是合法 JSON。") from exc
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                raise AppError("INVALID_REQUEST", "JSON 请求必须包含 text 字符串。")
            data = body["text"].encode("utf-8")
            consent = bool(body.get("consentExternalProcessing", False))
            create_new_copy = bool(body.get("createNewCopy", False))
            input_kind = "text"
        else:
            raise AppError(
                "UNSUPPORTED_MEDIA_TYPE",
                "请使用 multipart/form-data 或 application/json。",
                415,
            )
        result = backend.create_import(
            owner_id=principal.owner_id,
            data=data,
            filename=filename,
            content_type=upload_content_type,
            input_kind=input_kind,
            consent_external_processing=consent,
            idempotency_key=idempotency_key,
            create_new_copy=create_new_copy,
            request_id=request.state.request_id,
        )
        return ok(request, result)

    @application.get("/v1/catalogs/moods")
    def get_mood_catalog(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        del principal
        return ok(
            request,
            {
                "version": backend.catalogs.taxonomy_version,
                "status": "candidate",
                "items": [
                    {
                        "moodId": item["moodId"],
                        "displayName": item["displayName"],
                        "definition": item["definition"],
                        "boundary": item["boundary"],
                    }
                    for item in backend.catalogs.taxonomy
                ],
            },
        )

    @application.get("/v1/catalogs/pet-assets")
    def get_pet_asset_catalog(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        del principal
        return ok(request, customization_catalog(backend.catalogs))

    @application.get("/v1/playlist-imports/{import_id}")
    def get_playlist_import(
        import_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_import(principal.owner_id, import_id))

    @application.get("/v1/consents/{consent_id}")
    def get_consent(
        consent_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_consent(principal.owner_id, consent_id))

    @application.post("/v1/consents/{consent_id}/revoke")
    def revoke_consent(
        consent_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.revoke_consent(
                owner_id=principal.owner_id,
                consent_id=consent_id,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.delete("/v1/playlist-imports/{import_id}", status_code=202)
    def delete_playlist_import(
        import_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.request_import_deletion(
                owner_id=principal.owner_id,
                import_id=import_id,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.get("/v1/deletions/{deletion_id}")
    def get_deletion(
        deletion_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_deletion(principal.owner_id, deletion_id))

    @application.put("/v1/playlist-imports/{import_id}/mapping")
    def save_mapping(
        import_id: str,
        payload: MappingRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.save_mapping(
                owner_id=principal.owner_id,
                import_id=import_id,
                mapping=payload.mapping,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.post("/v1/playlist-imports/{import_id}/analyses", status_code=202)
    def create_analysis(
        import_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.create_analysis_task(
                owner_id=principal.owner_id,
                import_id=import_id,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.get("/v1/tasks/{task_id}")
    def get_task(
        task_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_task(principal.owner_id, task_id))

    @application.post("/v1/tasks/{task_id}/cancel")
    def cancel_task(
        task_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.request_cancel(principal.owner_id, task_id, idempotency_key),
        )

    @application.post("/v1/tasks/{task_id}/retry")
    def retry_task(
        task_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.retry_task(principal.owner_id, task_id, idempotency_key),
        )

    @application.get("/v1/mood-profiles/{revision}")
    def get_profile(
        revision: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.get_profile(principal.owner_id, revision, page=page, page_size=page_size),
        )

    @application.post("/v1/mood-profiles/{revision}/corrections")
    def correct_profile(
        revision: str,
        payload: CorrectionsRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.correct_profile(
                owner_id=principal.owner_id,
                revision=revision,
                request=payload,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.post("/v1/mood-profiles/{revision}/confirm")
    def confirm_profile(
        revision: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.confirm_profile(
                owner_id=principal.owner_id,
                revision=revision,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.post("/v1/mood-profiles/{revision}/pet-candidates", status_code=202)
    def create_pet_candidates(
        revision: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        payload: PetCustomizationRequest | None = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.create_pet_task(
                owner_id=principal.owner_id,
                revision=revision,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
                customization=payload.customization if payload else None,
            ),
        )

    @application.get("/v1/pet-candidates/{task_id}")
    def get_pet_candidates(
        task_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_pet_candidates(principal.owner_id, task_id))

    @application.post("/v1/pet-candidates/{task_id}/select")
    def select_pet_candidate(
        task_id: str,
        payload: PetSelectionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.select_pet_candidate(
                owner_id=principal.owner_id,
                task_id=task_id,
                candidate_record_id=payload.candidate_record_id,
                expected_selection_version=payload.expected_selection_version,
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
            ),
        )

    @application.post("/v1/pet-candidates/{task_id}/activate-desktop")
    def activate_desktop_pet(
        task_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.activate_desktop_pet(
                owner_id=principal.owner_id,
                task_id=task_id,
                request_id=request.state.request_id,
            ),
        )

    @application.post("/v1/pet-candidates/{task_id}/desktop-state")
    def set_desktop_pet_state(
        task_id: str,
        payload: DesktopPetStateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(
            request,
            backend.set_desktop_pet_state(
                owner_id=principal.owner_id,
                task_id=task_id,
                action=payload.action,
                say=payload.say,
                request_id=request.state.request_id,
            ),
        )

    @application.get("/v1/pet-candidates/{task_id}/previews/{candidate_id}")
    def get_pet_preview(
        task_id: str,
        candidate_id: str,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> Response:
        content, media_type = backend.get_preview(principal.owner_id, task_id, candidate_id)
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    @application.get("/v1/pet-candidates/{task_id}/cards/{candidate_id}")
    def get_pet_character_card(
        task_id: str,
        candidate_id: str,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> Response:
        content = backend.get_character_card(principal.owner_id, task_id, candidate_id)
        return Response(
            content,
            media_type="image/svg+xml",
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    @application.get("/v1/pet-candidates/{task_id}/actions/{candidate_id}")
    def get_pet_actions(
        task_id: str,
        candidate_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> dict[str, Any]:
        return ok(request, backend.get_pet_actions(principal.owner_id, task_id, candidate_id))

    @application.get("/v1/pet-candidates/{task_id}/actions/{candidate_id}/{action}")
    def get_pet_action_preview(
        task_id: str,
        candidate_id: str,
        action: str,
        principal: Annotated[Principal, Depends(get_principal)],
        backend: Annotated[MeloPetService, Depends(get_service)],
    ) -> Response:
        content, media_type = backend.get_pet_action_preview(principal.owner_id, task_id, candidate_id, action)
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    return application


app = create_app()


def run() -> None:
    uvicorn.run("melopet_backend.api:app", host="127.0.0.1", port=8010, reload=False)


if __name__ == "__main__":
    run()
