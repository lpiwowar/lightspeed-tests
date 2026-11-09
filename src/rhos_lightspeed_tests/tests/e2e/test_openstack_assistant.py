"""End-to-end test for Goose ACP in the OpenStack Assistant pod."""

import pytest

from rhos_lightspeed_tests.acp import ACPClient, PodExecTransport


class TestOpenStackAssistant:
    @pytest.mark.xfail(reason="Waiting for fix for the bug number OSPRH-40495")
    @pytest.mark.openstack_assistant
    def test_goose_query(self, config):
        """Goose ACP answers an OpenStack question successfully from its pod."""
        assistant_pod_config = config["openstack_assistant_pod"]
        openshift_config = config["openshift"]

        with PodExecTransport(
            pod_name=assistant_pod_config["name"],
            namespace=assistant_pod_config["namespace"],
            command=["goose", "acp"],
            openshift_config=config["openshift"],
            timeout=openshift_config["timeout"],
        ) as transport:
            acp_client = ACPClient()
            acp_client.initialize(transport)
            acp_client.new_session(transport)

            # NOTE(lpiwowar): We are using an explicit request to use the file_search tool
            # to increase the chances that we test a path involving a file_search tool call.
            # Without the explicit request, the LLM sometimes does not trigger the tool.
            prompt = "What is nova in OpenStack? Use the file_search tool if available."
            response = acp_client.prompt(transport, prompt)

        assert response.strip(), "Goose returned an empty response"
