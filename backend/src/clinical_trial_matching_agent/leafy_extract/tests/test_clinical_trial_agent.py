import unittest

from agent_tools.clinical_trial_agent import (
    ClinicalTrialAgentError,
    get_clinical_trial_agent_status,
    invoke_clinical_trial_agent,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRequests:
    def __init__(self, *, get_response=None, post_response=None, get_error=None, post_error=None):
        self.get_response = get_response
        self.post_response = post_response
        self.get_error = get_error
        self.post_error = post_error
        self.get_calls = []
        self.post_calls = []

    def get(self, url, timeout):
        self.get_calls.append({"url": url, "timeout": timeout})
        if self.get_error is not None:
            raise self.get_error
        return self.get_response

    def post(self, url, json, timeout):
        self.post_calls.append({"url": url, "json": json, "timeout": timeout})
        if self.post_error is not None:
            raise self.post_error
        return self.post_response


class ClinicalTrialAgentIntegrationTests(unittest.TestCase):
    def test_health_status_uses_internal_mode_without_network_probe(self):
        requests_module = FakeRequests()

        status = get_clinical_trial_agent_status(
            mode="internal",
            base_url="",
            timeout_sec=5,
            requests_module=requests_module,
        )

        self.assertEqual(status["state"], "internal")
        self.assertEqual(status["availability"], "internal_mode")
        self.assertEqual(requests_module.get_calls, [])

    def test_health_status_reports_available_when_external_agent_is_ready(self):
        requests_module = FakeRequests(
            get_response=FakeResponse(
                200,
                {
                    "ok": True,
                    "featureReadiness": {"mongo": True, "pharmaTrials": True},
                },
            )
        )
        cache = {}

        status = get_clinical_trial_agent_status(
            mode="auto",
            base_url="http://localhost:8014",
            timeout_sec=7,
            requests_module=requests_module,
            cache=cache,
            cache_ttl_sec=30,
        )

        self.assertEqual(
            requests_module.get_calls[0]["url"],
            "http://localhost:8014/api/v1/agents/clinical-trial-matching/health",
        )
        self.assertEqual(status["availability"], "available")
        self.assertEqual(status["state"], "live")
        self.assertTrue(status["featureReadiness"]["mongo"])
        self.assertEqual(cache["status"]["availability"], "available")

    def test_invoke_normalizes_external_agent_payload(self):
        requests_module = FakeRequests(
            post_response=FakeResponse(
                200,
                {
                    "success": True,
                    "matches": [{"trialId": "NCT-100"}],
                    "patientEvidencePack": {"patientSummary": "summary"},
                    "pipelineVisibility": {"matcher": "shared_trial_matcher"},
                    "patientContextStatus": {"ready": True},
                    "patientLink": {"patientId": "patient-1"},
                    "syntheaAnalogs": [{"patientRef": "Patient/alpha"}],
                    "agentInvocation": {"mode": "external", "traceId": "trace-1"},
                },
            )
        )

        result = invoke_clinical_trial_agent(
            question="Which trials fit this patient?",
            patient_context={"patientId": "patient-1"},
            top_k=4,
            base_url="http://localhost:8014",
            timeout_sec=9,
            requests_module=requests_module,
        )

        self.assertEqual(
            requests_module.post_calls[0]["url"],
            "http://localhost:8014/api/v1/agents/clinical-trial-matching/invoke",
        )
        self.assertEqual(requests_module.post_calls[0]["json"]["topK"], 4)
        self.assertEqual(result["matches"][0]["trialId"], "NCT-100")
        self.assertEqual(result["patientEvidencePack"]["patientSummary"], "summary")
        self.assertEqual(result["pipelineVisibility"]["matcher"], "shared_trial_matcher")
        self.assertEqual(result["pipelineVisibility"]["externalAgent"]["agentId"], "clinical-trial-matching-agent")
        self.assertEqual(result["agentStatus"]["availability"], "available")
        self.assertEqual(result["agentInvocation"]["traceId"], "trace-1")

    def test_invoke_raises_structured_error_for_failed_response(self):
        requests_module = FakeRequests(
            post_response=FakeResponse(
                503,
                {"error": "Clinical trial service timed out."},
            )
        )

        with self.assertRaises(ClinicalTrialAgentError) as raised:
            invoke_clinical_trial_agent(
                question="Which trials fit this patient?",
                patient_context={"patientId": "patient-1"},
                top_k=4,
                base_url="http://localhost:8014",
                timeout_sec=9,
                requests_module=requests_module,
            )

        self.assertEqual(raised.exception.agent_status["availability"], "unavailable")
        self.assertEqual(raised.exception.agent_status["errorCategory"], "timeout")


if __name__ == "__main__":
    unittest.main()
