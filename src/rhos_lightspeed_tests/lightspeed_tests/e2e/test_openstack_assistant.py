"""End-to-end test for Goose ACP in the OpenStack Assistant pod."""

import pytest

from rhos_lightspeed_tests.lib.acp import ACPClient, PodExecTransport


class TestOpenStackAssistant:
    @pytest.mark.xfail(reason="Waiting for OSPRH-40495 to be resolved", strict=True)
    @pytest.mark.openstack_assistant
    def test_goose_query(self, config):
        """Goose ACP answers an OpenStack question successfully from its pod."""
        assistant_pod_config = config["openstack_assistant_pod"]
        openshift_config = config["openshift"]

        with PodExecTransport(
            pod_name=assistant_pod_config["name"],
            namespace=assistant_pod_config["namespace"],
            command=["goose", "acp"],
            config=config["openshift"],
            timeout=openshift_config["timeout"],
        ) as transport:
            acp_client = ACPClient(transport)
            acp_client.initialize()
            acp_client.new_session()

            # NOTE(lpiwowar): We are using an explicit request to use the file_search tool
            # to increase the chances that we test a path involving a file_search tool call.
            # Without the explicit request, the LLM sometimes does not trigger the tool.
            prompt = "What is nova in OpenStack? Use the file_search tool if available."
            response = acp_client.prompt(prompt)

        assert response.strip(), "Goose returned an empty response"
