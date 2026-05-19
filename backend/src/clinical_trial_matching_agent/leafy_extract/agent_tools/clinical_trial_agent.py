from __future__ import annotations

import time as time_module


CLINICAL_TRIAL_AGENT_HEALTH_PATH = "/api/v1/agents/clinical-trial-matching/health"
CLINICAL_TRIAL_AGENT_INVOKE_PATH = "/api/v1/agents/clinical-trial-matching/invoke"


class ClinicalTrialAgentError(RuntimeError):
    def __init__(self, message, *, agent_status=None):
        super().__init__(message)
        self.agent_status = agent_status if isinstance(agent_status, dict) else {}


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def _join_url(base_url, path):
    normalized_base = str(base_url or "").strip().rstrip("/")
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    if not normalized_base:
        return normalized_path
    return f"{normalized_base}{normalized_path}"


def build_clinical_trial_agent_status(
    *,
    state,
    availability,
    severity,
    title,
    message,
    mode=None,
    live_agent_used=False,
    fallback_used=False,
    error_category=None,
    error_detail=None,
    agent_endpoint=None,
):
    payload = {
        "state": str(state or "").strip() or "error",
        "availability": str(availability or "").strip() or "error",
        "severity": str(severity or "").strip() or "error",
        "title": str(title or "").strip() or "Clinical trial matching agent status",
        "message": str(message or "").strip(),
        "mode": str(mode or "").strip() or None,
        "liveAgentUsed": bool(live_agent_used),
        "fallbackUsed": bool(fallback_used),
    }
    if error_category:
        payload["errorCategory"] = str(error_category).strip()
    if error_detail:
        payload["errorDetail"] = str(error_detail).strip()
    if agent_endpoint:
        payload["agentEndpoint"] = str(agent_endpoint).strip()
    return payload


def _error_category_from_message(message):
    normalized = str(message or "").strip().lower()
    if "timed out" in normalized or "timeout" in normalized:
        return "timeout"
    if "not configured" in normalized or "missing" in normalized:
        return "configuration"
    if "connection refused" in normalized or "name or service not known" in normalized:
        return "availability"
    if "404" in normalized:
        return "not_found"
    return "unexpected"


def get_clinical_trial_agent_status(
    *,
    mode,
    base_url,
    timeout_sec,
    requests_module,
    cache=None,
    cache_ttl_sec=15,
    force_refresh=False,
):
    normalized_mode = str(mode or "auto").strip().lower() or "auto"
    normalized_url = str(base_url or "").strip().rstrip("/")
    cache_state = cache if isinstance(cache, dict) else None
    now_ts = time_module.time()

    if normalized_mode == "internal":
        return build_clinical_trial_agent_status(
            state="internal",
            availability="internal_mode",
            severity="info",
            title="Embedded trial matcher enabled",
            message="Leafy is configured to use the embedded clinical-trial matcher.",
            mode=normalized_mode,
            live_agent_used=False,
            fallback_used=False,
        )

    if (
        not force_refresh
        and cache_state is not None
        and isinstance(cache_state.get("status"), dict)
        and (now_ts - float(cache_state.get("checkedAt") or 0.0)) < max(1, int(cache_ttl_sec or 15))
    ):
        return cache_state["status"]

    if not normalized_url:
        status = build_clinical_trial_agent_status(
            state="error",
            availability="not_configured",
            severity="error",
            title="External clinical-trial agent is not configured",
            message="CLINICAL_TRIAL_AGENT_URL is not configured in the backend environment.",
            mode=normalized_mode,
            live_agent_used=False,
            fallback_used=False,
            error_category="configuration",
        )
        if cache_state is not None:
            cache_state["status"] = status
            cache_state["checkedAt"] = now_ts
        return status

    health_url = _join_url(normalized_url, CLINICAL_TRIAL_AGENT_HEALTH_PATH)
    try:
        response = requests_module.get(health_url, timeout=max(1, int(timeout_sec or 5)))
        payload = _safe_json(response)
        if int(getattr(response, "status_code", 500) or 500) >= 400:
            message = str(payload.get("detail") or payload.get("error") or f"Health probe returned HTTP {response.status_code}.").strip()
            status = build_clinical_trial_agent_status(
                state="error",
                availability="unavailable",
                severity="error",
                title="External clinical-trial agent is unavailable",
                message=message,
                mode=normalized_mode,
                live_agent_used=False,
                fallback_used=False,
                error_category=_error_category_from_message(message),
                error_detail=message,
                agent_endpoint=normalized_url,
            )
        elif not bool(payload.get("ok")):
            message = str(
                ((payload.get("agentStatus") or {}).get("message"))
                or payload.get("message")
                or "Health probe reported the service as not ready."
            ).strip()
            status = build_clinical_trial_agent_status(
                state="error",
                availability="unavailable",
                severity="error",
                title="External clinical-trial agent is unavailable",
                message=message,
                mode=normalized_mode,
                live_agent_used=False,
                fallback_used=False,
                error_category="dependency",
                error_detail=message,
                agent_endpoint=normalized_url,
            )
        else:
            status = build_clinical_trial_agent_status(
                state="live",
                availability="available",
                severity="info",
                title="External clinical-trial agent available",
                message="Leafy can route trial-matching prompts to the standalone clinical-trial agent.",
                mode=normalized_mode,
                live_agent_used=True,
                fallback_used=False,
                agent_endpoint=normalized_url,
            )
            if isinstance(payload.get("featureReadiness"), dict):
                status["featureReadiness"] = payload.get("featureReadiness") or {}
    except Exception as error:
        message = str(error).strip() or "Clinical trial agent health probe failed."
        status = build_clinical_trial_agent_status(
            state="error",
            availability="unavailable",
            severity="error",
            title="External clinical-trial agent is unavailable",
            message="Leafy could not reach the standalone clinical-trial matching agent.",
            mode=normalized_mode,
            live_agent_used=False,
            fallback_used=False,
            error_category=_error_category_from_message(message),
            error_detail=message,
            agent_endpoint=normalized_url,
        )

    if cache_state is not None:
        cache_state["status"] = status
        cache_state["checkedAt"] = now_ts
    return status


def invoke_clinical_trial_agent(
    *,
    question,
    patient_context,
    top_k,
    base_url,
    timeout_sec,
    requests_module,
):
    normalized_url = str(base_url or "").strip().rstrip("/")
    if not normalized_url:
        status = build_clinical_trial_agent_status(
            state="error",
            availability="not_configured",
            severity="error",
            title="External clinical-trial agent is not configured",
            message="CLINICAL_TRIAL_AGENT_URL is not configured in the backend environment.",
            mode="external",
            live_agent_used=False,
            fallback_used=False,
            error_category="configuration",
        )
        raise ClinicalTrialAgentError(status["message"], agent_status=status)

    invoke_url = _join_url(normalized_url, CLINICAL_TRIAL_AGENT_INVOKE_PATH)
    payload = {
        "question": str(question or "").strip(),
        "patientContext": patient_context if isinstance(patient_context, dict) else {},
        "topK": max(1, min(int(top_k or 5), 20)),
    }

    try:
        response = requests_module.post(invoke_url, json=payload, timeout=max(1, int(timeout_sec or 30)))
        response_payload = _safe_json(response)
        if int(getattr(response, "status_code", 500) or 500) >= 400:
            message = str(
                response_payload.get("detail")
                or response_payload.get("error")
                or f"Clinical trial agent returned HTTP {response.status_code}."
            ).strip()
            status = build_clinical_trial_agent_status(
                state="error",
                availability="unavailable",
                severity="error",
                title="Clinical trial agent request failed",
                message="Leafy could not complete the external clinical-trial matching request.",
                mode="external",
                live_agent_used=False,
                fallback_used=False,
                error_category=_error_category_from_message(message),
                error_detail=message,
                agent_endpoint=normalized_url,
            )
            raise ClinicalTrialAgentError(message, agent_status=status)
        if not bool(response_payload.get("success")):
            message = str(response_payload.get("error") or "Clinical trial agent returned an unsuccessful response.").strip()
            status = build_clinical_trial_agent_status(
                state="error",
                availability="unavailable",
                severity="error",
                title="Clinical trial agent request failed",
                message="Leafy could not complete the external clinical-trial matching request.",
                mode="external",
                live_agent_used=False,
                fallback_used=False,
                error_category=_error_category_from_message(message),
                error_detail=message,
                agent_endpoint=normalized_url,
            )
            raise ClinicalTrialAgentError(message, agent_status=status)
    except ClinicalTrialAgentError:
        raise
    except Exception as error:
        message = str(error).strip() or "Clinical trial agent request failed."
        status = build_clinical_trial_agent_status(
            state="error",
            availability="unavailable",
            severity="error",
            title="Clinical trial agent request failed",
            message="Leafy could not reach the external clinical-trial matching agent.",
            mode="external",
            live_agent_used=False,
            fallback_used=False,
            error_category=_error_category_from_message(message),
            error_detail=message,
            agent_endpoint=normalized_url,
        )
        raise ClinicalTrialAgentError(message, agent_status=status) from error

    agent_status = response_payload.get("agentStatus") or build_clinical_trial_agent_status(
        state="live",
        availability="available",
        severity="info",
        title="External clinical-trial agent response",
        message="The standalone clinical-trial matching agent returned a response to Leafy.",
        mode="external",
        live_agent_used=True,
        fallback_used=False,
        agent_endpoint=normalized_url,
    )
    pipeline_visibility = dict(response_payload.get("pipelineVisibility") or {})
    pipeline_visibility.setdefault(
        "externalAgent",
        {
            "agentId": "clinical-trial-matching-agent",
            "status": agent_status,
            "baseUrl": normalized_url,
        },
    )
    return {
        "matches": response_payload.get("matches") or [],
        "patientEvidencePack": response_payload.get("patientEvidencePack") or {},
        "pipelineVisibility": pipeline_visibility,
        "patientContextStatus": response_payload.get("patientContextStatus") or {},
        "patientLink": response_payload.get("patientLink") or {},
        "syntheaAnalogs": response_payload.get("syntheaAnalogs") or [],
        "agentStatus": agent_status,
        "agentInvocation": response_payload.get("agentInvocation") or {},
    }
